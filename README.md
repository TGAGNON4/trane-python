# trane-scripts

Scripts for the Trane senior design project — data collection, control, and MQTT communication for a two-circuit refrigeration system running on Raspberry Pis.

## Directories

### `final/`
Contains all application logic. Key modules: `main.py` (orchestrator), `compressor.py` (PID/PWM/VFD), `sensors.py` (MLX90614 + ADS1115), `storage.py` (buffered file persistence), `hmi.py` (Nextion serial), `mqtt_handlers.py` / `mqtt_publish.py` / `mqtt_replay.py` (MQTT I/O), `coolprop_props.py` (R-1234yf thermodynamics). Run `python main.py` from here.

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

## Running manually

On each Pi, run from the appropriate circuit directory:

```bash
cd circuit1   # or circuit2
python main.py
```

## Running as a systemd service (auto-start on boot)

These steps set up Circuit 1 to start automatically. For Circuit 2, replace every occurrence of `circuit1` with `circuit2`.

### Step 1: Create the wrapper script

```bash
nano /home/team6/start_circuit1.sh
```

Paste this:

```bash
#!/bin/bash

cd /home/team6/trane-python
git pull origin main

source /home/team6/adsenv/bin/activate
python3 /home/team6/trane-python/circuit1/main.py
```

Save and exit: `Ctrl+X` → `Y` → `Enter`

### Step 2: Make it executable

```bash
chmod +x /home/team6/start_circuit1.sh
```

### Step 3: Create the service file

```bash
sudo nano /etc/systemd/system/circuit1.service
```

Paste this:

```ini
[Unit]
Description=Circuit 1 Main Script
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=/home/team6/start_circuit1.sh
WorkingDirectory=/home/team6/trane-python/circuit1
StandardOutput=journal
StandardError=journal
Restart=always
User=team6

[Install]
WantedBy=multi-user.target
```

Save and exit: `Ctrl+X` → `Y` → `Enter`

### Step 4: Create the data folder

```bash
mkdir -p /home/team6/data
```

### Step 5: Enable and start the service

```bash
sudo systemctl enable circuit1.service
sudo systemctl start circuit1.service
```

### Step 6: Verify it's running

```bash
sudo systemctl status circuit1.service
```
