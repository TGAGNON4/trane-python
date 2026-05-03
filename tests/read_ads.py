import time
import board, busio
import adafruit_ads1x15.ads1115 as ADS
from adafruit_ads1x15.analog_in import AnalogIn

i2c = busio.I2C(board.SCL, board.SDA)
ads = ADS.ADS1115(i2c)
ads.gain = 1  # +/-4.096V range

sensor0 = AnalogIn(ads, 0)  # A0
sensor1 = AnalogIn(ads, 1)  # A1

while True:
    v0 = sensor0.voltage
    v1 = sensor1.voltage
    print(f"A0: {v0:.4f} V   A1: {v1:.4f} V")
    time.sleep(0.25)
