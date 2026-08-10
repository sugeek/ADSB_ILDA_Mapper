import math
import logging
import json
import threading
import paho.mqtt.client as mqtt

class DroneTracker:
    def __init__(self, config):
        self.config = config
        
        # Pull MQTT network targets from the config file
        self.mqtt_host = config["system"].get("mqtt_broker_ip", "127.0.0.1")
        self.mqtt_port = config["system"].get("mqtt_broker_port", 1883)
        self.mqtt_topic = config["system"].get("mqtt_topic", "telemetry/#")
        
        # Projector origin reference for ENU coordinate scaling
        ref = config["projector"]
        self.ref_lat = ref["lat"]
        self.ref_lon = ref["lon"]
        self.ref_alt = ref["alt_m"]
        self.max_nohd = config["hazard_zone"]["max_nohd_meters"]

        # Thread-safe storage for tracking active aircraft data across threads
        self.lock = threading.Lock()
        self.active_drones = {}  # Format: { "drone_id_string": {processed_drone_dict} }

        # Initialize and configure the non-blocking background MQTT client
        self.mqtt_client = mqtt.Client()
        self.mqtt_client.on_connect = self._on_connect
        self.mqtt_client.on_message = self._on_message
        
        logging.info(f"Connecting to local MQTT Broker at {self.mqtt_host}:{self.mqtt_port}")
        self.mqtt_client.connect(self.mqtt_host, self.mqtt_port, 60)
        
        # Start the background thread loop to handle network traffic automatically
        self.mqtt_client.loop_start()

    def _on_connect(self, client, userdata, flags, rc):
        """Callback automatically triggered when connecting to the local broker switch."""
        if rc == 0:
            logging.info(f"MQTT Connected successfully. Subscribing to: {self.mqtt_topic}")
            client.subscribe(self.mqtt_topic)
        else:
            logging.error(f"MQTT Connection failed with return code: {rc}")

    def _on_message(self, client, userdata, message):
        """Callback triggered instantly whenever an unencrypted JSON drone packet lands."""
        try:
            payload = message.payload.decode("utf-8")
            drone_data = json.loads(payload)
            
            # Map standard network gateway keys (handles common variants like lat vs latitude)
            lat = drone_data.get("lat") or drone_data.get("latitude")
            lon = drone_data.get("lon") or drone_data.get("longitude")
            alt_m = drone_data.get("alt_msl") or drone_data.get("altitude") or 0
            drone_id = drone_data.get("id") or drone_data.get("drone_id") or "unknown"

            if lat is not None and lon is not None:
                # Transform coordinates to Cartesian space relative to the projector head
                dx, dy, dz = self.wgs84_to_local_enu(float(lat), float(lon), float(alt_m))
                drone_range = math.sqrt(dx**2 + dy**2 + dz**2)
                is_hazard = drone_range <= self.max_nohd

                if is_hazard:
                    logging.warning(f"MQTT Remote ID Hazard: UAV [{drone_id}] at {drone_range:.1f}m")

                # Store the updated state safely in memory using an atomic thread lock
                with self.lock:
                    self.active_drones[drone_id] = {
                        "id": drone_id,
                        "x": dx, "y": dy, "z": dz,
                        "range": drone_range,
                        "hazard": is_hazard,
                        "topic": message.topic  # Allows filtering controlled vs uncontrolled
                    }
        except Exception as e:
            logging.debug(f"Failed to parse incoming MQTT payload packet: {e}")

    def wgs84_to_local_enu(self, lat, lon, alt_m):
        """Converts global WGS84 coordinates to local Cartesian (ENU) meters."""
        ref_lat_rad = math.radians(self.ref_lat)
        x_east = (lon - self.ref_lon) * 111320.0 * math.cos(ref_lat_rad)
        y_north = (lat - self.ref_lat) * 110574.0
        z_up = alt_m - self.ref_alt
        return x_east, y_north, z_up

    def get_current_hazards(self):
        """
        Replaces the old poll method. Queries the thread-safe active states in memory 
        without executing blocking HTTP requests.
        """
        with self.lock:
            processed_drones = list(self.active_drones.values())
            
        hazards_found = any(drone["hazard"] for drone in processed_drones)
        return hazards_found, processed_drones

    def close(self):
        """Clean disconnect function to safely stop background network operations."""
        self.mqtt_client.loop_stop()
        self.mqtt_client.disconnect()
        logging.info("MQTT tracking network pipeline shut down smoothly.")
