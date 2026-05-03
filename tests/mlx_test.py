from smbus2 import SMBus
import time

MLX90614_ADDR = 0x5A
AMBIENT_REG   = 0x06
OBJECT_REG    = 0x07

def read_temp(bus, reg):
    # Read a 16-bit word from the sensor
    data = bus.read_word_data(MLX90614_ADDR, reg)

    # Many examples do a manual byte swap, but on your setup
    # that appears to be giving nonsense. Let's try it *without*
    # swapping first:
    raw = data & 0xFFFF

    # Debug: see the raw value we got
    print(f"reg 0x{reg:02X} raw = 0x{raw:04X} ({raw})")

    # According to datasheet: Temp[K] = raw * 0.02
    temp_c = raw * 0.02 - 273.15
    return temp_c

with SMBus(1) as bus:
    while True:
        amb = read_temp(bus, AMBIENT_REG)
        obj = read_temp(bus, OBJECT_REG)

        print(f"Ambient: {amb:.2f} °C | Object: {obj:.2f} °C")
        print("-" * 50)
        time.sleep(1)
