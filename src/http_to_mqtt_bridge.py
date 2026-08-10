import time
import json
import logging
import requests
import paho.mqtt.client as mqtt

# 1. Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# 2. Local fallback configuration (Overridden by main show config if needed)
DUMP1090_URL = "http://127.0.0"
MQTT_HOST = "192.168.1.50"  # Match your local network switch broker IP
MQTT_PORT = 1883
BASE_TOPIC = "telemetry/adsb"
POLL_INTERVAL = 1.0  # dump1090 typically updates its internal json cache at 1Hz

def main():
    # 3. Initialize and connect the MQTT publisher client
    client = mqtt.Client()
    logging.info(f"Connecting bridge to MQTT broker at {MQTT_HOST}:{MQTT_PORT}...")
    try:
        client.connect(MQTT_HOST, MQTT_PORT, 60)
        client.loop_start()
    except Exception as e:
        logging.critical(f"Could not connect to MQTT broker: {e}")
        return

    logging.info(f"Bridge active. Polling dump1090 at {DUMP1090_URL} every {POLL_INTERVAL}s...")

    while True:
        try:
            # 4. Fetch the latest aircraft state from dump1090 web server
            response = requests.get(DUMP1090_URL, timeout=0.5)
            if response.status_code != 200:
                logging.warning(f"Unexpected HTTP status from dump1090: {response.status_code}")
                time.sleep(POLL_INTERVAL)
                continue
                
            data = response.json()
            aircraft_list = data.get("aircraft", [])
            
            # 5. Process each active transponder frame in the sky
            for aircraft in aircraft_list:
                hex_id = aircraft.get("hex")  # Unique ICAO 24-bit address string
                lat = aircraft.get("lat")
                lon = aircraft.get("lon")
                
                # We can only track and map positions if coordinates are valid
                if hex_id and lat is not None and lon is not None:
                    # Extract metric transformations
                    alt_feet = aircraft.get("altitude", 0)
                    
                    # Convert feet to meters to match your project's ENU standard geometry
                    # $1 \text{ foot} = 0.3048 \text{ meters}$
                    alt_m = float(alt_feet) * 0.3048
                    
                    # Build a structured, normalized telemetry payload
                    payload = {
                        "id": hex_id,
                        "flight": aircraft.get("flight", "UNKNOWN").strip(),
                        "lat": float(lat),
                        "lon": float(lon),
                        "alt_msl": alt_m,
                        "speed_kt": aircraft.get("speed", 0),
                        "heading": aircraft.get("track", 0),
                        "seen_seconds_ago": aircraft.get("seen", 0)
                    }
                    
                    # 6. Publish to a cleanly isolated, individual aircraft sub-topic
                    target_topic = f"{BASE_TOPIC}/{hex_id}/position"
                    client.publish(target_topic, json.dumps(payload), qos=0)
                    logging.debug(f"Published ADSB -> {target_topic}")
                    
        except requests.exceptions.RequestException as re:
            logging.error(f"Failed to fetch data from dump1090 instance: {re}")
        except Exception as e:
            logging.error(f"Error executing telemetry bridge loop: {e}")
            
        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    main()
