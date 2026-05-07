import time
import board
import busio

import adafruit_ads1x15.ads1115 as ADS
from adafruit_ads1x15.analog_in import AnalogIn

import adafruit_mlx90614

MLX_ADDRS = [0x1A, 0x5A, 0x3A, 0x4A]   # your 4 MLX90614s
ADS_ADDR  = 0x48                       # ADS1115 address

def c_to_f(c: float) -> float:
    return c * 9.0/5.0 + 32.0

def main():
    # Shared I2C bus for all devices
    i2c = busio.I2C(board.SCL, board.SDA)

    # ADS1115 setup
    ads = ADS.ADS1115(i2c, address=ADS_ADDR)
    ads.gain = 1
    a1 = AnalogIn(ads, 0)  # A1
    a2 = AnalogIn(ads, 1)  # A2
    a3 = AnalogIn(ads, 2) # A3
    a4 = AnalogIn(ads, 3) #A4
    # MLX90614 setup (one object per address)
    mlxs = [adafruit_mlx90614.MLX90614(i2c, address=a) for a in MLX_ADDRS]

    print("Combined I2C read: 4x MLX90614 + ADS1115 (A1/A2)")
    print("MLX:", ", ".join(f"0x{a:02X}" for a in MLX_ADDRS), " | ADS1115:", hex(ADS_ADDR))
    print("Press Ctrl+C to stop.\n")

    while True:
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        print(ts)

        # MLX temps
        print(" MLX90614:")
        print("  Addr   Ambient (F)   Object (F)")
        print("  ----   -----------   ----------")
        for addr, mlx in zip(MLX_ADDRS, mlxs):
            try:
                amb_f = c_to_f(mlx.ambient_temperature)
                obj_f = c_to_f(mlx.object_temperature)
                print(f"  0x{addr:02X}     {amb_f:7.2f}      {obj_f:7.2f}")
            except Exception as e:
                print(f"  0x{addr:02X}     ERROR: {e}")

        # ADS voltages
        print("\n ADS1115:")
        print(f"  A1: {a1.voltage:0.4f} V   (raw={a1.value})")
        print(f"  A2: {a2.voltage:0.4f} V   (raw={a2.value})")
        print(f"  A3: {a3.voltage:0.4f} V   (raw={a3.value})")
        print(f"  A4: {a4.voltage:0.4f} V   (raw={a4.value})")

        print("-" * 50)

        time.sleep(1)

if __name__ == "__main__":
    main()