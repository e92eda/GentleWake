"""CLK (GPIO22) のみを 1秒ON / 1秒OFF で叩き続けるだけの診断スクリプト。
LED でも、テスターでも、オシロでも観測できる。
"""

import time
import RPi.GPIO as GPIO

CLOCK_PIN = 22  # 物理ピン 15

GPIO.setmode(GPIO.BCM)
GPIO.setup(CLOCK_PIN, GPIO.OUT, initial=GPIO.LOW)

print(f"GPIO{CLOCK_PIN} (物理ピン 15) を 1秒間隔で HIGH/LOW します")
print("Ctrl+C で終了")
print()

try:
    state = False
    while True:
        state = not state
        GPIO.output(CLOCK_PIN, GPIO.HIGH if state else GPIO.LOW)
        print(f"  GPIO{CLOCK_PIN} = {'HIGH (3.3V)' if state else 'LOW (0V)'}")
        time.sleep(1.0)
except KeyboardInterrupt:
    print("\n終了")
finally:
    GPIO.output(CLOCK_PIN, GPIO.LOW)
    GPIO.cleanup()