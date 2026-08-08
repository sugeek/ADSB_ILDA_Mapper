# Multi-Stream Airspace Laser Safeguard Core

This repository handles automated, multi-stream aircraft and drone telemetry tracking for integration with laser projection safety platforms (such as MadMapper/MadLaser via OSC). It tracks cooperative production assets over local telemetry links and passively sniffs uncontrolled, uncooperative rogue drones using long-range hardware gateways.

---

##  System Architecture & Data Flow

The system runs completely offline on a localized network switch, using an asynchronous Pub/Sub architecture to completely decouple radio packet parsing from the main show control/laser rendering loop.

```
[ Controlled Stream ] ──> MAVLink Telemetry Ground Radio (UDP 14550) ──┐
▼
[ MQTT Broker ] ──► [ Your Parsing Script ] ──► [ OSC Out ] ──► MadMapper
▲
[ Uncontrolled Stream ] ──> Long-Range Outdoor PoE Gateway (2.4G/5.8G) ──┘
```

1. **Controlled Stream**: The production team's drone transmits real-time telemetry over a dedicated radio link to the pilot ground station. The Ground Control Software mirrors this packet state as unencrypted JSON coordinates to a local MQTT topic.
2. **Uncontrolled Stream**: Passive, mast-mounted outdoor receivers scan the environment for public **FAA Remote ID (Bluetooth 5 Coded PHY / Wi-Fi Beacons)**, passing detected uncooperative aircraft profiles directly to the same broker.
3. **Tracking Engine**: The tracking hub fetches these streams via a non-blocking background thread, converts global WGS84 coordinates to local Cartesian (ENU) coordinates relative to the projector head, assesses hazard zones, and fires vector blanking commands to the laser server over customizable OSC ports.

---

##  Hardware Dependencies (Long-Range Uncontrolled Sniffing)

To maintain a wide enough protection perimeter to safeguard tight-divergence laser lenses and camera sensors, do **not** use short-range consumer PC USB dongles. Use dedicated long-range network infrastructure:

### 1. The PoE Gateway Option (Recommended for Venue Deployment)
* **Hardware**: [DroneScout ds240/ds230](https://dronescout.co) or [Dronetag Scout](https://dronetag.com).
* **Specifications**: Outdoor IP67 mast-mounted receiver powered completely via **Standard 48V PoE (802.3af/at)** over Cat6 (up to 100 meters away from Front of House).
* **Antennas**: High-Gain 2.4GHz / 5.8GHz Omnidirectional Outdoor Aerials mated via heavy-duty **N-Type Female RF connectors**.
* **Stream Delivery**: Natively publishes unencrypted JSON payloads directly over the local network via TCP/IP to an MQTT broker.

### 2. The Microcontroller Interface Option (Affordable DIY)
* **Hardware**: [Seeed Studio XIAO ESP32-C3](https://seeedstudio.com) or Nordic nRF52840 Eval Board running open-source [OpenDroneID Firmware](https://github.com).
* **Specifications**: Bypasses the laptop's shielded internal chipsets to decode Bluetooth 5 Long Range (LE Coded PHY) hardware error correction (S=8 mode) on silicon.
* **Cabling**: Requires a **u.FL to Female SMA Pigtail coax** to adapt the tiny board pad to an external antenna structure. Passes text via USB Serial (`pyserial`).

---

## 📦 Software Dependencies

Install the core tracking and communication libraries on your local parsing engine using `pip`:

```bash
pip install paho-mqtt oscpy pymavlink
```

* **`paho-mqtt`**: Handles background network loop management and parsing for asynchronous telemetry packets.
* **`oscpy`**: Handles fast, low-overhead UDP broadcasting to target media servers (MadMapper, Neon Captain Radiator, or TouchDesigner).
* **`pymavlink`** *(Optional)*: Required if you are running a direct offline serial link from an ArduPilot/PX4 telemetry radio instead of network mirroring.

### Local Network Broker Setup
The script requires an active local MQTT broker running on your network switch (e.g., at IP `192.168.1.50`). You can run a clean, lightweight instance of eclipse-mosquitto via Docker:

### MQTT: 
The script for drones requires an active local MQTT broker running on your local network space. To avoid paid enterprise licensing platforms, you can run a lightweight instance of the open-source `eclipse-mosquitto` broker inside Minikube Kubernetes cluster, docker or where ever you care to run it:


#### Docker MQTT
```bash
docker run -d -p 1883:1883 --name show-mqtt eclipse-mosquitto
```

#### Minikube MQTT

```bash
minikube start
kubectl create deployment show-mqtt --image=eclipse-mosquitto:latest
kubectl expose deployment show-mqtt --type=NodePort --port=1883
kubectl port-forward service/show-mqtt 1883:1883
```

---

## ⚙️ Configuration File Structure (`config.json`)

```json
{
  "projector": {
    "lat": 40.786400,
    "lon": -119.204500,
    "alt_m": 1200.0,
    "azimuth": 0.0,
    "h_fov": 60.0,
    "v_fov": 45.0,
    "divergence_mrad": 2.0
  },
  "hazard_zone": {
    "max_nohd_meters": 3500.0
  },
  "system": {
    "watchdog_timeout": 1.0,
    "adsb_url": "http://127.0.0.1:8080/data/aircraft.json",
    "log_path": "logs/safety_system.log",
    "mqtt_broker_ip": "192.168.1.50",
    "mqtt_broker_port": 1883,
    "mqtt_topic": "telemetry/#",
    "uav_data_timeout_seconds": 5.0
  },
  "osc_targets": {
    "madmapper": {"ip": "127.0.0.1", "port": 8010},
    "pangolin": {"ip": "127.0.0.1", "port": 9000},
    "openlase": {"ip": "127.0.0.1", "port": 7000},
    "radiator": {"ip": "127.0.0.1", "port": 8000},
    "uav_tracker_prefix": "/madlaser/elements/uav"
  }
}
```

### JSON Element Descriptions

#### `projector` (Spatial Anchor & Optical Properties)
* **`lat`** *(Float)*: The physical latitude coordinate of the laser projector head using the standard WGS84 global datum. Used as the local map baseline.
* **`lon`** *(Float)*: The physical longitude coordinate of the laser projector head using the standard WGS84 global datum. Used as the local map baseline.
* **`alt_m`** *(Float)*: The altitude of the laser projector head calculated in meters above Mean Sea Level (MSL).
* **`azimuth`** *(Float)*: The horizontal heading orientation angle of the laser hardware face in degrees (0.0 = True North, 90.0 = East, etc.).
* **`h_fov`** *(Float)*: The maximum horizontal optical field-of-view scanning limit of the laser galvos, measured in degrees.
* **`v_fov`** *(Float)*: The maximum vertical optical field-of-view scanning limit of the laser galvos, measured in degrees.
* **`divergence_mrad`** *(Float)*: The physical expansion rate of the laser beam diameter over distance, measured in milliradians (mrad). Crucial for future dynamic eye-safety attenuation calculations.

#### `hazard_zone` (System Safety Thresholds)
* **`max_nohd_meters`** *(Float)*: Nominal Ocular Hazard Distance. The designated physical perimeter boundary radius in meters around the projector where the raw laser beam energy density remains unsafe for unprotected human eyes or camera sensors.

#### `system` (Core Networking & Data Lifecycle)
* **`watchdog_timeout`** *(Float)*: The safety heartbeat threshold timer in seconds. If main loop data updates stop arriving within this window, the system flags a fault state.
* **`adsb_url`** *(String)*: The local network HTTP endpoint exposing the raw JSON broadcast stream from the RTL-SDR manned commercial aviation receiver array.
* **`log_path`** *(String)*: The explicit storage directory path and file name target where system tracking audits and hazard exceptions are saved.
* **`mqtt_broker_ip`** *(String)*: The network IP address of the central local switch broker routing the incoming drone telemetry packets.
* **`mqtt_broker_port`** *(Integer)*: The TCP network port allocated for standard, unencrypted MQTT communication (Default standard is 1883).
* **`mqtt_topic`** *(String)*: The communication namespace string hook. Utilizes wildcard syntax (`#`) to simultaneously ingest multiple data topics.
* **`uav_data_timeout_seconds`** *(Float)*: The memory expiration timer.Drones that drop connection or fly out of range are cleanly deleted from active tracking tracking arrays after this window to prevent ghost masking locks.

#### `osc_targets` (Outgoing Hardware Ports)
* **`madmapper`** *(Object)*: Target destination mapping parameter block (IP/Port) hosting MadMapper 5 and the MadLaser extension layer.
* **`pangolin`** *(Object)*: Target destination mapping parameter block (IP/Port) routing vector data to Beyond/QuickShow controller hardware links.
* **`openlase`** *(Object)*: Target destination mapping parameter block (IP/Port) streaming directly to open-source software-defined vector tools.
* **`radiator`** *(Object)*: Target destination mapping parameter block (IP/Port) corresponding to the Neon Captain Radiator hardware module.
* **`uav_tracker_prefix`** *(String)*: The custom base string format utilized for the outgoing OSC network namespaces (e.g., `/madlaser/elements/uav/drone_id/x`).

---

## Show Day Validation Protocol (LSO Check)

> **CRITICAL SAFETY NOTE**: This automated software mapping framework is designed strictly as a secondary defensive layer. Software systems, network routing lines, and RF tracking loops are vulnerable to latency, dropped packets, and firmware exceptions. They **DO NOT** replace or supersede trained human spotters and physical E-Stop termination systems managed by the Laser Safety Officer (LSO).

When deploying this tracking engine for physical test confirmation, ensure your team ticks off the following steps:

1. **Verify Coordinate Origins**: Confirm the `projector` coordinates inside `config.json` match your physical hardware setup location to ensure the local Cartesian (ENU) math accurately transforms WGS84 coordinate vectors.
2. **Execute a Range-Test**: Emulate a remote ID beacon or fly your cooperative test drone to the perimeter edge. Confirm that the background thread flags the vehicle and writes warnings to the log before the aircraft breaches your `max_nohd_meters` radius.
3. **Confirm Dynamic Masking Response**: Watch the MadMapper laser output preview window. Verify that when target aircraft traverse inside your defined safety boundary, the corresponding digital mask instantly engages and blanks the color diode vectors down to **0% intensity** at the target destination without lag.
