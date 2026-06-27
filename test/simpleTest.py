import RPi.GPIO as GPIO
import time

DATA_PIN = 17  # P11
CLOCK_PIN = 22  # P15

GPIO.setmode(GPIO.BCM)
GPIO.setup(DATA_PIN, GPIO.IN)
GPIO.setup(CLOCK_PIN, GPIO.OUT)

print("HX711診断開始")
print(f"DT (GPIO{DATA_PIN}) の状態: {GPIO.input(DATA_PIN)}")

# HX711はデータ準備完了時にDTピンがLOWになる
for i in range(5):
    state = GPIO.input(DATA_PIN)
    print(f"{i+1}秒: DT = {'LOW (データ準備OK)' if state == 0 else 'HIGH (待機中)'}")
    time.sleep(1)

GPIO.cleanup()
