"""HX711 安定読み取り (lgpio 版)

RPi.GPIO 版 (test_stable.py) のビットバンギングが OS 割り込みで
データ化けする問題に対処したバージョン。

改善点:
- lgpio (カーネル空間, ~1-5μs/op) でジッタ削減
- 各 24bit 読み取りの所要時間を計測 → 長すぎたら破棄
  (SCK HIGH が 50μs 超で HX711 が power-down してデータが汚れるため)
- リアルタイムスケジューリング (SCHED_FIFO) で割り込み耐性を上げる
"""

import gc
import os
import time

import lgpio

DATA_PIN = 17    # HX711 DOUT
CLOCK_PIN = 22   # HX711 PD_SCK

# 25=ChA Gain128 (±20mV), 26=ChA Gain64 (±40mV), 27=ChB Gain32 (±80mV)
NUM_PULSES = 25

# HX711 飽和判定 (24bit signed)
SAT_POS = 0x7FFFFE
SAT_NEG = -0x7FFFFE

# 26パルス全体でこれ以上かかったらビット化け疑い (lgpio なら通常 < 500μs)
READ_TIMEOUT_S = 0.0015


def try_realtime_priority():
    """SCHED_FIFO に上げて割り込み耐性を稼ぐ (sudo 必要)。失敗しても続行。"""
    try:
        param = os.sched_param(20)
        os.sched_setscheduler(0, os.SCHED_FIFO, param)
        return True
    except (PermissionError, OSError):
        return False


class HX711:
    def __init__(self, dout_pin, sck_pin):
        self.dout = dout_pin
        self.sck = sck_pin
        self.h = lgpio.gpiochip_open(0)
        lgpio.gpio_claim_input(self.h, self.dout)
        lgpio.gpio_claim_output(self.h, self.sck, 0)
        self.offset = 0
        self.reset()

    def close(self):
        try:
            lgpio.gpio_write(self.h, self.sck, 0)
            lgpio.gpiochip_close(self.h)
        except Exception:
            pass

    def reset(self):
        """Power down → Power up シーケンス。Channel A / Gain 128 にリセット。"""
        lgpio.gpio_write(self.h, self.sck, 0)
        time.sleep(0.0001)
        lgpio.gpio_write(self.h, self.sck, 1)  # >60us HIGH で power down
        time.sleep(0.0002)
        lgpio.gpio_write(self.h, self.sck, 0)  # LOW で wake up
        time.sleep(0.0002)

    def _wait_ready(self, timeout_sec=1.0):
        deadline = time.monotonic() + timeout_sec
        while lgpio.gpio_read(self.h, self.dout):
            if time.monotonic() > deadline:
                return False
            time.sleep(0.0005)
        return True

    def _read_once(self):
        """1回読み取り。返り値 (value, elapsed_sec) または (None, 0)。"""
        if not self._wait_ready():
            return None, 0.0

        gc_was_enabled = gc.isenabled()
        if gc_was_enabled:
            gc.disable()

        # ローカル束縛で属性参照のオーバーヘッドを削る
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

            # 追加パルス: 次回読み取りのゲイン/チャネル設定
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

    def read(self, retries=5, verbose=False):
        """飽和/タイムアウト/遅延を検出して自動リトライ。"""
        for attempt in range(retries):
            v, elapsed = self._read_once()
            if v is None:
                if verbose:
                    print(f"    [retry {attempt+1}/{retries}] DOUT待ちタイムアウト → reset")
                self.reset()
                continue
            if elapsed > READ_TIMEOUT_S:
                if verbose:
                    print(f"    [retry {attempt+1}/{retries}] 遅延 {elapsed*1e3:.2f}ms → 破棄")
                self.reset()
                time.sleep(0.05)
                continue
            if v >= SAT_POS or v <= SAT_NEG:
                if verbose:
                    print(f"    [retry {attempt+1}/{retries}] 飽和値 {v} → reset")
                self.reset()
                time.sleep(0.05)
                continue
            return v
        return None

    def read_median(self, n=7, verbose=False):
        samples = []
        misses = 0
        max_misses = n
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
        v = self.read_median(n, verbose=verbose)
        if v is None:
            raise RuntimeError("tare failed: HX711 から有効値が得られない")
        self.offset = v
        return self.offset


def main():
    print("HX711 安定読み取りテスト (lgpio版)")
    print(f"  DOUT = GPIO{DATA_PIN}, SCK = GPIO{CLOCK_PIN}")

    if try_realtime_priority():
        print("  [info] SCHED_FIFO 取得成功 (割り込み耐性UP)")
    else:
        print("  [info] SCHED_FIFO 取得失敗 (sudo なしでも動作はします)")

    hx = HX711(DATA_PIN, CLOCK_PIN)

    try:
        print("ウォームアップ中... (3秒, ロードセル温度安定のため)")
        time.sleep(3)

        # 生読みテスト
        print("生読みテスト (verbose)...")
        v = hx.read(retries=3, verbose=True)
        print(f"  → {v}")
        if v is None:
            print("HX711 から有効値が一切取れません。")
            print("  対処: HX711 VCC を抜き差し、配線確認 (DOUT=GPIO17, SCK=GPIO22)")
            return

        # 安定するまで数発捨てる
        print("初期安定化 (5発読み捨て)...")
        for _ in range(5):
            hx.read()

        print("風袋引き (荷物を載せないでください)...")
        try:
            offset = hx.tare(n=15, verbose=True)
            print(f"  offset = {offset}")
        except RuntimeError as e:
            print(f"  ERROR: {e}")
            return

        print("計測開始 - Ctrl+C で終了")
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
        hx.close()


if __name__ == "__main__":
    main()