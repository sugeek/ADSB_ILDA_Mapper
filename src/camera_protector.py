import math
import logging

class CameraProtector:
    def __init__(self, config):
        self.config = config
        self.ref = config["projector"]
        # Load static assets from config or fall back to defaults
        self.assets = config.get("static_assets", [
            {"name": "FOH_A_Camera", "lat": self.ref["lat"] + 0.00005, "lon": self.ref["lon"], "alt_m": self.ref["alt_m"] + 2.0, "radius_m": 3.0},
            {"name": "Stage_B_Camera", "lat": self.ref["lat"] - 0.00005, "lon": self.ref["lon"] + 0.00005, "alt_m": self.ref["alt_m"] + 1.5, "radius_m": 2.5}
        ])

    def wgs84_to_local_enu(self, lat, lon, alt_m):
        """Converts global WGS84 coordinates to local Cartesian (ENU) meters."""
        ref_lat_rad = math.radians(self.ref["lat"])
        x_east = (lon - self.ref["lon"]) * 111320.0 * math.cos(ref_lat_rad)
        y_north = (lat - self.ref["lat"]) * 110574.0
        z_up = alt_m - self.ref_alt_m if hasattr(self, 'ref_alt_m') else alt_m - self.ref["alt_m"]
        return x_east, y_north, z_up

    def check_asset_intersections(self, active_targets):
        """
        Evaluates a list of active target vectors (drones, aircraft, etc.) 
        against all static protected camera zones.
        """
        for asset in self.assets:
            ax, ay, az = self.wgs84_to_local_enu(asset["lat"], asset["lon"], asset["alt_m"])
            
            for target in active_targets:
                tx, ty, tz = target["x"], target["y"], target["z"]
                distance = math.sqrt((tx - ax)**2 + (ty - ay)**2 + (tz - az)**2)
                
                if distance <= asset["radius_m"]:
                    logging.warning(f"Asset Hazard: Target encroached on protected zone '{asset['name']}' ({distance:.1f}m)")
                    return True
        return False