import math
import time
import requests
import json
import logging
import os
import sys
import socket

# --- LOGGING SETUP ---
# Ensures all events are captured for audit trails
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
    logging.info("Captain Neon configuration loaded successfully.")
except Exception as e:
    logging.critical(f"Captain Neon node failed to load config.json: {e}")
    sys.exit(1)

# --- RADIATOR SETTINGS ---
RADIATOR_ADDR = ("192.168.1.50", 1234)
CMD_BLACKOUT = b'\xFF\x00\x00'
CMD_RESUME = b'\xFF\x00\x01'
last_state = None

# --- GEOMETRIC MATH FUNCTIONS ---
def wgs84_to_local_enu(lat, lon, alt_m):
    """
    Performs full coordinate conversion to local Cartesian (ENU)
    using the projector's reference point settings.
    """
    ref = config["projector"]
    
    # Calculate difference in meters using local projection approximation
    ref_lat_rad = math.radians(ref["lat"])
    x_east = (lon - ref["lon"]) * 111320.0 * math.cos(ref_lat_rad)
    y_north = (lat - ref["lat"]) * 110574.0
    z_up = alt_m - ref["alt_m"]
    
    return x_east, y_north, z_up

# --- INITIALIZE STATE ---
last_valid_data_time = time.time()
logging.info("Captain Neon Safety Node initialized and entering loop.")

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
                        logging.warning(f"Captain Neon Hazard Detected: Aircraft at {slant_range:.1f}m")
                        break
        
        # --- WATCHDOG & ACTUATION LOGIC ---
        # Trigger blackout if aircraft in zone OR telemetry watchdog expires
        watchdog_triggered = (current_time - last_valid_data_time > config["system"]["watchdog_timeout"])
        
        target_state = "BLACKOUT" if (in_zone or watchdog_triggered) else "RESUME"
        
        # Only send UDP packet if the state has changed (State Management)
        if last_state != target_state:
            command = CMD_BLACKOUT if target_state == "BLACKOUT" else CMD_RESUME
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                    s.sendto(command, RADIATOR_ADDR)
                logging.info(f"Captain Neon state transition: {target_state}")
                last_state = target_state
            except Exception as e:
                logging.error(f"UDP communication error: {e}")
            
    except requests.exceptions.RequestException as e:
        # Failsafe: If the request fails, assume hazard
        logging.error(f"Captain Neon telemetry request failed: {e}")
        # Force Blackout if request fails
        if last_state != "BLACKOUT":
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.sendto(CMD_BLACKOUT, RADIATOR_ADDR)
            last_state = "BLACKOUT"
        
    except Exception as e:
        # Catch-all for any other runtime errors
        logging.error(f"Captain Neon runtime loop error: {e}")
        
    # Maintain cycle frequency
    time.sleep(0.1)