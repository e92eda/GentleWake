        import RPi.GPIO as GPIO
import time

DATA_PIN = 17
CLOCK_PIN = 22

GPIO.setmode(GPIO.BCM)
GPIO.setup(DATA_PIN, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
GPIO.setup(CLOCK_PIN, GPIO.OUT)
GPIO.output(CLOCK_PIN, GPIO.LOW)

print("=== CLKなしで DATA を24回読む ===")
bits_no_clk = []
for i in range(24):
    bits_no_clk.append(GPIO.input(DATA_PIN))
print(f"bits: {bits_no_clk}")
print(f"(全部0なら DOUT接続OK、全部1なら未接続/フロート)")

print()
time.sleep(0.1)

print("=== CLKを送りながら DATA を24回読む ===")
bits_with_clk = []
for i in range(24):
    GPIO.output(CLOCK_PIN, GPIO.HIGH)
    bit = GPIO.input(DATA_PIN)
    GPIO.output(CLOCK_PIN, GPIO.LOW)
    bits_with_clk.append(bit)

print(f"bits: {bits_with_clk}")

value = 0
for b in bits_with_clk:
    value = (value << 1) | b
if value & 0x800000:
    value -= 0x1000000
print(f"value: {value}")

GPIO.cleanup()