import math
import time
import json
import logging
import os
import sys
import threading
import paho.mqtt.client as mqtt
from pythonosc import udp_client

# --- LOGGING SETUP ---
os.makedirs("logs", exist_ok=True)
log_file_path = "logs/safety_system.log"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(log_file_path),
        logging.StreamHandler()
    ]
)

# --- LOAD CONFIGURATION ---
try:
    with open("config.json", "r") as config_file:
        config = json.load(config_file)
    logging.info("OpenLase configuration loaded successfully.")
except Exception as e:
    logging.critical(f"OpenLase node failed to load config.json: {e}")
    sys.exit(1)

# --- OSC CLIENT SETUP ---
try:
    osc_client = udp_client.SimpleUDPClient(
        config["osc_targets"]["openlase"]["ip"], 
        config["osc_targets"]["openlase"]["port"]
    )
    logging.info(f"OpenLase OSC client initialized at {config['osc_targets']['openlase']['ip']}")
except Exception as e:
    logging.critical(f"Failed to initialize OpenLase OSC client: {e}")
    sys.exit(1)

# --- GEOMETRIC MATH FUNCTIONS ---
def wgs84_to_local_enu(lat, lon, alt_m):
    """
    Performs full coordinate conversion to local Cartesian (ENU)
    using the projector's reference point settings.
    """
    ref = config["projector"]
    ref_lat_rad = math.radians(ref["lat"])
    x_east = (lon - ref["lon"]) * 111320.0 * math.cos(ref_lat_rad)
    y_north = (lat - ref["lat"]) * 110574.0
    z_up = alt_m - ref["alt_m"]
    return x_east, y_north, z_up

# --- GLOBAL THREAD-SAFE STATE ---
state_lock = threading.Lock()
active_aircraft = {}  # Format: { "hex_or_uav_id": {"last_seen": timestamp, "is_hazard": bool} }
last_valid_data_time = time.time()

# --- MQTT CALLBACK HANDLERS ---
def on_connect(client, userdata, flags, rc):
    """Triggered automatically upon successful connection to the network broker switch."""
    if rc == 0:
        topic = config["system"].get("mqtt_topic", "telemetry/#")
        logging.info(f"Connected to MQTT broker. Subscribing to topic root: {topic}")
        client.subscribe(topic)
    else:
        logging.error(f"MQTT connection failed with return code {rc}")

def on_message(client, userdata, msg):
    """Asynchronously parses incoming JSON tracking payloads across all telemetry streams."""
    global last_valid_data_time
    current_time = time.time()
    
    try:
        payload = msg.payload.decode("utf-8")
        aircraft = json.loads(payload)
        
        # Unify common layout keys between ADS-B schema and Remote ID structures
        ac_id = aircraft.get("id") or aircraft.get("hex") or "unknown"
        lat = aircraft.get("lat") or aircraft.get("latitude")
        lon = aircraft.get("lon") or aircraft.get("longitude")
        
        # Extract altitude supporting standard baro feet or absolute metric values
        if "alt_msl" in aircraft:
            alt_m = float(aircraft["alt_msl"])
        elif "altitude" in aircraft:
            alt_m = float(aircraft["altitude"]) * 0.3048
        else:
            alt_m = 0.0

        if lat is not None and lon is not None:
            # Execute local Cartesian mapping calculations
            x, y, z = wgs84_to_local_enu(float(lat), float(lon), alt_m)
            slant_range = math.sqrt(x**2 + y**2 + z**2)
            is_hazard = slant_range <= config["hazard_zone"]["max_nohd_meters"]
            
            if is_hazard:
                logging.warning(f"Hazard Flag Raised: Object [{ac_id}] at {slant_range:.1f}m inside safety zone.")

            # Update the global state dictionary atomically
            with state_lock:
                last_valid_data_time = current_time
                active_aircraft[ac_id] = {
                    "last_seen": current_time,
                    "is_hazard": is_hazard
                }
    except Exception as e:
        logging.debug(f"Failed to parse asynchronous MQTT network payload: {e}")

# --- SETUP ASYNCHRONOUS MQTT CLIENT ---
mqtt_client = mqtt.Client()
mqtt_client.on_connect = on_connect
mqtt_client.on_message = on_message

try:
    mqtt_client.connect(
        config["system"]["mqtt_broker_ip"], 
        config["system"].get("mqtt_broker_port", 1883), 
        60
    )
    # Spins up an isolated background OS thread to ingest data without dragging down the shutter loop
    mqtt_client.loop_start()
except Exception as e:
    logging.critical(f"Failed to initiate network MQTT loop: {e}")
    sys.exit(1)

logging.info("OpenLase MQTT Safety Node initialized and entering loop.")

# --- MAIN SAFETY ACTUATION LOOP ---
try:
    while True:
        current_time = time.time()
        in_zone = False
        timeout_threshold = config["system"].get("uav_data_timeout_seconds", 5.0)
        
        with state_lock:
            # 1. Sweep expired target tracking logs out of active memory
            expired_targets = [
                ac_id for ac_id, target in active_aircraft.items() 
                if current_time - target["last_seen"] > timeout_threshold
            ]
            for ac_id in expired_targets:
                del active_aircraft[ac_id]
                logging.info(f"Target [{ac_id}] signal lost or departed. Expired from tracking tracking memory.")
            
            # 2. Check remaining target objects for spatial violations
            if any(target["is_hazard"] for target in active_aircraft.values()):
                in_zone = True

        # --- WATCHDOG & ACTUATION LOGIC ---
        # Failsafe: Trigger shutter blanking if target in zone OR global tracking heartbeat drops out
        watchdog_triggered = (current_time - last_valid_data_time > config["system"]["watchdog_timeout"])
        
        if in_zone or watchdog_triggered:
            if watchdog_triggered:
                logging.error("SAFETY CRITICAL: Telemetry network watchdog expired! Forcing blanking blackout state.")
            osc_client.send_message("/openlase/blank", 1)
        else:
            osc_client.send_message("/openlase/blank", 0)
            
        # Maintain rapid 20Hz cycle frequency for responsive hardware actuation
        time.sleep(0.05)

except KeyboardInterrupt:
    logging.info("Shutdown signal received. Cleaving connections...")
finally:
    mqtt_client.loop_stop()
    mqtt_client.disconnect()
    logging.info("OpenLase safety thread successfully terminated.")
