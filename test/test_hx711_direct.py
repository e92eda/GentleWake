import time
import RPi.GPIO as GPIO

DATA_PIN = 17
CLOCK_PIN = 22

# HX711 timing: PD_SCK high time and low time should be safely below 50us.
# Python is slow enough, but explicit sleeps make the waveform easier to see on the oscilloscope.
PULSE_DELAY = 0.000005  # 5 us
READ_INTERVAL = 0.5


def setup_gpio():
    GPIO.setmode(GPIO.BCM)
    # HX711 DOUT is driven by the HX711 itself. Do not force pull-up/down here.
    GPIO.setup(DATA_PIN, GPIO.IN, pull_up_down=GPIO.PUD_OFF)
    GPIO.setup(CLOCK_PIN, GPIO.OUT, initial=GPIO.LOW)


def wait_ready(timeout_sec=1.0):
    deadline = time.time() + timeout_sec
    while GPIO.input(DATA_PIN) == GPIO.HIGH:
        if time.time() > deadline:
            return False
        time.sleep(0.001)
    return True


def pulse_clock():
    GPIO.output(CLOCK_PIN, GPIO.HIGH)
    time.sleep(PULSE_DELAY)
    GPIO.output(CLOCK_PIN, GPIO.LOW)
    time.sleep(PULSE_DELAY)


def read_hx711_raw():
    """Read 24-bit HX711 data, then send the 25th pulse for channel A gain 128."""
    if not wait_ready():
        return None, "timeout"

    value = 0
    bits = []

    for _ in range(24):
        GPIO.output(CLOCK_PIN, GPIO.HIGH)
        time.sleep(PULSE_DELAY)
        value = (value << 1) | GPIO.input(DATA_PIN)
        bits.append(GPIO.input(DATA_PIN))
        GPIO.output(CLOCK_PIN, GPIO.LOW)
        time.sleep(PULSE_DELAY)

    # 25th pulse: select channel A, gain 128 for the next conversion.
    pulse_clock()

    if value & 0x800000:
        value -= 0x1000000

    return value, "".join(str(bit) for bit in bits)


def main():
    setup_gpio()
    print("HX711 direct test - Ctrl+C で終了")
    print(f"CH1: GPIO{CLOCK_PIN} (SCK), CH2: GPIO{DATA_PIN} (DOUT)")
    print("DOUT入力は PUD_OFF、25パルスで Channel A / Gain 128")

    try:
        while True:
            value, info = read_hx711_raw()
            if value is None:
                print("DOUT待ち: タイムアウト")
            else:
                print(f"bits: {info}")
                print(f"value: {value}")
            time.sleep(READ_INTERVAL)
    except KeyboardInterrupt:
        pass
    finally:
        GPIO.output(CLOCK_PIN, GPIO.LOW)
        GPIO.cleanup()


if __name__ == "__main__":
    main()
