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

Download and install the [Raspberry Pi Imager](https://www.raspberrypi.com/software/). Flash **Raspberry Pi OS (64-bit)** to an SD card. In the imager's advanced settings, configure your hostname, username (`team6`), password, and Wi-Fi before writing so the Pi is reachable on first boot, or use a monitor and peripherals to log in directly.

### 2. Enable SSH

SSH is disabled by default on Raspberry Pi OS. Enable it via `raspi-config`:

```bash
sudo raspi-config
```

Navigate to **Interface Options → SSH** and select **Enable**. Alternatively, if you used the Imager's advanced settings you can enable SSH there before flashing.

### 3. Enable UART for the HMI display and disable serial console login

The Nextion HMI connects over the hardware UART pins (GPIO 14/15). By default the Pi uses those pins as a serial console for login, which conflicts with the HMI. You need to enable the UART hardware and disable the login shell on it.

Run `raspi-config`:

```bash
sudo raspi-config
```

Navigate to **Interface Options → Serial Port**:
- **Would you like a login shell to be accessible over the serial port?** → **No**
- **Would you like the serial port hardware to be enabled?** → **Yes**

Finish and reboot. After rebooting, `/dev/serial0` (aliased to `/dev/ttyAMA0` or `/dev/ttyS0` depending on the Pi model) will be available for the HMI without a conflicting getty process.

### 4. Add team6 to the gpio group

The compressor speed is controlled via `RPi.GPIO`, which requires access to `/dev/gpiomem`. Raspberry Pi OS ships with a `gpio` group that has this access:

```bash
sudo usermod -aG gpio team6
```

Log out and back in (or reboot) for the group membership to take effect.

### 5. Clone this repo

```bash
git clone https://github.com/TGAGNON4/trane-python.git
cd trane-python
```

### 6. Create a virtual environment

```bash
python3 -m venv ../venv
source ../adsenv/bin/activate
```

### 7. Install dependencies

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

# disabled for security purposes
#git pull origin main

source /home/team6/venv/bin/activate
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

---

## AWS EC2 MQTT Broker Setup

This documents how to set up a Mosquitto MQTT broker on an EC2 instance with WebSocket support so both the Raspberry Pis (plain MQTT on port 1883) and the browser dashboard (MQTT-over-WebSockets on port 8083) can connect.

### 1. Launch an EC2 instance

1. Open the [EC2 Console](https://console.aws.amazon.com/ec2/) and click **Launch instance**.
2. Choose **Ubuntu Server 22.04 LTS** (free tier eligible, `t2.micro`).
3. Create or select a key pair and download the `.pem` file — you need it to SSH in.
4. Under **Network settings**, create a security group with the following inbound rules:

| Type       | Protocol | Port | Source    | Purpose                          |
|------------|----------|------|-----------|----------------------------------|
| SSH        | TCP      | 22   | Your IP   | Admin access                     |
| Custom TCP | TCP      | 1883 | 0.0.0.0/0 | MQTT (Raspberry Pis)            |
| Custom TCP | TCP      | 8083 | 0.0.0.0/0 | MQTT over WebSockets (browser)  |

5. Launch the instance and note its **Public IPv4 address** or DNS name.

### 2. SSH into the instance

```bash
chmod 400 your-key.pem
ssh -i your-key.pem ubuntu@<your-ec2-public-ip>
```

### 3. Install Mosquitto

```bash
sudo apt update
sudo apt install -y mosquitto mosquitto-clients
sudo systemctl enable mosquitto
```

### 4. Create a password file

Do **not** put passwords in the config file in plain text. Use Mosquitto's password utility:

```bash
sudo mosquitto_passwd -c /etc/mosquitto/passwd <username>
```

You will be prompted to enter and confirm the password. To add more users later (without the `-c` flag, which overwrites the file):

```bash
sudo mosquitto_passwd /etc/mosquitto/passwd <another-username>
```

### 5. Configure Mosquitto

```bash
sudo nano /etc/mosquitto/conf.d/trane.conf
```

Paste the following:

```
# Plain MQTT listener (Raspberry Pis)
listener 1883
allow_anonymous false
password_file /etc/mosquitto/passwd

# WebSocket listener (browser dashboard)
listener 8083
protocol websockets
allow_anonymous false
password_file /etc/mosquitto/passwd
```

Save and exit: `Ctrl+X` → `Y` → `Enter`

### 6. Restart and verify

```bash
sudo systemctl restart mosquitto
sudo systemctl status mosquitto
```

Test from a separate terminal (replace values as needed):

```bash
mosquitto_sub -h <your-ec2-ip> -p 1883 -u <username> -P <password> -t test/#
```

In another terminal:

```bash
mosquitto_pub -h <your-ec2-ip> -p 1883 -u <username> -P <password> -t test/hello -m "world"
```

### 7. Point the Pis and dashboard at the new broker

In each Pi's `circuit1/config.py` or `circuit2/config.py`, update:

```python
BROKER = "<your-ec2-public-ip-or-dns>"
PORT   = 1883
```

In the dashboard (`src/hooks/MQTT.ts`), update the broker URL:

```typescript
url: "wss://<your-ec2-public-ip-or-dns>:8083"
```

> **Note:** Browsers require WSS (encrypted WebSockets) when the dashboard is served over HTTPS. To enable TLS, obtain a certificate (e.g. via Let's Encrypt with `certbot`) and add `cafile`, `certfile`, and `keyfile` paths to the Mosquitto listener config. Plain `ws://` works when the dashboard is accessed over HTTP only.
