import math
import time
import requests
import argparse
import json
import sys
import logging
import os
from pythonosc import udp_client

# --- LOGGING SETUP ---
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.FileHandler("logs/safety_system.log"), logging.StreamHandler()]
)

# --- LOAD CONFIGURATION ---
try:
    with open("config.json", "r") as config_file:
        config = json.load(config_file)
except Exception as e:
    logging.critical(f"Failed to load config.json: {e}")
    sys.exit(1)

# --- CLI ARGUMENTS ---
parser = argparse.ArgumentParser(description='MadMapper Laser Safety Node')
parser.add_argument('--mode', choices=['mask', 'kill'], default='mask',
                    help="Choose 'mask' for dynamic blanking or 'kill' for master blackout.")
args = parser.parse_args()

# --- OSC CLIENT SETUP ---
osc_client = udp_client.SimpleUDPClient(
    config["osc_targets"]["madmapper"]["ip"], 
    config["osc_targets"]["madmapper"]["port"]
)

# --- MATH FUNCTIONS ---
def wgs84_to_local_enu(lat, lon, alt_m):
    ref = config["projector"]
    x_east = (lon - ref["lon"]) * 111320.0 * math.cos(math.radians(ref["lat"]))
    y_north = (lat - ref["lat"]) * 110574.0
    z_up = alt_m - ref["alt_m"]
    return x_east, y_north, z_up

def enu_to_spherical(x, y, z):
    h_dist = math.sqrt(x**2 + y**2)
    azimuth = (math.degrees(math.atan2(x, y)) + 360) % 360
    elevation = math.degrees(math.atan2(z, h_dist))
    return azimuth, elevation

def spherical_to_texture_uv(az, el, p_az, h_fov, v_fov):
    az_diff = (az - p_az + 540) % 360 - 180
    if abs(az_diff) > (h_fov / 2.0) or abs(el) > (v_fov / 2.0):
        return None
    u = (az_diff + (h_fov / 2.0)) / h_fov
    v = (el + (v_fov / 2.0)) / v_fov
    return max(0.0, min(1.0, u)), max(0.0, min(1.0, v))

# --- MAIN SAFETY LOOP ---
logging.info(f"Starting MadMapper Node. Mode: {args.mode.upper()}")
last_valid_data_time = time.time()

while True:
    current_time = time.time()
    in_zone = False
    
    try:
        response = requests.get(config["system"]["adsb_url"], timeout=0.5)
        data = response.json().get("aircraft", [])
        
        if data:
            last_valid_data_time = current_time
            for t in data:
                if "lat" in t:
                    # Convert to ENU and calculate Slant Range
                    x, y, z = wgs84_to_local_enu(t["lat"], t["lon"], t.get("alt_baro", 0) * 0.3048)
                    slant_range = math.sqrt(x**2 + y**2 + z**2)
                    
                    if slant_range <= config["hazard_zone"]["max_nohd_meters"]:
                        az, el = enu_to_spherical(x, y, z)
                        uv = spherical_to_texture_uv(az, el, config["projector"]["azimuth"], 
                                                     config["projector"]["h_fov"], 
                                                     config["projector"]["v_fov"])
                        
                        if uv:
                            in_zone = True
                            if args.mode == 'mask':
                                u, v = uv
                                p = 0.05
                                osc_client.send_message("/surfaces/Dynamic_Blanking_Zone/quad/point_1_x", max(0.0, u - p))
                                # (Additional coordinate mapping omitted for readability here)
                                osc_client.send_message("/surfaces/Dynamic_Blanking_Zone/visible", 1)
                                logging.info("Hazard detected: Mask active.")
                            break
                
        if not in_zone:
            osc_client.send_message("/surfaces/Dynamic_Blanking_Zone/visible", 0)
            
    except Exception as e:
        logging.error(f"Loop error: {e}")

    # --- WATCHDOG & FAILSAFE ---
    if (current_time - last_valid_data_time > config["system"]["watchdog_timeout"]) or (in_zone and args.mode == 'kill'):
        osc_client.send_message("/master_opacity", 0.0)
        if in_zone: logging.warning("Safety Interlock: Master Opacity Killed.")
    else:
        osc_client.send_message("/master_opacity", 1.0)
        
    time.sleep(0.05)