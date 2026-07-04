"""HX711 安定読み取り (lgpio 版, Pi Zero 初代向け v2)

test_stable_lgpio_pizero.py で 0xFFFFFF / 0x7FFFFF (DOUT stuck HIGH) が
頻発する問題への対策版。

原因: 元コードは SCK HIGH 中に gpio_read を挟むため、SCK HIGH 実測 ≒ 80μs
になり HX711 の power-down 閾値 60μs を超える。目覚め直後は DOUT が HIGH に
張り付き、value = -1 (0xFFFFFF) や 8388607 (0x7FFFFF) が返る。

対策: SCK を HIGH→即 LOW と最短で叩き、DOUT の読み取りを LOW 期間中に行う。
HX711 は次の rising edge まで DOUT を保持するので、LOW 中に読んでも同じ
ビットが得られる。hx711py など C 系ライブラリと同じ方式。
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

# Pi Zero (初代) 用: lgpio 1 コール ~40μs、SCK LOW 期間で DOUT 読むと 24bit
# 全体で ~3-5ms。個々の SCK HIGH は ~40μs で power-down 閾値の下。
READ_TIMEOUT_S = 0.008


def try_realtime_priority():
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
        # ADC が新規変換を終えるまで待つ (10Hz モードで ~100ms)
        time.sleep(0.4)

    def _wait_ready(self, timeout_sec=1.0):
        deadline = time.monotonic() + timeout_sec
        while lgpio.gpio_read(self.h, self.dout):
            if time.monotonic() > deadline:
                return False
            time.sleep(0.0005)
        return True

    def _read_once(self):
        """1回読み取り。返り値 (value, elapsed_sec) または (None, 0)。

        SCK HIGH を最短化するため、SCK を HIGH→即 LOW と叩き、
        DOUT は SCK LOW 中に読む。
        """
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
                gpio_write(h, sck, 1)   # SCK HIGH (最短)
                gpio_write(h, sck, 0)   # SCK LOW (即座に落とす)
                bit = gpio_read(h, dout)  # LOW 中に DOUT を読む
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
            if v == -1 or v == 0xFFFFFF - 0x1000000:
                # DOUT stuck HIGH の典型症状
                if verbose:
                    print(f"    [retry {attempt+1}/{retries}] DOUT張り付き (v=-1) → reset")
                self.reset()
                time.sleep(0.05)
                continue
            if v >= SAT_POS or v <= SAT_NEG:
                if verbose:
                    print(f"    [retry {attempt+1}/{retries}] 飽和値 {v} → reset")
                self.reset()
                time.sleep(0.05)
                continue
            if verbose:
                print(f"    [ok] {elapsed*1e3:.2f}ms  value={v}")
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
    print("HX711 安定読み取りテスト (lgpio版, Pi Zero 初代向け v2)")
    print(f"  DOUT = GPIO{DATA_PIN}, SCK = GPIO{CLOCK_PIN}")
    print(f"  READ_TIMEOUT = {READ_TIMEOUT_S*1e3:.1f}ms")
    print(f"  タイミング: SCK HIGH→LOW→read (LOW 中に DOUT を読む)")

    if try_realtime_priority():
        print("  [info] SCHED_FIFO 取得成功 (割り込み耐性UP)")
    else:
        print("  [info] SCHED_FIFO 取得失敗 (sudo なしでも動作はします)")

    hx = HX711(DATA_PIN, CLOCK_PIN)

    try:
        print("ウォームアップ中... (3秒, ロードセル温度安定のため)")
        time.sleep(3)

        print("生読みテスト (verbose)...")
        v = hx.read(retries=5, verbose=True)
        print(f"  → {v}")
        if v is None:
            print("HX711 から有効値が一切取れません。")
            print("  対処: 配線 (DOUT=GPIO17, SCK=GPIO22, VCC=3.3V) を再確認")
            return

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