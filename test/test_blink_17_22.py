"""GPIO17 (DATA) と GPIO22 (CLK) を 1秒間隔で同時点滅させる診断スクリプト。
LED やテスターで両ピンが 3.3V ⇄ 0V を交互に出しているか確認する。
"""

import time
import RPi.GPIO as GPIO

DATA_PIN = 17   # 物理ピン 11
CLOCK_PIN = 22  # 物理ピン 15

GPIO.setmode(GPIO.BCM)
GPIO.setup(DATA_PIN, GPIO.OUT, initial=GPIO.LOW)
GPIO.setup(CLOCK_PIN, GPIO.OUT, initial=GPIO.LOW)

print(f"GPIO{DATA_PIN} (物理ピン 11) と GPIO{CLOCK_PIN} (物理ピン 15) を 1秒間隔で HIGH/LOW します")
print("Ctrl+C で終了")
print()

try:
    state = False
    while True:
        state = not state
        level = GPIO.HIGH if state else GPIO.LOW
        GPIO.output(DATA_PIN, level)
        GPIO.output(CLOCK_PIN, level)
        print(f"  GPIO{DATA_PIN}, GPIO{CLOCK_PIN} = {'HIGH (3.3V)' if state else 'LOW (0V)'}")
        time.sleep(1.0)
except KeyboardInterrupt:
    print("\n終了")
finally:
    GPIO.output(DATA_PIN, GPIO.LOW)
    GPIO.output(CLOCK_PIN, GPIO.LOW)
    GPIO.cleanup()
