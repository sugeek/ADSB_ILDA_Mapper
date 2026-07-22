import math
import time
import requests
import json
import logging
import os
import sys
import socket
from pythonosc import udp_client
from pythonosc import osc_message_builder

# --- LOGGING SETUP ---
# Ensures all events are captured for auditing purposes
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
# Attempt to load the JSON configuration; abort if critical settings are missing
try:
    with open("config.json", "r") as config_file:
        config = json.load(config_file)
    logging.info("Configuration loaded successfully.")
except Exception as e:
    logging.critical(f"Pangolin node failed to load config.json: {e}")
    sys.exit(1)

# --- OSC CLIENT SETUP ---
# Establish communication with the Pangolin BEYOND software
try:
    osc_client = udp_client.SimpleUDPClient(
        config["osc_targets"]["pangolin"]["ip"], 
        config["osc_targets"]["pangolin"]["port"]
    )
    logging.info(f"OSC client initialized at {config['osc_targets']['pangolin']['ip']}")
except Exception as e:
    logging.critical(f"Failed to initialize OSC client: {e}")
    sys.exit(1)

# --- GEOMETRIC MATH FUNCTIONS ---
def wgs84_to_local_enu(lat, lon, alt_m):
    """
    Performs full coordinate conversion to local Cartesian (ENU)
    using the projector's reference point.
    """
    ref = config["projector"]
    
    # Earth radius and conversion constants
    ref_lat_rad = math.radians(ref["lat"])
    
    # Calculate offset in meters
    x_east = (lon - ref["lon"]) * 111320.0 * math.cos(ref_lat_rad)
    y_north = (lat - ref["lat"]) * 110574.0
    z_up = alt_m - ref["alt_m"]
    
    return x_east, y_north, z_up

# --- INITIALIZE STATE ---
last_valid_data_time = time.time()
logging.info("Pangolin Safety Node initialized and entering loop.")

# --- MAIN SAFETY LOOP ---
while True:
    current_time = time.time()
    in_zone = False
    
    try:
        # Fetch telemetry from ADS-B receiver
        response = requests.get(config["system"]["adsb_url"], timeout=0.5)
        response.raise_for_status()
        aircraft_data = response.json().get("aircraft", [])
        
        # Process each aircraft in the telemetry list
        if aircraft_data:
            last_valid_data_time = current_time
            
            for ac in aircraft_data:
                if "lat" in ac:
                    x, y, z = wgs84_to_local_enu(
                        ac["lat"], 
                        ac["lon"], 
                        ac.get("alt_baro", 0) * 0.3048
                    )
                    
                    # Calculate slant range to target
                    slant_range = math.sqrt(x**2 + y**2 + z**2)
                    
                    # Hazard check
                    if slant_range <= config["hazard_zone"]["max_nohd_meters"]:
                        in_zone = True
                        logging.warning(f"Hazard Detected: Aircraft at {slant_range:.1f}m")
                        break
        
        # --- WATCHDOG & ACTUATION LOGIC ---
        # Trigger blackout if aircraft in zone OR telemetry watchdog expires
        watchdog_triggered = (current_time - last_valid_data_time > config["system"]["watchdog_timeout"])
        
        if in_zone or watchdog_triggered:
            osc_client.send_message("/beyond/master/blackout", 1)
        else:
            osc_client.send_message("/beyond/master/blackout", 0)
            
    except requests.exceptions.RequestException as e:
        # Failsafe: If the request fails, assume hazard and perform blackout
        logging.error(f"Pangolin telemetry request failed: {e}")
        osc_client.send_message("/beyond/master/blackout", 1)
        
    except Exception as e:
        # Catch-all for any other runtime errors
        logging.error(f"Pangolin runtime loop error: {e}")
        osc_client.send_message("/beyond/master/blackout", 1)
        
    # Maintain cycle frequency
    time.sleep(0.05)