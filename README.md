# trane-scripts

Scripts for the Trane senior design project — data collection, control, and MQTT communication for a two-circuit refrigeration system running on Raspberry Pis.

## Directories

### `final/`
Contains all application logic: sensor reading, PID control, PWM output, HMI communication, MQTT I/O, and data storage. Run `python main.py` from here.

### `circuit1/` and `circuit2/`
Deployment directories for each Pi. Each contains only a `config.py` (with circuit-specific settings like `CIRCUIT = "Circuit1"`) and a `main.py` shim. Running `python main.py` from either directory calls `final/main.py` with that circuit's config automatically.

### `mqtt/`
Standalone MQTT utilities: a sensor data simulator, a latency tester, a file-to-MQTT bridge, and an older prototype data server.

### `tests/`
One-off hardware and sensor test scripts (I2C, ADS, MLX, etc.). Not part of the main application.

## Pi Setup

### 1. Flash Raspberry Pi OS

Download and install the [Raspberry Pi Imager](https://www.raspberrypi.com/software/). Flash **Raspberry Pi OS (64-bit)** to an SD card. In the imager's advanced settings, configure your hostname, username, password, and Wi-Fi before writing so the Pi is reachable on first boot or have a monitor and peripherals to login into the Pi.

### 2. Clone this repo

```bash
git clone https://github.com/TGAGNON4/trane-scripts.git
cd trane-scripts
```

### 3. Create a virtual environment

```bash
python3 -m venv ../venv
source ../venv/bin/activate
```

### 4. Install dependencies

Each subdirectory that requires packages has its own README listing what to install. Follow the relevant one for your Pi (e.g. `circuit1/README.md` or `circuit2/README.md`), then install with:

```bash
pip install <packages listed in that README>
```

## Running

On each Pi, run from the appropriate circuit directory:

```bash
cd circuit1   # or circuit2
python main.py
```
