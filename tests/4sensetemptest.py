import time
import board
import busio
import adafruit_ads1x15.ads1115 as ADS
from adafruit_ads1x15.analog_in import AnalogIn

# ── Config ──────────────────────────
ADS_ADDR  = 0x48
R_SENSE   = 200     # ohms
I_MIN     = 0.004   # 4 mA
I_MAX     = 0.020   # 20 mA
P_MAX     = 150.0   # psi
# ────────────────────────────────────

def voltage_to_psi(v: float) -> float:
    current = v / R_SENSE
    current = max(I_MIN, min(I_MAX, current))
    return ((current - I_MIN) / (I_MAX - I_MIN)) * P_MAX

def is_connected(v: float) -> bool:
    return v > (0.0035 * R_SENSE)  # below ~0.7V = no sensor

def main():
    i2c = busio.I2C(board.SCL, board.SDA)
    ads = ADS.ADS1115(i2c, address=ADS_ADDR)
    ads.gain = 1
    channels = [AnalogIn(ads, i) for i in range(4)]

    print("Testing ADS1115 all channels | Press Ctrl+C to stop\n")
    print(f"  {'Ch':<5} {'Pressure':>10} {'Voltage':>10} {'Raw':>8}")
    print("  " + "-" * 38)

    while True:
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        print(f"\n  {ts}")
        for i, ch in enumerate(channels):
            v = ch.voltage
            if not is_connected(v):
                print(f"  A{i:<4} {'DISCONNECTED':>10}   ({v:.4f} V)")
            else:
                print(f"  A{i:<4} {voltage_to_psi(v):>9.1f} psi  {v:>8.4f} V  {ch.value:>8}")
        time.sleep(1)

if __name__ == "__main__":
    main()
