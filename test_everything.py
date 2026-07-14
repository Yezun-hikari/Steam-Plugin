import os
import sys
import unittest
import struct
import math
import zlib
import json
from unittest.mock import patch, MagicMock

# Ensure we can import modules from the current directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from divoom_api import DivoomGalleryAPI

# Mock app.plugin_api for plugin.py imports during tests if not available
try:
    import app.plugin_api
except ImportError:
    import sys
    from types import ModuleType
    app_mod = ModuleType("app")
    plugin_api_mod = ModuleType("app.plugin_api")
    class PixooPluginBase:
        def __init__(self, config=None):
            self.config = config or {}
            self.running = False
            self.setup()
        def setup(self): pass
        def get_pixoo_instance(self):
            mock_pixoo = MagicMock()
            return mock_pixoo
        def get_playing_game(self): return None
        def release_screen(self): pass
    plugin_api_mod.PixooPluginBase = PixooPluginBase
    app_mod.plugin_api = plugin_api_mod
    sys.modules["app"] = app_mod
    sys.modules["app.plugin_api"] = plugin_api_mod

from plugin import SteamPlugin

class TestDivoomComprehensive(unittest.TestCase):
    def setUp(self):
        self.api = DivoomGalleryAPI()

    # ==========================================
    # 1. KEYWORD EXTRACTION & STEMMING TESTS
    # ==========================================
    def test_extract_logical_keywords_orcs(self):
        kw = self.api.extract_logical_keywords("Orcs Must Die! Deathtrap")
        # Must include cleaned full title, main title, compound, and singularized 'Orc'
        self.assertIn("Orcs Must Die Deathtrap", kw)
        self.assertIn("Orcs Must Die", kw)
        self.assertIn("Deathtrap", kw)
        self.assertIn("Orcs Deathtrap", kw)
        self.assertIn("Orcs", kw)
        self.assertIn("Orc", kw) # Singular form derived from Orcs
        # Must NOT include stop words alone
        self.assertNotIn("Must", kw)
        self.assertNotIn("Die", kw)

    def test_extract_logical_keywords_meccha_chameleon(self):
        kw = self.api.extract_logical_keywords("Meccha Camelion")
        self.assertIn("Meccha Camelion", kw)
        self.assertIn("Camelion", kw)
        self.assertIn("Meccha", kw)
        # Typo fallback rules
        self.assertIn("chameleon", kw)
        self.assertIn("mecha", kw)

    def test_extract_logical_keywords_hollow_knight(self):
        kw = self.api.extract_logical_keywords("Hollow Knight: Silksong")
        self.assertIn("Hollow Knight Silksong", kw)
        self.assertIn("Hollow Knight", kw)
        self.assertIn("Silksong", kw)
        self.assertIn("Hollow", kw)
        self.assertIn("Knight", kw)

    def test_extract_logical_keywords_empty_or_invalid(self):
        self.assertEqual(self.api.extract_logical_keywords(""), [])
        self.assertEqual(self.api.extract_logical_keywords(None), [])
        self.assertEqual(self.api.extract_logical_keywords(12345), [])

    # ==========================================
    # 2. SMART GALLERY SEARCH BEHAVIOR TESTS
    # ==========================================
    @patch.object(DivoomGalleryAPI, 'search_gallery')
    def test_smart_search_gallery_no_hot_fallback_when_zero_matches(self, mock_search):
        # When all keyword searches across 64, 32, 16 return 0 hits, must return empty list []
        mock_search.return_value = []
        results = self.api.smart_search_gallery("Unknown Game Title 123", size=64)
        self.assertEqual(results, [])

    @patch.object(DivoomGalleryAPI, 'search_gallery')
    def test_smart_search_gallery_size_fallback(self, mock_search):
        # Suppose search for 'Orc' returns items of size 32 (FileSize=2) when searching at size=64
        def side_effect(query, size=64, return_cnt=20):
            if query.lower() == "orc":
                return [{"FileId": "item_32_orc", "LikeCnt": 50, "FileName": "Orc Fighter", "FileSize": 2}]
            return []
        mock_search.side_effect = side_effect

        results = self.api.smart_search_gallery("Orcs Must Die! Deathtrap", size=64)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["FileId"], "item_32_orc")
        self.assertEqual(results[0]["pixel_size"], 2) # 64 / 32 = 2 scaling factor

    # ==========================================
    # 3. SPIL BINARY PARSING & DECODING TESTS
    # ==========================================
    def _create_synthetic_spil_bin(self, side=16, n_colors=4):
        # Create a synthetic SPIL binary with known little-endian LSB index pixel pattern and subframes
        palette_bytes = b"\xff\x00\x00\x00\xff\x00\x00\x00\xff\xff\xff\xff"[:n_colors*3]
        bit_width = max(1, math.ceil(math.log2(n_colors)))
        total_pixels = side * side

        indices = [1 if (i % side) == (i // side) else 0 for i in range(total_pixels)]

        # Pack indices into little-endian continuous bit stream for Frame 1
        bit_stream = bytearray((total_pixels * bit_width + 7) // 8)
        for i, idx in enumerate(indices):
            for bit_idx in range(bit_width):
                bit = (idx >> bit_idx) & 1
                byte_pos = (i * bit_width + bit_idx) // 8
                bit_in_byte = (i * bit_width + bit_idx) % 8
                if bit:
                    bit_stream[byte_pos] |= (1 << bit_in_byte)

        # Build Frame 1 payload: speed=300ms, n_colors, palette, raw_bits
        speed_bytes = struct.pack('<H', 300)
        n_col_bytes = struct.pack('<H', n_colors)
        frame_payload = len(bit_stream)
        
        # Two subframe headers to separate 3 frames (`raw[header_pos] == 0xAA` and `raw[next_pos] == 0xAA`)
        subframe_header = b'\xaa\x88' + struct.pack('<H', frame_payload + 4) + struct.pack('<H', 300) + struct.pack('<H', 0)
        full_payload = speed_bytes + n_col_bytes + palette_bytes + bytes(bit_stream) + subframe_header + bytes(bit_stream) + subframe_header + bytes(bit_stream)

        header = b'\x00' * 17 + b'\xaa\xc1' + struct.pack('<H', len(full_payload))
        return header + full_payload, indices

    def test_parse_spil_frame_exact_dimensions(self):
        for candidate_side in [8, 16, 32]:
            data, expected_indices = self._create_synthetic_spil_bin(side=candidate_side, n_colors=4)
            parsed = self.api._parse_spil_frame(data)
            self.assertIsNotNone(parsed, f"Failed to parse synthetic SPIL for side={candidate_side}")
            side, palette, indices = parsed
            self.assertEqual(side, candidate_side)
            self.assertEqual(len(palette), 4)
            self.assertEqual(indices[: side*side], expected_indices)

    def test_decode_image_and_png_bytes(self):
        data, _ = self._create_synthetic_spil_bin(side=16, n_colors=4)
        
        # 1. Test decode_image (PIL behavior if installed vs None if not)
        img = self.api.decode_image(data)
        try:
            import PIL
            self.assertIsNotNone(img)
            self.assertEqual(img.size, (16, 16))
            self.assertEqual(img.getpixel((0, 0))[:3], (0, 255, 0))
            self.assertEqual(img.getpixel((1, 0))[:3], (255, 0, 0))
        except ImportError:
            self.assertIsNone(img)

        # 2. Test decode_to_png_bytes (pure Python fallback verification generates exact 64x64 output)
        png_bytes = self.api.decode_to_png_bytes(data)
        self.assertIsNotNone(png_bytes)
        self.assertTrue(png_bytes.startswith(b'\x89PNG\r\n\x1a\n'))
        self.assertIn(b'IHDR', png_bytes)
        self.assertIn(b'IDAT', png_bytes)
        self.assertIn(b'IEND', png_bytes)
        import struct
        w, h = struct.unpack('>II', png_bytes[16:24])
        self.assertEqual((w, h), (64, 64))

    def test_parse_spil_frame_corrupt_or_short(self):
        self.assertIsNone(self.api._parse_spil_frame(b"short"))
        self.assertIsNone(self.api._parse_spil_frame(b"\x00"*30))

    def test_parse_spil_frame_advanced_fallbacks(self):
        data, expected_indices = self._create_synthetic_spil_bin(side=16, n_colors=4)
        
        # 1. Test SPIL frame with garbage prefix putting 0xAA at arbitrary offset (e.g. pos=45)
        shifted_data = b"\x00\x01\x02\x03" * 11 + data[data.find(b'\xaa'):]
        parsed = self.api._parse_spil_frame(shifted_data)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed[0], 16)
        
        # 2. Test zlib compressed SPIL binary via decode_image
        import zlib
        compressed = zlib.compress(data)
        img = self.api.decode_image(compressed)
        try:
            import PIL
            self.assertIsNotNone(img)
            self.assertEqual(img.size, (16, 16))
        except ImportError:
            pass

    def test_parse_spil_frame_bogus_prefix_rejection(self):
        # Create a real SPIL binary
        data, expected_indices = self._create_synthetic_spil_bin(side=16, n_colors=4)
        # Prepend a bogus 0xAA byte with invalid subframe ID and misleading length/speed bytes
        bogus_prefix = b'\xaa\x99' + struct.pack('<H', 100) + b'\x64\x00\x10\x00' + b'\x00' * 30
        full_data = bogus_prefix + data[17:] # Real header is at offset 17 in data
        parsed = self.api._parse_spil_frame(full_data)
        self.assertIsNotNone(parsed, "Failed to reject bogus prefix and parse true SPIL frame")
        self.assertEqual(parsed[0], 16)
        self.assertEqual(len(parsed[1]), 4)

    def test_parse_divoom_v3_container_sizes(self):
        # Test V3 container extraction for side=16 and side=32
        for row_count, expected_side in [(1, 16), (2, 32)]:
            pal_bytes = b'\xff\x00\x00' * 4
            indices_bytes = b'\x00\x01\x02\x03' * ((expected_side * expected_side * 2 + 7) // 8)
            data_payload = b'\x00\x00\x00\x00\x01\x0c\x04\x00' + pal_bytes + indices_bytes
            v3_header = b'\x00' * 5 + b'\x1a' + struct.pack('>BHBB', 1, 100, row_count, row_count) + struct.pack('>I', len(data_payload))
            parsed = self.api._parse_divoom_v3_container(v3_header + data_payload)
            self.assertIsNotNone(parsed)
            self.assertEqual(parsed[0], expected_side)

    def test_parse_divoom_v3_untiling(self):
        # Verify that 16x16 tiles are properly untiled into linear row-major scanline order
        # For a 32x32 image (row_count=2), tile 1 (indices 256..511) occupies top-right quadrant (x=16..31, y=0..15).
        row_count = 2
        pal_bytes = b'\xff\x00\x00' + b'\x00\xff\x00' + (b'\x00\x00\xff' * 254) # red, green, blue...
        # We construct 1024 raw indices where tile 0 is color 0 and tile 1 is color 1
        raw_idx = [0] * 256 + [1] * 256 + [0] * 512
        data_payload = b'\xaa\x00\x00\x00\x01\x0c\x00\x00' + pal_bytes + bytes(raw_idx)
        v3_header = b'\x1a' + struct.pack('>BHBB', 1, 100, row_count, row_count) + struct.pack('>I', len(data_payload))
        parsed = self.api._parse_divoom_v3_container(v3_header + data_payload)
        self.assertIsNotNone(parsed)
        side, pal, indices = parsed
        self.assertEqual(side, 32)
        # Linear check: (x=16, y=0) must come from tile 1 (color index 1)
        self.assertEqual(indices[0 * side + 16], 1)
        # Linear check: (x=0, y=0) must come from tile 0 (color index 0)
        self.assertEqual(indices[0 * side + 0], 0)

    # ==========================================
    # 4. STEAM PLUGIN COMPREHENSIVE TESTS
    # ==========================================
    def test_plugin_setup_and_missing_credentials(self):
        config = {"steam_api_key": "", "steam_id": ""}
        plugin = SteamPlugin(config=config)
        self.assertEqual(plugin.art_list, [])
        self.assertIsNotNone(plugin.divoom_api)

    @patch('urllib.request.urlopen')
    def test_get_playing_game_success(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            "response": {
                "players": [
                    {"gameextrainfo": "Elden Ring"}
                ]
            }
        }).encode('utf-8')
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        plugin = SteamPlugin(config={"steam_api_key": "fake_key", "steam_id": "fake_id"})
        self.assertEqual(plugin.get_playing_game(), "Elden Ring")

    @patch('urllib.request.urlopen')
    def test_get_playing_game_error(self, mock_urlopen):
        mock_urlopen.side_effect = Exception("Network timeout")
        plugin = SteamPlugin(config={"steam_api_key": "fake_key", "steam_id": "fake_id"})
        self.assertIsNone(plugin.get_playing_game())


    @patch('urllib.request.urlopen')
    def test_plugin_download_and_process_art_spil_binary(self, mock_urlopen):
        data, _ = self._create_synthetic_spil_bin(side=32, n_colors=4)
        mock_response = MagicMock()
        mock_response.read.return_value = data
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        config = {"min_likes": 1}
        plugin = SteamPlugin(config=config)
        
        art_item = {
            "title": "Synthetic Art",
            "url": "http://fake-cdn/image.bin",
            "pixel_size": 2
        }
        
        success = plugin.download_and_process_art(art_item)
        self.assertTrue(success)
        self.assertTrue(os.path.exists(plugin.current_art_path))
        try:
            from PIL import Image
            with Image.open(plugin.current_art_path) as img:
                self.assertEqual(img.size, (64, 64))
        except ImportError:
            pass

    @patch.object(DivoomGalleryAPI, 'search_gallery')
    def test_smart_search_gallery_tag_success(self, mock_search):
        mock_search.return_value = [{
            "FileId": "t1_fake",
            "FileName": "Orcs Must Die Battle",
            "LikeCnt": 150,
            "FileSize": 4
        }]
        results = self.api.smart_search_gallery("Orcs Must Die! Deathtrap", size=64, return_cnt=5)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["FileId"], "t1_fake")
        self.assertEqual(results[0]["pixel_size"], 1)

    @patch.object(DivoomGalleryAPI, '_post')
    def test_login_success_and_search_gallery_headers(self, mock_post):
        mock_post.return_value = {"ReturnCode": 0, "Token": "test_tok_123", "UserId": 998877}
        self.api.login(email="test@example.com", password="secret_password")
        self.assertEqual(self.api.token, "test_tok_123")
        self.assertEqual(self.api.user_id, 998877)

        mock_post.return_value = {"ReturnCode": 0, "FileList": [{"FileId": "f1", "FileName": "Orc Item"}]}
        res = self.api.search_gallery("Orc", size=64)
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["DownloadUrl"], "https://f.divoom-gz.com/f1")

    def test_decode_to_png_bytes_invalid_input(self):
        self.assertIsNone(self.api.decode_to_png_bytes(b"not a valid spil or png"))
        self.assertIsNone(self.api.decode_to_png_bytes(b"\x00\x01\x02"))

    def test_extract_logical_keywords_complex_punctuation(self):
        keywords = self.api.extract_logical_keywords("Counter-Strike: Global Offensive™ 2")
        self.assertIn("Counter Strike Global Offensive 2", keywords)
        self.assertIn("Counter Strike", keywords)
        self.assertIn("Counter", keywords)

class TestPreCommitVersionBump(unittest.TestCase):
    def test_plugin_json_version(self):
        plugin_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "plugin.json")
        self.assertTrue(os.path.exists(plugin_path))
        with open(plugin_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertIn("version", data)
        # Verify semver format
        parts = data["version"].split(".")
        self.assertEqual(len(parts), 3)
        for part in parts:
            self.assertTrue(part.isdigit())

    def test_pre_commit_hooks_exist_and_executable(self):
        root_dir = os.path.dirname(os.path.abspath(__file__))
        for hook_path in [
            os.path.join(root_dir, ".githooks", "pre-commit"),
            os.path.join(root_dir, ".git", "hooks", "pre-commit")
        ]:
            if os.path.exists(hook_path):
                self.assertTrue(os.access(hook_path, os.X_OK), f"{hook_path} is not executable")

    def test_version_increment_regex_logic(self):
        import re
        sample_json = '{\n    "name": "Steam Pixel Art",\n    "version": "1.0.9",\n    "author": "Tim"\n}'
        match = re.search(r'"version"\s*:\s*"(\d+)\.(\d+)\.(\d+)"', sample_json)
        self.assertIsNotNone(match)
        major, minor, patch = int(match.group(1)), int(match.group(2)), int(match.group(3))
        new_version = f"{major}.{minor}.{patch + 1}"
        new_content = sample_json[:match.start(1)] + new_version + sample_json[match.end(3):]
        self.assertIn('"version": "1.0.10"', new_content)
        self.assertTrue(new_content.startswith('{\n    "name": "Steam Pixel Art",\n'))

if __name__ == '__main__':
    unittest.main(verbosity=2)

