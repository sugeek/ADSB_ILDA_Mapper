import math
import time
import requests
import argparse
import json
import sys
import logging
import os
import threading  # Added for architectural hardening
from pythonosc import udp_client

# --- LOGGING SETUP ---
# Ensures all events are captured for audit trails and post-incident analysis
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler("logs/safety_system.log"), 
        logging.StreamHandler()
    ]
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
                    help="Choose 'mask' for dynamic quad blanking or 'shutdown' for master blackout.")
args = parser.parse_args()

# --- OSC CLIENT SETUP ---
try:
    osc_client = udp_client.SimpleUDPClient(
        config["osc_targets"]["madmapper"]["ip"], 
        config["osc_targets"]["madmapper"]["port"]
    )
except Exception as e:
    logging.critical(f"Failed to initialize OSC client: {e}")
    sys.exit(1)

# --- GLOBAL STATE & LOCKING ---
# The lock ensures that if we move to a multi-threaded/Pub-Sub model, 
# the 'in_zone' and 'last_valid_data_time' updates are atomic.
state_lock = threading.Lock()
last_valid_data_time = time.time()

# --- MATH FUNCTIONS ---

def wgs84_to_local_enu(lat, lon, alt_m):
    """Converts global WGS84 coordinates to local Cartesian (ENU) meters."""
    ref = config["projector"]
    # Earth radius approximation constants
    x_east = (lon - ref["lon"]) * 111320.0 * math.cos(math.radians(ref["lat"]))
    y_north = (lat - ref["lat"]) * 110574.0
    z_up = alt_m - ref["alt_m"]
    return x_east, y_north, z_up

def enu_to_spherical(x, y, z):
    """Converts local Cartesian coordinates to Azimuth and Elevation."""
    h_dist = math.sqrt(x**2 + y**2)
    azimuth = (math.degrees(math.atan2(x, y)) + 360) % 360
    elevation = math.degrees(math.atan2(z, h_dist))
    return azimuth, elevation

def spherical_to_texture_uv(az, el, p_az, h_fov, v_fov):
    """Maps Spherical coordinates to MadMapper UV texture space (0.0 - 1.0)."""
    # Calculate angular offset from projector center
    az_diff = (az - p_az + 540) % 360 - 180
    
    # If target is outside the optical Field of View, return None to prevent edge-case errors
    if abs(az_diff) > (h_fov / 2.0) or abs(el) > (v_fov / 2.0):
        return None
        
    u = (az_diff + (h_fov / 2.0)) / h_fov
    v = (el + (v_fov / 2.0)) / v_fov
    
    # Clamp to [0, 1] to prevent texture coordinate overflow/underflow
    return max(0.0, min(1.0, u)), max(0.0, min(1.0, v))

# --- MAIN SAFETY LOOP ---
logging.info(f"Starting MadMapper Safety Node. Mode: {args.mode.upper()}")

while True:
    current_time = time.time()
    in_zone = False  # Local loop variable for the current frame
    current_uv = None # Store UV to use outside the lock
    
    try:
        # 1. Fetch Telemetry (Synchronous for now, but protected by lock in logic below)
        response = requests.get(config["system"]["adsb_url"], timeout=0.5)
        data = response.json().get("aircraft", [])
        
        if data:
            # CRITICAL SECTION: Protect the shared timestamp and target processing
            with state_lock:
                last_valid_data_time = current_time
                
                for t in data:
                    if "lat" in t:
                        # Convert altitude from feet to meters (0.3048)
                        x, y, z = w_en_u = wgs84_to_local_enu(t["lat"], t["lon"], t.get("alt_baro", 0) * 0.3048)
                        slant_range = math.sqrt(x**2 + y**2 + z**2)
                        
                        # Check if aircraft is within the hazard perimeter
                        if slant_range <= config["hazard_zone"]["max_nohd_meters"]:
                            az, el = enu_to_spherical(x, y, z)
                            uv = spherical_to_texture_uv(
                                az, el, 
                                config["projector"]["azimuth"], 
                                config["projector"]["h_fov"], 
                                config["projector"]["v_fov"]
                            )
                            
                            if uv:
                                in_zone = True
                                current_uv = uv # Pass UV out of the lock for processing
                                break
            
            # 2. Actuation Logic (Processed outside the lock to avoid blocking other threads)
            if in_scope := (in_zone and current_uv is not None):
                u, v = current_uv
                if args.mode == 'mask':
                    # padding parameter (p) creates a bounding box area around the point
                    p = 0.05 
                    
                    # Define the 4 corners of the quad to ensure visible coverage area
                    quad_points = [
                        (u - p, v + p), # Top Left
                        (u - p, v - p), # Bottom Left
                        (u + p, v - p), # Bottom Right
                        (u + p, v + p)  # Top Right
                    ]
                    
                    # Iterate and send all 4 vertices to MadMapper via OSC
                    for i, (px, py) in enumerate(quad_points):
                        osc_client.send_message(f"/surfaces/Dynamic_Blanking_zone/quad/point_{i+1}_x", px)
                        osc_client.send_message(f"/surfaces/Dynamic_Blanking_zone/quad/point_{i+1}_y", py)
                    
                    # Enable the dynamic surface visibility
                    osc_client.send_message("/surfaces/Dynamic_Blanking_zone/visible", 1)
                    logging.warning(f"HAZARD DETECTED: Aircraft at {u:.2f}, {v:.2f}. Mask Active.")
                
                elif args.mode == 'kill':
                    # In 'kill' mode, we trigger the master blackout immediately
                    osc_client.send_message("/master_opacity", 0.0)
                    logging.critical("HAZARD DETECTED: Aircraft Breach - Master Opacity Killed.")
            
            elif not in_zone:
                # No aircraft detected; reset visibility and opacity to normal
                osc_client.send_message("/surfaces/Dynamic_Blanking_zone/visible", 0)
                osc_client.send_message("/master_opacity", 1.0)

    except Exception as e:
        logging.error(f"Runtime Loop Error: {e}")

    # --- WATCHDOG & FAILSAFE ---
    # Check if the telemetry heartbeat has expired
    with state_lock:
        watchdog_expired = (current_time - last_valid_data_time > config["system"]["watchdog_timeout"])

    if watchdog_expired:
        # Emergency action: Total darkness/Opacity Kill due to communication loss
        osc_client.send_message("/master_opacity", 0.0)
        logging.critical("SAFETY INTERLOCK: Telemetry Loss - Master Opacity Killed.")
    elif in_zone and args.mode == 'kill':
        # Redundant check for kill mode triggers
        osc_client.send_message("/master_opacity", 0.0)
        logging.critical("SAFETY INTERLOCK: Aircraft Breach - Master Opacity Killed.")
        
    # Maintain high-frequency cycle (20Hz) to ensure low-latency response
    time.sleep(0.05)
