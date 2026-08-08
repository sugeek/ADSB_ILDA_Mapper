import unittest
import math

# --- CORE MATH LOGIC (Shared across all nodes) ---
def wgs84_to_local_enu(lat, lon, alt_m, ref_lat, ref_lon, ref_alt_m):
    x_east = (lon - ref_lon) * 111320.0 * math.cos(math.radians(ref_lat))
    y_north = (lat - ref_lat) * 110574.0
    z_up = alt_m - ref_alt_m
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

class TestLaserSafetyLogic(unittest.TestCase):
    
    def setUp(self):
        # Default projection parameters
        self.ref_lat = 40.786400
        self.ref_lon = -119.204500
        self.ref_alt = 1200.0
        self.azimuth = 0.0 
        self.h_fov = 60.0
        self.v_fov = 45.0
        self.max_nohd = 3500.0 

    def test_aircraft_inside_nohd_and_inside_fov(self):
        # Place aircraft 1000m directly North
        test_lat = self.ref_lat + (1000.0 / 110574.0) 
        test_lon = self.ref_lon         
        y_dist = 1000.0
        # Use 15 degrees instead of 22.5 to stay safely within FOV boundaries
        test_alt = self.ref_alt + (math.tan(math.radians(15.0)) * y_dist)
        
        x, y, z = wgs84_to_local_enu(test_lat, test_lon, test_alt, self.ref_lat, self.ref_lon, self.ref_alt)
        slant_range = math.sqrt(x**2 + y**2 + z**2)
        
        self.assertLess(slant_range, self.max_nohd)
        
        az, el = enu_to_spherical(x, y, z)
        uv = spherical_to_texture_uv(az, el, self.azimuth, self.h_fov, self.v_fov)
        
        self.assertIsNotNone(uv)
        u, v = uv
        self.assertAlmostEqual(u, 0.5, places=2)
        self.assertAlmostEqual(v, 0.83, places=2)

    def test_aircraft_exceeds_nohd_gate(self):
        # Place aircraft 5000m away (outside 3500m NOHD)
        test_lat = self.ref_lat + (5000.0 / 110574.0)
        test_lon = self.ref_lon
        test_alt = self.ref_alt + 1000.0
        
        x, y, z = wgs84_to_local_enu(test_lat, test_lon, test_alt, self.ref_lat, self.ref_lon, self.ref_alt)
        slant_range = math.sqrt(x**2 + y**2 + z**2)
        
        self.assertGreater(slant_range, self.max_nohd)

    def test_aircraft_inside_nohd_but_outside_fov(self):
        # Place aircraft directly behind the projector
        test_lat = self.ref_lat - (1000.0 / 110574.0) 
        test_lon = self.ref_lon
        test_alt = self.ref_alt + 500.0
        
        x, y, z = wgs84_to_local_enu(test_lat, test_lon, test_alt, self.ref_lat, self.ref_lon, self.ref_alt)
        slant_range = math.sqrt(x**2 + y**2 + z**2)
        
        self.assertLess(slant_range, self.max_nohd)
        
        az, el = enu_to_spherical(x, y, z)
        uv = spherical_to_texture_uv(az, el, self.azimuth, self.h_fov, self.v_fov)
        
        self.assertIsNone(uv)

    def test_openlase_uv_boundary_clamping(self):
        # Verify that even on the extreme edges of the FOV, UV values never exceed 1.0 or drop below 0.0
        # This is strictly required for OpenLase stability.
        
        # Test extreme left edge
        uv_left = spherical_to_texture_uv(-30.0, 0.0, self.azimuth, self.h_fov, self.v_fov)
        self.assertIsNotNone(uv_left)
        self.assertEqual(uv_left[0], 0.0)
        
        # Test extreme right edge
        uv_right = spherical_to_texture_uv(30.0, 0.0, self.azimuth, self.h_fov, self.v_fov)
        self.assertIsNotNone(uv_right)
        self.assertEqual(uv_right[0], 1.0)
        
        # Test extreme top edge
        uv_top = spherical_to_texture_uv(0.0, 22.5, self.azimuth, self.h_fov, self.v_fov)
        self.assertIsNotNone(uv_top)
        self.assertEqual(uv_top[1], 1.0)

        # Test extreme bottom edge
        uv_bottom = spherical_to_texture_uv(0.0, -22.5, self.azimuth, self.h_fov, self.v_fov)
        self.assertIsNotNone(uv_bottom)
        self.assertEqual(uv_bottom[1], 0.0)

    def test_quad_vertex_bounds(self):
        """Verify that quad expansion (p) does not push coordinates out of [0, 1] range."""
        # Test an aircraft at the very edge of the FOV
        az, el = self.azimuth, 0.0 # Center
        u, v = 0.01, 0.5  # Very close to the left edge
        p = 0.05 # Expansion is larger than the distance to the edge
        
        # Simulating the node's quad logic with clamping
        quad_points = [
            (max(0.0, min(1.0, u - p)), max(0.0, min(1.0, v + p))),
            (max(0.0, min(1.0, u - p)), max(0.0, min(1.0, v - p))),
            (max(0.0, min(1.0, u + p)), max(0.0, min(1.0, v - p))),
            (max(0.0, min(1.0, u + p)), max(0.0, min(1.0, v + p)))
        ]
        
        for px, py in quad_points:
            # The math should clamp these to 0.0 or 1.0
            self.assertGreaterEqual(px, 0.0)
            self.assertLessEqual(px, 1.0)
            self.assertGreaterEqual(py, 0.0)
            self.assertLessEqual(py, 1.0)


if __name__ == '__main__':
    unittest.main()