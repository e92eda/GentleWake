"""HX711 を読み続けて在床状態を判定するモジュール。

- HX711 ドライバは lgpio ベース (Pythonビットバンギングは化けるため)
- SensorMonitor が別スレッドで連続読み取りし、Nサンプル連続デバウンスで in_bed 判定
- lgpio が無い環境 (Mac開発時) ではモックモードで起動 (state は常に None / False)
"""

import gc
import json
import os
import threading
import time

try:
    import lgpio
    LGPIO_AVAILABLE = True
except ImportError:
    LGPIO_AVAILABLE = False

# ---- HX711 設定 ----
DATA_PIN = 17    # HX711 DOUT
CLOCK_PIN = 22   # HX711 PD_SCK

# 25=ChA Gain128, 26=ChA Gain64, 27=ChB Gain32
NUM_PULSES = 25  # Gain 128 (3.3V VCC 動作下で検証済み)

SAT_POS = 0x7FFFFE
SAT_NEG = -0x7FFFFE


def _is_pi_zero_v1():
    """Pi Zero 初代 (BCM2835 単核 ARMv6) かを判定。Pi Zero 2 (BCM2710) は False。"""
    try:
        with open("/proc/device-tree/model") as f:
            model = f.read()
        if "Zero 2" in model:
            return False
        if "Zero" in model:
            return True
    except (FileNotFoundError, OSError):
        pass
    # /proc/device-tree/model が読めない環境ではコア数で推定
    return (os.cpu_count() or 1) < 2


IS_PI_ZERO_V1 = _is_pi_zero_v1()

# 24bit読みがこれより遅かったら OS 割込みでビット化け疑い → 破棄。
# Pi Zero 初代は lgpio 1 コール ~40μs で 24bit 読みに 2-4ms かかるため緩める必要あり。
# Pi Zero 2 は <1ms で済むので厳しく取れる。
READ_TIMEOUT_S = 0.005 if IS_PI_ZERO_V1 else 0.0015


def try_realtime_priority():
    """SCHED_FIFO に上げて割り込み耐性を稼ぐ (sudo 必要)。失敗しても続行。

    Pi Zero 初代 (単核) では有効化すると sensor スレッドが Flask を食い潰し、
    web リクエストが 500 になるためスキップする。
    """
    if IS_PI_ZERO_V1:
        return False
    try:
        param = os.sched_param(20)
        os.sched_setscheduler(0, os.SCHED_FIFO, param)
        return True
    except (PermissionError, OSError, AttributeError):
        return False


class HX711:
    def __init__(self, dout_pin=DATA_PIN, sck_pin=CLOCK_PIN):
        if not LGPIO_AVAILABLE:
            raise RuntimeError("lgpio is not available on this platform")
        self.dout = dout_pin
        self.sck = sck_pin
        self.h = lgpio.gpiochip_open(0)
        lgpio.gpio_claim_input(self.h, self.dout)
        lgpio.gpio_claim_output(self.h, self.sck, 0)
        self.reset()

    def close(self):
        try:
            lgpio.gpio_write(self.h, self.sck, 0)
            lgpio.gpiochip_close(self.h)
        except Exception:
            pass

    def reset(self):
        lgpio.gpio_write(self.h, self.sck, 0)
        time.sleep(0.0001)
        lgpio.gpio_write(self.h, self.sck, 1)
        time.sleep(0.0002)
        lgpio.gpio_write(self.h, self.sck, 0)
        time.sleep(0.0002)

    def _wait_ready(self, timeout_sec=1.0):
        deadline = time.monotonic() + timeout_sec
        while lgpio.gpio_read(self.h, self.dout):
            if time.monotonic() > deadline:
                return False
            time.sleep(0.0005)
        return True

    def _read_once(self):
        if not self._wait_ready():
            return None, 0.0

        gc_was_enabled = gc.isenabled()
        if gc_was_enabled:
            gc.disable()

        h = self.h
        sck = self.sck
        dout = self.dout
        gpio_write = lgpio.gpio_write
        gpio_read = lgpio.gpio_read

        t0 = time.monotonic()
        try:
            value = 0
            for _ in range(24):
                gpio_write(h, sck, 1)
                bit = gpio_read(h, dout)
                gpio_write(h, sck, 0)
                value = (value << 1) | bit
            for _ in range(NUM_PULSES - 24):
                gpio_write(h, sck, 1)
                gpio_write(h, sck, 0)
        finally:
            if gc_was_enabled:
                gc.enable()
        elapsed = time.monotonic() - t0

        if value & 0x800000:
            value -= 0x1000000
        return value, elapsed

    def read(self, retries=5):
        for _ in range(retries):
            v, elapsed = self._read_once()
            if v is None:
                self.reset()
                continue
            if elapsed > READ_TIMEOUT_S:
                self.reset()
                time.sleep(0.05)
                continue
            if v >= SAT_POS or v <= SAT_NEG:
                self.reset()
                time.sleep(0.05)
                continue
            return v
        return None

    def read_median(self, n=5):
        samples = []
        misses = 0
        while len(samples) < n:
            v = self.read()
            if v is None:
                misses += 1
                if misses >= n:
                    return None
                continue
            samples.append(v)
        samples.sort()
        return samples[len(samples) // 2]


# ---- 在床判定モニタ ----

DEFAULT_THRESHOLD = 10000
DEFAULT_CONSECUTIVE = 5
SAMPLE_INTERVAL_S = 0.3


class SensorMonitor:
    """HX711 を別スレッドで連続読み取りし、在床状態を保持する。

    在床判定: tared 値が threshold を N サンプル連続で上回れば in_bed=True、
              下回れば in_bed=False (両エッジでデバウンス)。
    """

    def __init__(self, config_path):
        self.config_path = config_path
        self._lock = threading.Lock()
        self._raw = None
        self._tared = None
        self._in_bed = False
        self._above_count = 0
        self._below_count = 0
        self._hx = None
        self._thread = None
        self._stop_event = threading.Event()
        self._available = LGPIO_AVAILABLE
        # 設定 (config.json から読む)
        self.tare_offset = 0
        self.threshold = DEFAULT_THRESHOLD
        self.consecutive = DEFAULT_CONSECUTIVE
        self.reload_config()

    # ---- 設定読み書き ----

    def reload_config(self):
        try:
            with open(self.config_path) as f:
                cfg = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return
        with self._lock:
            self.tare_offset = int(cfg.get("tare_offset", 0))
            self.threshold = int(cfg.get("in_bed_threshold", DEFAULT_THRESHOLD))
            self.consecutive = int(cfg.get("consecutive_samples", DEFAULT_CONSECUTIVE))

    def _persist_to_config(self, updates: dict):
        """config.json の指定キーだけを上書き保存する。"""
        try:
            with open(self.config_path) as f:
                cfg = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            cfg = {}
        cfg.update(updates)
        with open(self.config_path, "w") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)

    # ---- スレッド制御 ----

    def start(self):
        if not self._available:
            print("[sensor] lgpio 無し → モックモードで起動 (常に out_of_bed)")
            return
        if self._thread and self._thread.is_alive():
            return
        try_realtime_priority()
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2)
        if self._hx:
            self._hx.close()
            self._hx = None

    def _loop(self):
        try:
            self._hx = HX711()
        except Exception as e:
            print(f"[sensor] HX711 初期化失敗: {e}")
            self._available = False
            return
        time.sleep(2)  # ウォームアップ
        # ゲイン切替の過渡を捨てる
        for _ in range(5):
            self._hx.read()

        while not self._stop_event.is_set():
            v = self._hx.read_median(n=5)
            if v is not None:
                self._update_state(v)
            self._stop_event.wait(SAMPLE_INTERVAL_S)

    def _update_state(self, v: int):
        with self._lock:
            self._raw = v
            tared = v - self.tare_offset
            self._tared = tared
            if tared > self.threshold:
                self._above_count += 1
                self._below_count = 0
                if not self._in_bed and self._above_count >= self.consecutive:
                    self._in_bed = True
            else:
                self._below_count += 1
                self._above_count = 0
                if self._in_bed and self._below_count >= self.consecutive:
                    self._in_bed = False

    # ---- 外部API ----

    def get_state(self) -> dict:
        with self._lock:
            return {
                "available": self._available,
                "raw": self._raw,
                "tared": self._tared,
                "in_bed": self._in_bed,
                "tare_offset": self.tare_offset,
                "threshold": self.threshold,
                "consecutive": self.consecutive,
            }

    def is_in_bed(self) -> bool:
        with self._lock:
            return self._in_bed

    def tare_now(self):
        """現在のraw値をtareオフセットとして保存。Noneなら失敗 (まだ読めてない)。"""
        with self._lock:
            if self._raw is None:
                return None
            self.tare_offset = self._raw
            self._above_count = 0
            self._below_count = 0
            self._in_bed = False
            new_offset = self.tare_offset
        self._persist_to_config({"tare_offset": new_offset})
        return new_offset

    def update_settings(self, threshold=None, consecutive=None):
        updates = {}
        with self._lock:
            if threshold is not None:
                self.threshold = int(threshold)
                updates["in_bed_threshold"] = self.threshold
            if consecutive is not None:
                self.consecutive = max(1, int(consecutive))
                updates["consecutive_samples"] = self.consecutive
        if updates:
            self._persist_to_config(updates)