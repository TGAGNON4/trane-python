import time
import os
import pytz
from smbus2 import SMBus
from datetime import datetime

# Sensor I2C addresses - update these once addresses are confirmed
SENSORS = {
    'HighSide':   0x5A,
    'LowSide':    0x5B,
    'Evaporator': 0x5C,
    'Condenser':  0x5D,
    'SpaceTemp':  0x5E
}

AMBIENT_REG = 0x06
OBJECT_REG  = 0x07

timezone = pytz.timezone("America/Chicago")

def read_temp_c(bus, addr, reg):
    data = bus.read_word_data(addr, reg)
    data = data & 0xFFFF
    temp_c = data * 0.02 - 273.15
    return temp_c

def write_temps(bus):
    # Get the current date
    current_date = datetime.now(timezone).strftime('%d-%m-%Y')

    # Build folder path
    #base_path = '/home/team6/data'
    base_path = '/home/thomas/School/SeniorDesign/trane-scripts'
    folder_path = os.path.join(base_path, current_date)
    if not os.path.exists(folder_path):
        os.makedirs(folder_path)

    current_time = datetime.now(timezone).strftime("%H:%M:%S")

    # Read ambient and object temp for each sensor
    # Line format: time, ambient1, object1, ambient2, object2, ...
    readings = [current_time]
    for name, addr in SENSORS.items():
        ambient = read_temp_c(bus, addr, AMBIENT_REG)
        obj     = read_temp_c(bus, addr, OBJECT_REG)
        readings.append(str(ambient))
        readings.append(str(obj))

    # Write one line per reading
    file_path = os.path.join(folder_path, 'temps')
    with open(file_path, 'a') as file:
        file.write(",".join(readings) + "\n")

write_temps(SMBus(1))
