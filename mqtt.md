# Local Network MQTT Telemetry Architecture Specifications

This document outlines the unified Message Queuing Telemetry Transport (MQTT) pub/sub architecture utilized to manage multi-stream airspace data. This architecture decouples raw radio frequency data ingestion from the high-frequency safety actuation loops controlling laser projection blanking servers (e.g., OpenLase, MadMapper, Pangolin).

---

## System Topology

All hardware and software nodes sit on a localized network switch operating completely offline. 

```text
[ Ingestion Layer (Publishers) ]               [ Local Message Bus ]        [ Actuation Layer (Subscribers) ]

Manned Aviation (dump1090 Bridge) ───► JSON ───┐
                                               ▼
Cooperative Assets (MAVLink Link) ───► JSON ───┼──► [ MQTT Broker ] ───► [ OpenLase Safety Node ] ──► OSC ──► Projector Shutter
                                               ▲   (Mosquitto Local)
Rogue Drone Sniffers (PoE Gateway) ──► JSON ───┘
```

### Strategic Network Advantages
* **Zero Main-Loop Blocking**: Replaces high-overhead synchronous HTTP polling requests (`requests.get`) with an asynchronous background C-thread socket listener.
* **Parallel Consumption**: Allows multiple nodes (safety scripts, LSO visual dashboards, automated tracking fixtures) to observe the exact same data streams simultaneously without performance penalties.
* **Graceful Degradation**: If an individual data script or visualization module crashes, the local network broker remains unaffected, allowing the engine to retain or restore tracking states seamlessly.

---

## 🌳 Telemetry Topic Tree Schema

The broker segregates data streams into explicit namespaces using a logical hierarchical path layout. This allows subscriber clients to listen to individual tracking targets or ingest global airspace data using wildcard flags (`#`, `+`).

```text
telemetry/
├── adsb/
│   ├── {icao_24bit_hex}/
│   │   └── position           <── Commercial & Manned Aviation Telemetry
│   └── {icao_24bit_hex}/
│       └── status             <── Transponder Signal Strength & Health Telemetry
├── controlled/
│   ├── {uav_flight_id}/
│   │   └── position           <── Production Cinema Drones & Cooperative Assets
│   └── {uav_flight_id}/
│       └── status             <── Battery, Link Quality & Command Loop States
└── uncontrolled/
    ├── {remote_id_serial}/
    │   └── position           <── Passive Remote ID Intercept (Uncooperative UAVs)
    └── {remote_id_serial}/
        └── status             <── Signal Strength & RF Detection Metadata
```

---

## 📋 JSON Payload Specifications

To maintain maximum compatibility across varying data ingestion sources, the core tracking script handles property structural variations natively.

### 1. Manned Aviation (`telemetry/adsb/{icao_hex}/position`)
Published via your local `adsb_to_mqtt.py` bridge utility monitoring `dump1090`. Altitude is automatically scaled to metric units.
```json
{
  "id": "a4b2c1",
  "flight": "UAL1230",
  "lat": 40.812450,
  "lon": -119.215400,
  "alt_msl": 3450.4,
  "speed_kt": 240,
  "heading": 180,
  "seen_seconds_ago": 0.4
}
```

### 2. Cooperative Production UAVs (`telemetry/controlled/{uav_id}/position`)
Published via your pilot ground control interface (e.g., QGroundControl MAVLink-to-MQTT mirror link).
```json
{
  "id": "cinema_heavy_01",
  "lat": 40.789210,
  "lon": -119.206310,
  "altitude": 1245.5,
  "heading": 45.2,
  "battery_percent": 88
}
```

### 3. Passive Uncooperative UAV Detectors (`telemetry/uncontrolled/{serial}/position`)
Published natively via long-range, outdoor mast-mounted PoE gateways sniffing for public Bluetooth 5 Coded PHY or Wi-Fi beacon envelopes.
```json
{
  "drone_id": "70M28DF12A9X0",
  "latitude": 40.791140,
  "longitude": -119.201240,
  "alt_msl": 1280.2,
  "rssi_dbm": -72,
  "protocol": "BLE_LONG_RANGE"
}
```

---

## Command-Line Diagnostic Verification

During technical rehearsals and show setups, use standard CLI utilities on your local network switch loop to monitor or emulate telemetry payloads without touching your core python automation engine.

### Listening to the Whole Sky (Global Audit)
To observe every single object moving across your airspace envelope simultaneously, subscribe to the root namespace using the multi-level wildcard (`#`):
```bash
mosquitto_sub -h 192.168.1.50 -p 1883 -t "telemetry/#" -v
```

### Listening to Drones Only
To view all drone positions while excluding fast-moving manned transponder traffic, utilize the single-level wildcard (`+`):
```bash
mosquitto_sub -h 192.168.1.50 -p 1883 -t "telemetry/+/+/position" -v
```

### Simulating a Intruder Hazard Target (Mock Ingestion)
To manually inject an uncooperative rogue drone coordinate directly into your safety class algorithm to verify that your OpenLase/MadMapper shutters execute blanking commands on demand, fire a mock string payload via publication commands:
```bash
mosquitto_pub -h 192.168.1.50 -p 1883 -t "telemetry/uncontrolled/mock_uav/position" -m '{"drone_id": "mock_uav", "latitude": 40.786500, "longitude": -119.204600, "alt_msl": 1210.0}'
```

---

## Operation & Memory Management Rules
1. **The Failsafe Timeout Window**: The tracking logic maintains a strict target pruning dictionary loop. If an uncooperative or cooperative tracking target does not publish an update payload for more than **5.0 seconds** (configured via `uav_data_timeout_seconds`), it is safely deleted from tracking array memory to clear the safety shutter from permanent "ghost" locking states.
2. **The Global Watchdog Heartbeat**: If your network switch loses power or the MQTT broker connection drops completely, the safety script tracking system will trigger a global watchdog alert state after **1.0 seconds** (`watchdog_timeout`), immediately defaulting your laser server to an uninterrupted **100% hard blanking blackout** until network topology connectivity is verified by the Laser Safety Officer (LSO).