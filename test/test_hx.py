import RPi.GPIO as GPIO
from hx711 import HX711

DATA_PIN = 17   # P11/Users/kunieda/Downloads/IMG_3797.HEIC
CLOCK_PIN = 22  # P15

hx = HX711(dout_pin=DATA_PIN, pd_sck_pin=CLOCK_PIN)
hx.reset()

# 風袋引き（ゼロ点設定）
hx.zero()

# 生の値を取得
raw_value = hx.get_raw_data_mean(readings=10)
print(f"Raw: {raw_value}")