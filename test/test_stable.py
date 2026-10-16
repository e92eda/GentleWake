"""HX711 安定読み取り (ライブラリ非依存)

- Wheatstone 4枚ロードセル / HX711 / Raspberry Pi 4 用
- 飽和値 (0x7FFFFF / 0x800000) を検出したら自動でリセット＆リトライ
- メディアンフィルタで外れ値除去
"""

import gc
import time

import RPi.GPIO as GPIO

DATA_PIN = 17    # HX711 DOUT
CLOCK_PIN = 22   # HX711 PD_SCK

# 25=ChA Gain128 (±20mV), 26=ChA Gain64 (±40mV), 27=ChB Gain32 (±80mV)
NUM_PULSES = 26

# HX711 飽和判定 (24bit signed)
SAT_POS = 0x7FFFFE
SAT_NEG = -0x7FFFFE


class HX711:
    def __init__(self, dout_pin, sck_pin):
        self.dout = dout_pin
        self.sck = sck_pin
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(self.dout, GPIO.IN, pull_up_down=GPIO.PUD_OFF)
        GPIO.setup(self.sck, GPIO.OUT, initial=GPIO.LOW)
        self.offset = 0
        self.reset()

    def reset(self):
        """Power down → Power up シーケンスでHX711を初期化。
        Channel A / Gain 128 にリセットされる。"""
        GPIO.output(self.sck, GPIO.LOW)
        time.sleep(0.0001)
        GPIO.output(self.sck, GPIO.HIGH)  # >60us HIGH で power down
        time.sleep(0.0002)
        GPIO.output(self.sck, GPIO.LOW)   # LOW で wake up
        time.sleep(0.0002)

    def _wait_ready(self, timeout_sec=1.0):
        deadline = time.time() + timeout_sec
        while GPIO.input(self.dout):
            if time.time() > deadline:
                return False
            time.sleep(0.0005)
        return True

    def _read_once(self):
        """1回読み取り。失敗時 None。"""
        if not self._wait_ready():
            return None

        # GC を一時停止してビットバンク中のスケジューラ割り込みリスクを下げる
        gc_was_enabled = gc.isenabled()
        if gc_was_enabled:
            gc.disable()

        try:
            value = 0
            # SCK を HIGH に保つ時間を最小化したタイトループ
            for _ in range(24):
                GPIO.output(self.sck, GPIO.HIGH)
                bit = GPIO.input(self.dout)
                GPIO.output(self.sck, GPIO.LOW)
                value = (value << 1) | bit

            # 追加パルス: 25=ChA/G128, 26=ChA/G64, 27=ChB/G32
            for _ in range(NUM_PULSES - 24):
                GPIO.output(self.sck, GPIO.HIGH)
                GPIO.output(self.sck, GPIO.LOW)
        finally:
            if gc_was_enabled:
                gc.enable()

        # 符号付き 24bit に変換
        if value & 0x800000:
            value -= 0x1000000
        return value

    def read(self, retries=3, verbose=False):
        """飽和や読み取り失敗を検出して自動リカバリ。"""
        for attempt in range(retries):
            v = self._read_once()
            if v is None:
                if verbose:
                    print(f"    [retry {attempt+1}/{retries}] DOUT待ちタイムアウト → reset")
                self.reset()
                continue
            if v >= SAT_POS or v <= SAT_NEG:
                if verbose:
                    print(f"    [retry {attempt+1}/{retries}] 飽和値 {v} → reset")
                self.reset()
                time.sleep(0.05)
                continue
            return v
        return None  # 全部失敗

    def read_median(self, n=7, verbose=False):
        """n 回読んでメディアンを返す。"""
        samples = []
        misses = 0
        max_misses = n  # 早めに諦める
        while len(samples) < n:
            v = self.read(verbose=verbose)
            if v is None:
                misses += 1
                if verbose:
                    print(f"  miss {misses}/{max_misses}")
                if misses >= max_misses:
                    return None
                continue
            samples.append(v)
            if verbose:
                print(f"  sample {len(samples)}/{n}: {v}")
        samples.sort()
        return samples[len(samples) // 2]

    def tare(self, n=20, verbose=False):
        """無負荷状態を呼んで風袋引きオフセットを記録。"""
        v = self.read_median(n, verbose=verbose)
        if v is None:
            raise RuntimeError("tare failed: HX711 から有効値が得られない")
        self.offset = v
        return self.offset


def main():
    print("HX711 安定読み取りテスト")
    print(f"  DOUT = GPIO{DATA_PIN}, SCK = GPIO{CLOCK_PIN}")

    hx = HX711(DATA_PIN, CLOCK_PIN)

    print("ウォームアップ中... (2秒)")
    time.sleep(2)

    # まず1発生読みして状態確認
    print("生読みテスト (verbose)...")
    v = hx.read(retries=2, verbose=True)
    print(f"  → {v}")
    if v is None:
        print("HX711 から有効値が一切取れません。")
        print("  対処: HX711 モジュールの VCC を 5秒抜いて挿し直してください。")
        print("  それでもダメなら SCK(GPIO22) / DOUT(GPIO17) の配線確認。")
        GPIO.cleanup()
        return

    print("風袋引き (荷物を載せないでください)...")
    try:
        offset = hx.tare(n=10, verbose=True)
        print(f"  offset = {offset}")
    except RuntimeError as e:
        print(f"  ERROR: {e}")
        GPIO.cleanup()
        return

    print("計測開始 - Ctrl+C で終了")
    try:
        while True:
            v = hx.read_median(n=5)
            if v is None:
                print("  読み取り失敗")
            else:
                tared = v - hx.offset
                print(f"  raw={v:>9d}  tared={tared:>+9d}")
            time.sleep(0.3)
    except KeyboardInterrupt:
        print("\n終了")
    finally:
        GPIO.output(CLOCK_PIN, GPIO.LOW)
        GPIO.cleanup()


if __name__ == "__main__":
    main()