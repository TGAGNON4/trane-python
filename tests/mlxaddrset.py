import smbus
import time

CURRENT_ADDR    = 0x5A
NEW_ADDR        = 0x5D   # Change this per sensor, 5b,5c,5d for unit 2. 2a,3a,4a,5a unit 1
EEPROM_ADDR_REG = 0x2E

def crc8(data):
    """CRC-8 used by MLX90614 (polynomial 0x07)."""
    crc = 0x00
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x80:
                crc = (crc << 1) ^ 0x07
            else:
                crc <<= 1
            crc &= 0xFF
    return crc

def write_eeprom_with_pec(bus, dev_addr, reg, lsb, msb):
    # PEC is calculated over: [dev_addr<<1, reg, lsb, msb]
    pec = crc8([(dev_addr << 1), reg, lsb, msb])
    bus.write_i2c_block_data(dev_addr, reg, [lsb, msb, pec])
    time.sleep(0.05)

bus = smbus.SMBus(1)

print("=== MLX90614 Address Reprogrammer (with PEC) ===\n")

# Read current
data = bus.read_i2c_block_data(CURRENT_ADDR, EEPROM_ADDR_REG, 3)
print(f"Current EEPROM cell: {[hex(b) for b in data]} (stored addr: {hex(data[0])})")

# Erase
print("\nErasing (writing 0x0000 with PEC)...")
write_eeprom_with_pec(bus, CURRENT_ADDR, EEPROM_ADDR_REG, 0x00, 0x00)
print("Done.")

# Write new address
print(f"Writing new address {hex(NEW_ADDR)} with PEC...")
write_eeprom_with_pec(bus, CURRENT_ADDR, EEPROM_ADDR_REG, NEW_ADDR, 0x00)
print("Done.")

# Read back before power cycle
data = bus.read_i2c_block_data(CURRENT_ADDR, EEPROM_ADDR_REG, 3)
print(f"\nEEPROM cell after write: {[hex(b) for b in data]} (stored addr: {hex(data[0])})")

bus.close()

if data[0] == NEW_ADDR:
    print(f"\n✓ EEPROM updated successfully!")
else:
    print(f"\n✗ Write still failed — value is {hex(data[0])}, expected {hex(NEW_ADDR)}")
    print("  Check wiring and try again.")
    exit()

input("\n>>> UNPLUG sensor VCC, wait 3 seconds, plug back in, then press ENTER...")
time.sleep(0.5)

# Verify at new address
bus = smbus.SMBus(1)
try:
    data = bus.read_i2c_block_data(NEW_ADDR, EEPROM_ADDR_REG, 3)
    print(f"\n✓ SUCCESS — sensor responding at {hex(NEW_ADDR)}")
except:
    print(f"\n✗ Sensor not found at {hex(NEW_ADDR)} after power cycle")
    print("  Run i2cdetect to locate it.")
bus.close()