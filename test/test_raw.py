import RPi.GPIO as GPIO
import time

DATA_PIN = 17
CLOCK_PIN = 22

GPIO.setmode(GPIO.BCM)
GPIO.setup(DATA_PIN, GPIO.IN)
GPIO.setup(CLOCK_PIN, GPIO.OUT)
GPIO.output(CLOCK_PIN, GPIO.LOW)

def read_hx711():
    # データ準備完了まで待つ（DTがLOWになるまで）
    timeout = time.time() + 1.0
    while GPIO.input(DATA_PIN) == 1:
        if time.time() > timeout:
            print("タイムアウト: HX711からデータが来ない")
            return None

    # 24ビット読み取り（sleepなし: time.sleepは実際1ms以上かかりHX711がパワーダウンしてしまう）
    value = 0
    for _ in range(24):
        GPIO.output(CLOCK_PIN, GPIO.HIGH)
        bit = GPIO.input(DATA_PIN)
        GPIO.output(CLOCK_PIN, GPIO.LOW)
        value = (value << 1) | bit

    # チャンネルA ゲイン128のための25パルス目
    GPIO.output(CLOCK_PIN, GPIO.HIGH)
    GPIO.output(CLOCK_PIN, GPIO.LOW)

    # 符号付き24bit変換
    if value & 0x800000:
        value -= 0x1000000

    return value

print("HX711直接読み取りテスト")
for i in range(50):
    val = read_hx711()
    if val is not None:
        print(f"{i+1}: {val}")
    time.sleep(0.5)

GPIO.cleanup()
