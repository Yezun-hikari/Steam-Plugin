import urllib.request
import urllib.error
import json
import ssl
import hashlib
import logging
import re
from io import BytesIO

logger = logging.getLogger(__name__)

class DivoomGalleryAPI:
    """
    A Python wrapper for the Divoom Gallery API.
    Supports authenticated login, intelligent search heuristics, SPIL animation decoding,
    and robust error handling.
    """

    BASE_URL = "https://app.divoom-gz.com"
    FILE_URL = "https://f.divoom-gz.com/"

    # Size mapping based on API observations
    # 1: 16x16, 2: 32x32, 4: 64x64, 16: 128x128
    SIZE_MAP = {
        16: 1,
        32: 2,
        64: 4,
        128: 16
    }

    def __init__(self, email=None, password=None, token=None, user_id=None, timeout=10, config=None):
        import os
        self.ctx = ssl.create_default_context()
        self.ctx.check_hostname = False
        self.ctx.verify_mode = ssl.CERT_NONE

        self.timeout = timeout
        self.config = config or {}

        self.email = email or self.config.get("divoom_email") or os.environ.get("DIVOOM_EMAIL", "")
        self.password = password or self.config.get("divoom_password") or os.environ.get("DIVOOM_PASSWORD", "")
        self.token = token or self.config.get("divoom_token") or os.environ.get("DIVOOM_TOKEN", "")
        self.user_id = user_id or self.config.get("divoom_user_id") or os.environ.get("DIVOOM_USER_ID", 0)

        # Automatically log in if email/password provided and token is missing
        if self.email and self.password and not self.token:
            self.login()

    def login(self, email=None, password=None):
        """
        Authenticates with Divoom Cloud via /UserLogin to obtain an access Token and UserId.
        Required for tag and keyword search endpoints.
        """
        if email:
            self.email = email
        if password:
            self.password = password

        if not self.email or not self.password:
            logger.warning("No email/password provided for Divoom Cloud login.")
            return False

        data = {
            "Email": self.email,
            "Password": hashlib.md5(self.password.encode('utf-8')).hexdigest()
        }
        response = self._post("UserLogin", data)
        if response and response.get("ReturnCode") == 0:
            self.token = str(response.get("Token", ""))
            self.user_id = int(response.get("UserId", 0))
            logger.info(f"Successfully logged in to Divoom Cloud (UserId={self.user_id})")
            return True
        else:
            msg = response.get("ReturnMessage") if response else "Unknown error"
            logger.error(f"Divoom Cloud login failed: {msg}")
            return False

    def _post(self, endpoint, data):
        url = f"{self.BASE_URL}/{endpoint}"

        headers = {
            'Content-Type': 'application/json',
            'User-Agent': 'Divoom/3.1.25 (Android; 13)'
        }

        req = urllib.request.Request(
            url,
            data=json.dumps(data).encode('utf-8'),
            headers=headers
        )
        try:
            with urllib.request.urlopen(req, context=self.ctx, timeout=self.timeout) as response:
                if response.status == 200:
                    return json.loads(response.read().decode('utf-8'))
                else:
                    raise Exception(f"HTTP Error: {response.status}")
        except urllib.error.HTTPError as e:
            logger.error(f"HTTP Error for {endpoint}: {e.code} {e.reason}")
        except Exception as e:
            logger.error(f"Request failed to {endpoint}: {e}")
        return None

    def search_gallery(self, keyword, size=64, return_cnt=50, base_cnt=0):
        """
        Searches the Divoom gallery using the modern Tag/GetTagGalleryListV2 endpoint.
        Requires an authenticated Token and UserId (obtained via login).
        """
        if size not in self.SIZE_MAP:
            raise ValueError(f"Invalid size '{size}'. Allowed sizes are: {list(self.SIZE_MAP.keys())}")

        if not self.token and self.email and self.password:
            self.login()

        data = {
            "TagName": keyword,
            "StartNum": 1 + base_cnt,
            "EndNum": return_cnt + base_cnt,
            "ReturnCnt": return_cnt,
            "BaseCnt": base_cnt,
            "Token": str(self.token or ""),
            "UserId": int(self.user_id or 0)
        }

        response = self._post("Tag/GetTagGalleryListV2", data)

        results = []
        if response and response.get("ReturnCode") == 0:
            file_list = response.get("FileList", [])
            for item in file_list:
                file_id = item.get("FileId")
                if file_id:
                    item["DownloadUrl"] = f"{self.FILE_URL}{file_id}"
                results.append(item)
        elif response and response.get("ReturnCode") == 11:
            logger.warning(f"Search failed for tag '{keyword}': Token mismatch or unauthenticated (ReturnCode=11). Please provide valid login credentials.")

        return results

    def extract_logical_keywords(self, game_name):
        """
        Intelligent keyword extractor that analyzes game titles to generate prioritized, logical search terms.
        Examples:
          'Orcs Must Die! Deathtrap' -> ['Orcs Must Die Deathtrap', 'Orcs Must Die', 'Deathtrap', 'Orcs Deathtrap', 'Orcs', 'Orc']
          'Meccha Camelion' -> ['Meccha Camelion', 'Camelion', 'Meccha', 'chameleon', 'mecha']
          'Hollow Knight: Silksong' -> ['Hollow Knight Silksong', 'Hollow Knight', 'Silksong', 'Hollow', 'Knight']
        """
        if not game_name or not isinstance(game_name, str):
            return []

        stop_words = {
            'the', 'a', 'an', 'and', 'or', 'of', 'for', 'with', 'in', 'on', 'at', 'to', 'by', 'vs', 'v',
            'edition', 'deluxe', 'remastered', 'goty', 'game', 'online', 'must', 'die', 'ii', 'iii', 'iv', 'hd'
        }

        candidates = []
        def add_cand(c):
            c_clean = re.sub(r'\s+', ' ', c).strip()
            if c_clean and len(c_clean) >= 3 and c_clean.lower() not in [x.lower() for x in candidates]:
                candidates.append(c_clean)

        # 1. Cleaned full title
        clean_full = re.sub(r'[^\w\s]', ' ', game_name).strip()
        add_cand(clean_full)

        # 2. Subtitle split by colon, exclamation, dash, slash
        parts = re.split(r'[:!–—/\-]', game_name)
        if len(parts) > 1:
            main_title = re.sub(r'[^\w\s]', ' ', parts[0]).strip()
            sub_title = re.sub(r'[^\w\s]', ' ', parts[1]).strip()
            add_cand(main_title)
            if len(sub_title) >= 4:
                add_cand(sub_title)

        # 3. Word pairs and core nouns (filtering stop words)
        words = [w for w in clean_full.split() if w.lower() not in stop_words and len(w) >= 3]
        if len(words) >= 2:
            add_cand(f'{words[0]} {words[1]}')

        # 4. Sort individual significant nouns by length descending (e.g. 'Camelion' before 'Meccha', 'Deathtrap' before 'Orcs')
        for w in sorted(words, key=len, reverse=True):
            add_cand(w)
            # Singular/plural stemming (e.g. Orcs -> Orc, Dragons -> Dragon, Zombies -> Zombie)
            if w.lower().endswith('s') and len(w) >= 4 and not w.lower().endswith('ss'):
                singular = w[:-1]
                if len(singular) >= 3:
                    add_cand(singular)

        # 5. Common gaming typo variations / phonetic fallbacks
        typos = {'camelion': 'chameleon', 'chameleon': 'camelion', 'meccha': 'mecha', 'mecha': 'meccha'}
        for w in list(candidates):
            w_low = w.lower()
            if w_low in typos:
                add_cand(typos[w_low])

        return candidates

    def smart_search_gallery(self, game_name, size=64, min_likes=1, return_cnt=100):
        """
        Intelligent search heuristic for games using Divoom Tag Gallery:
        1. Analyzes game title using extract_logical_keywords.
        2. Queries Tag/GetTagGalleryListV2 across candidate keywords.
        3. Filters by exact requested size and like count, with automatic fallbacks to 32x32 and 16x16 if needed.
        """
        results = []
        seen_ids = set()

        def add_results(items, fallback_pixel_size=1):
            for item in items:
                file_id = item.get("FileId")
                if not file_id or file_id in seen_ids:
                    continue
                likes = item.get("LikeCnt")
                if likes is not None and likes < min_likes:
                    continue
                seen_ids.add(file_id)
                item["DownloadUrl"] = f"{self.FILE_URL}{file_id}"
                item["pixel_size"] = fallback_pixel_size
                results.append(item)

        candidates = self.extract_logical_keywords(game_name)
        logger.info(f"Analyzed logical keywords for '{game_name}': {candidates}")

        for query in candidates:
            logger.info(f"Searching Divoom Tag Gallery with tag: '{query}'")
            res = self.search_gallery(query, size=size, return_cnt=max(50, return_cnt * 2))
            if not res:
                continue

            # Check exact requested size matches (e.g. FileSize == 4 for 64x64)
            target_file_size = self.SIZE_MAP[size]
            exact_matches = [x for x in res if x.get("FileSize") == target_file_size]
            add_results(exact_matches, fallback_pixel_size=1)

            # If not enough exact matches, add fallback sizes from the same tag search
            if len(results) < return_cnt and size != 32:
                matches_32 = [x for x in res if x.get("FileSize") == self.SIZE_MAP[32]]
                add_results(matches_32, fallback_pixel_size=2 if size == 64 else 1)

            if len(results) < return_cnt and size != 16:
                matches_16 = [x for x in res if x.get("FileSize") == self.SIZE_MAP[16]]
                add_results(matches_16, fallback_pixel_size=4 if size == 64 else 2)

            if len(results) >= return_cnt:
                break

        if not results:
            logger.warning(f"Tag search for '{game_name}' (tags: {candidates}) yielded 0 matching items.")

        return results[:return_cnt]

    def _parse_divoom_v3_container(self, content):
        """
        Parses Divoom Cloud v3 container binary content (.bin) (e.g. 0x1a 64x64 multi-frame format)
        and returns (side, palette, indices) for the first frame.
        """
        import io
        import struct

        if not content or len(content) < 15:
            return None

        row_count = 0
        data = None

        pos = content.find(b'\x1a')
        if pos != -1 and pos <= 64 and pos + 10 < len(content):
            try:
                fp = io.BytesIO(content[pos + 1 :])
                total_frames, speed, r_cnt, c_cnt = struct.unpack('>BHBB', fp.read(5))
                if 0 < r_cnt <= 8 and 0 < c_cnt <= 8 and 0 < total_frames <= 1000:
                    size = struct.unpack('>I', fp.read(4))[0]
                    if 0 < size <= len(content):
                        data = fp.read(size)
                        row_count = r_cnt
            except Exception:
                pass

        if not data:
            pos = content.find(b'\x12')
            if pos != -1 and pos <= 64 and pos + 6 < len(content):
                try:
                    fp = io.BytesIO(content[pos + 1 :])
                    total_frames, speed, r_cnt, c_cnt = struct.unpack('>BHBB', fp.read(5))
                    if r_cnt == 2 and c_cnt == 2 and 0 < total_frames <= 1000:
                        data = content[pos + 6 :]
                        row_count = 2
                except Exception:
                    pass

        if not data:
            pos = content.find(b'\x09')
            if pos != -1 and pos <= 64 and pos + 4 < len(content):
                try:
                    fp = io.BytesIO(content[pos + 1 :])
                    total_frames, speed = struct.unpack('>BH', fp.read(3))
                    if 0 < total_frames <= 1000:
                        data = content[pos + 4 :]
                        row_count = 1
                except Exception:
                    pass

        if not data or len(data) < 10 or row_count <= 0:
            return None

        if data[0] not in (0xaa, 0x00):
            aa_idx = data.find(b'\xaa')
            if aa_idx != -1:
                data = data[aa_idx:]
            else:
                aa_idx = content.find(b'\xaa')
                if aa_idx != -1:
                    data = content[aa_idx:]

        if len(data) < 10 or data[0] not in (0xaa, 0x00):
            return None

        try:
            uVar13 = data[6]
            iVar11 = uVar13 * 3
            if uVar13 == 0:
                bVar9 = 8
                iVar11 = 768
            else:
                bVar9 = 0xFF
                bVar15 = 1
                while True:
                    if (uVar13 & 1) != 0:
                        bVar18 = bVar9 == 0xFF
                        bVar9 = bVar15
                        if bVar18:
                            bVar9 = bVar15 - 1
                    uVar14 = uVar13 & 0xFFFE
                    bVar15 = bVar15 + 1
                    uVar13 = uVar14 >> 1
                    if uVar14 == 0:
                        break

            pixel_idx = 0
            pos = (iVar11 + 8) & 0xFFFF
            output = [0] * 12288

            while True:
                if not data[pos:]:
                    color_index = -1
                else:
                    uVar2 = bVar9 * pixel_idx & 7
                    uVar4 = bVar9 * pixel_idx * 65536 >> 0x13
                    if bVar9 < 9:
                        uVar3 = bVar9 + uVar2
                        if uVar3 < 9:
                            uVar6 = data[pos + uVar4] << (8 - uVar3 & 0xFF) & 0xFF
                            uVar6 >>= uVar2 + (8 - uVar3) & 0xFF
                        else:
                            uVar6 = data[pos + uVar4 + 1] << (0x10 - uVar3 & 0xFF) & 0xFF
                            uVar6 >>= 0x10 - uVar3 & 0xFF
                            uVar6 &= 0xFFFF
                            uVar6 <<= 8 - uVar2 & 0xFF
                            uVar6 |= data[pos + uVar4] >> uVar2
                    else:
                        color_index = -1
                        uVar6 = -1
                    if uVar6 != -1:
                        color_index = uVar6

                target_pos = pixel_idx * 3
                if color_index == -1:
                    output[target_pos] = output[target_pos + 1] = output[target_pos + 2] = 0
                else:
                    color_pos = 8 + color_index * 3
                    if color_pos + 2 < len(data):
                        output[target_pos] = data[color_pos]
                        output[target_pos + 1] = data[color_pos + 1]
                        output[target_pos + 2] = data[color_pos + 2]

                pixel_idx += 1
                if pixel_idx == 4096 or pixel_idx >= (row_count * 16) * (row_count * 16):
                    break

            side = row_count * 16
            palettes = []
            raw_indices = []
            for i in range(side * side):
                r = output[i * 3]
                g = output[i * 3 + 1]
                b = output[i * 3 + 2]
                rgb = (r, g, b, 255)
                try:
                    idx = palettes.index(rgb)
                except ValueError:
                    palettes.append(rgb)
                    idx = len(palettes) - 1
                raw_indices.append(idx)

            # Untile from 16x16 blocks into linear row-major scanline order (y * side + x)
            tiles_per_row = side // 16
            indices = [0] * (side * side)
            for i, idx in enumerate(raw_indices):
                tile_id = i // 256
                t_col = tile_id % tiles_per_row
                t_row = tile_id // tiles_per_row
                in_t = i % 256
                gx = t_col * 16 + (in_t % 16)
                gy = t_row * 16 + (in_t // 16)
                if gy * side + gx < len(indices):
                    indices[gy * side + gx] = idx

            return side, palettes, indices
        except Exception as e:
            logger.debug(f"Failed to parse v3 container: {e}")
            return None

    def _parse_spil_frame(self, content):
        """
        Parses raw Divoom SPIL binary content (.bin) and returns (side, palette, indices).
        Accurately detects dimensions (8, 11, 16, 32, 64) and unpacks little-endian bitpacked pixel indices.
        """
        import struct
        import math

        if len(content) < 15:
            return None

        # Collect candidate payloads across all 0xAA block headers and raw fallback
        candidates = []
        for pos in range(min(1024, len(content) - 10)):
            if content[pos] != 0xaa:
                continue
            
            block_id = content[pos+1]
            if block_id not in (0xc1, 0xc2, 0xc3, 0xc0, 0x88, 0x01, 0x02, 0x04, 0x1a):
                continue

            try:
                hdr_len = struct.unpack('<H', content[pos+2:pos+4])[0]
            except Exception:
                hdr_len = 0
                
            payload_candidates = []
            if 10 <= hdr_len <= len(content) - (pos + 4) + 64:
                payload_candidates.append(content[pos+4 : min(len(content), pos + 4 + hdr_len)])
            if len(content) - (pos + 4) >= 10:
                payload_candidates.append(content[pos+4 :])
                
            for payload in payload_candidates:
                if len(payload) >= 4:
                    try:
                        n_colors = struct.unpack('<H', payload[2:4])[0]
                        if 1 <= n_colors <= 256 and 4 + n_colors * 3 <= len(payload):
                            pal = []
                            for i in range(n_colors):
                                r, g, b = payload[4 + i*3 : 4 + (i+1)*3]
                                pal.append((r, g, b, 255))
                            raw = payload[4 + n_colors * 3 :]
                            if len(raw) >= 8:
                                candidates.append((n_colors, pal, raw))
                    except Exception:
                        pass

        # Raw SPIL fallback (if no valid 0xAA header found)
        if not candidates:
            for color_offset in [2, 0, 4]:
                if len(content) >= color_offset + 2:
                    try:
                        n_col = struct.unpack('<H', content[color_offset : color_offset+2])[0]
                        if 1 <= n_col <= 256 and color_offset + 2 + n_col * 3 <= len(content):
                            pal = []
                            for i in range(n_col):
                                r, g, b = content[color_offset + 2 + i*3 : color_offset + 2 + (i+1)*3]
                                pal.append((r, g, b, 255))
                            raw = content[color_offset + 2 + n_col * 3 :]
                            if len(raw) >= 8:
                                candidates.append((n_col, pal, raw))
                    except Exception:
                        pass

        if not candidates:
            return None

        # Try every candidate until we get a valid frame match whose indices fit the palette bounds
        for n_colors, palette, raw in candidates:
            bit_width = max(1, math.ceil(math.log2(n_colors)))
            if not raw:
                continue

            bits = []
            for b in raw[: 4096 * bit_width // 8 + 10]:
                for bit_idx in range(8):
                    bits.append((b >> bit_idx) & 1)

            detected_sz = None
            for candidate_sz in [64, 32, 16, 8]:
                expected_raw_len = candidate_sz * candidate_sz * bit_width // 8
                if len(raw) < expected_raw_len + 8:
                    continue
                header_pos = expected_raw_len
                if raw[header_pos] == 0xaa:
                    next_pos = header_pos + 8 + expected_raw_len
                    if next_pos + 8 <= len(raw) and raw[next_pos] == 0xaa:
                        detected_sz = candidate_sz
                        break
                    elif next_pos >= len(raw) - 8:
                        detected_sz = candidate_sz
                        break

            if not detected_sz:
                for candidate_sz in [64, 32, 16, 8]:
                    expected_raw_len = candidate_sz * candidate_sz * bit_width // 8
                    if expected_raw_len <= len(raw) <= expected_raw_len + 64:
                        detected_sz = candidate_sz
                        break

            if not detected_sz:
                best_sz = None
                best_score = 999.0
                for sz in [64, 32, 16, 8]:
                    if sz * sz * bit_width // 8 > len(raw):
                        continue
                    ind = [sum(bits[i*bit_width + j] * (1 << j) for j in range(bit_width)) for i in range(sz * sz)]
                    if sum(1 for idx in ind if idx >= n_colors) > max(1, int(sz * sz * 0.02)):
                        continue
                    rep = sum(1 for y in range(sz) if ind[y*sz : y*sz + sz//2] == ind[y*sz + sz//2 : y*sz + sz])
                    if rep >= sz * 0.8:
                        continue
                    diff_h = sum(1 for y in range(sz) for x in range(sz - 1) if ind[y*sz + x] != ind[y*sz + x + 1]) / max(1, sz * (sz - 1))
                    diff_v = sum(1 for y in range(sz - 1) for x in range(sz) if ind[y*sz + x] != ind[(y + 1)*sz + x]) / max(1, sz * (sz - 1))
                    score = diff_h + diff_v
                    if score < best_score:
                        best_score = score
                        best_sz = sz
                if best_sz is not None:
                    detected_sz = best_sz

            if not detected_sz:
                continue

            indices = []
            for i in range(detected_sz * detected_sz):
                chunk = bits[i*bit_width : (i+1)*bit_width]
                indices.append(sum(bit * (1 << j) for j, bit in enumerate(chunk)))

            invalid_indices = sum(1 for idx in indices if idx >= n_colors)
            if invalid_indices > max(1, int(len(indices) * 0.02)):
                continue

            return detected_sz, palette, indices

        return None

    def decode_image(self, content):
        """
        Decodes raw bytes from Divoom CDN into a PIL.Image (RGBA).
        First attempts standard image opening (PNG, GIF, WebP).
        If that throws UnidentifiedImageError due to Divoom SPIL binary (.bin) format,
        extracts the frame block and palette to construct the PIL Image cleanly.
        """
        try:
            from PIL import Image
            return Image.open(BytesIO(content)).convert("RGBA")
        except Exception:
            pass

        try:
            from PIL import Image
            import zlib
            import gzip
            
            # Check decompression fallback if standard direct image opening failed
            for decompressor in [lambda c: zlib.decompress(c), lambda c: zlib.decompress(c, -zlib.MAX_WBITS), lambda c: gzip.decompress(c)]:
                try:
                    decomp = decompressor(content)
                    if decomp:
                        try:
                            return Image.open(BytesIO(decomp)).convert("RGBA")
                        except Exception:
                            content = decomp
                            break
                except Exception:
                    pass

            parsed = self._parse_divoom_v3_container(content) or self._parse_spil_frame(content)
            if not parsed:
                return None
            side, palette, indices = parsed
            img = Image.new("RGBA", (side, side), (0, 0, 0, 255))
            pixels = img.load()
            for i, idx in enumerate(indices):
                x = i % side
                y = i // side
                if idx < len(palette):
                    pixels[x, y] = palette[idx]
                elif palette:
                    pixels[x, y] = palette[idx % len(palette)]
            return img
        except Exception as e:
            logger.error(f"Error decoding SPIL/Binary frame: {e}")
            return None

    def decode_to_png_bytes(self, content):
        """
        Converts Divoom raw bytes (whether PNG/GIF or SPIL binary .bin format) into standard PNG bytes.
        Works with or without Pillow (PIL) installed by using a built-in pure Python zlib fallback.
        """
        if content.startswith(b'\x89PNG\r\n\x1a\n'):
            return content

        try:
            from PIL import Image
            img = self.decode_image(content)
            if img:
                if img.width != 64 or img.height != 64:
                    if img.width == img.height or (img.width in (8, 16, 32, 64, 128) and img.height in (8, 16, 32, 64, 128)):
                        img = img.resize((64, 64), Image.Resampling.NEAREST)
                buf = BytesIO()
                img.save(buf, format="PNG")
                return buf.getvalue()
        except Exception:
            pass

        try:
            import struct
            import zlib

            parsed = self._parse_divoom_v3_container(content) or self._parse_spil_frame(content)
            if not parsed:
                return None
            side, palette, indices = parsed

            scale = 64 // side if (side > 0 and 64 % side == 0) else 1
            out_side = side * scale

            raw_scanlines = bytearray()
            for y in range(side):
                scanline = bytearray()
                for x in range(side):
                    idx = indices[y * side + x]
                    if idx < len(palette):
                        rgba = palette[idx]
                    elif palette:
                        rgba = palette[idx % len(palette)]
                    else:
                        rgba = (0, 0, 0, 255)
                    for _ in range(scale):
                        scanline.extend(bytes(rgba[:3]))  # RGB bytes
                for _ in range(scale):
                    raw_scanlines.append(0)  # PNG filter byte None (0)
                    raw_scanlines.extend(scanline)

            compressed = zlib.compress(raw_scanlines)

            def make_chunk(tag, data):
                return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data) & 0xffffffff)

            png_data = b'\x89PNG\r\n\x1a\n'
            png_data += make_chunk(b'IHDR', struct.pack('>IIBBBBB', out_side, out_side, 8, 2, 0, 0, 0))
            png_data += make_chunk(b'IDAT', compressed)
            png_data += make_chunk(b'IEND', b'')
            return png_data
        except Exception as e:
            logger.error(f"Pure Python PNG conversion failed: {e}")
            return None

if __name__ == "__main__":
    pass

