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

    def __init__(self, timeout=10):
        # Setting up a permissive SSL context if needed
        self.ctx = ssl.create_default_context()
        self.ctx.check_hostname = False
        self.ctx.verify_mode = ssl.CERT_NONE

        self.timeout = timeout

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

    def search_gallery(self, keyword, size=64, return_cnt=20, base_cnt=0):
        """
        Searches the Divoom gallery for a specific keyword and size.
        """
        if size not in self.SIZE_MAP:
            raise ValueError(f"Invalid size '{size}'. Allowed sizes are: {list(self.SIZE_MAP.keys())}")

        file_size_id = self.SIZE_MAP[size]

        data = {
            "KeyWord": keyword,
            "ReturnCnt": return_cnt,
            "BaseCnt": base_cnt,
            "FileSize": file_size_id
        }

        response = self._post("SearchGalleryV2", data)

        results = []
        if response and response.get("ReturnCode") == 0:
            file_list = response.get("FileList", [])
            for item in file_list:
                file_id = item.get("FileId")
                if file_id:
                    item["DownloadUrl"] = f"{self.FILE_URL}{file_id}"
                results.append(item)

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

    def smart_search_gallery(self, game_name, size=64, min_likes=1, return_cnt=20):
        """
        Intelligent search heuristic for games:
        1. Analyzes game title using extract_logical_keywords (stripping stop words, splitting subtitles, singularizing plurals like Orcs -> Orc).
        2. Searches Divoom Gallery across requested size and fallbacks (32, 16) for every candidate keyword.
        3. Only returns true semantic keyword matches rather than unrelated random hot files.
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

        # Tier 1: Direct Divoom Cloud Search across sizes 64, 32, and 16
        for query in candidates:
            logger.info(f"Searching Divoom Gallery (size={size}) with keyword: '{query}'")
            res = self.search_gallery(query, size=size, return_cnt=return_cnt)
            add_results(res, fallback_pixel_size=1)

            if not res and size != 32:
                logger.info(f"No {size}x{size} matches for '{query}', checking 32x32...")
                res32 = self.search_gallery(query, size=32, return_cnt=return_cnt)
                add_results(res32, fallback_pixel_size=2 if size == 64 else 1)
                if not res32 and size != 16:
                    logger.info(f"No 32x32 matches for '{query}', checking 16x16...")
                    res16 = self.search_gallery(query, size=16, return_cnt=return_cnt)
                    add_results(res16, fallback_pixel_size=4 if size == 64 else 2)

            if len(results) >= 3:
                break

        # Tier 2: Hybrid Keyword Matching against open Divoom Curated Catalogs (RecommendList & HotFiles)
        if not results:
            logger.info(f"Tier 1 yielded 0 direct matches for '{game_name}'. Checking Tier 2: Divoom Curated Catalogs...")
            curated_items = []
            try:
                curated_items.extend(self.get_recommend_list(return_cnt=50))
                curated_items.extend(self.get_hot_files(size=64, return_cnt=50))
                curated_items.extend(self.get_hot_files(size=32, return_cnt=50))
            except Exception as e:
                logger.error(f"Error fetching curated catalogs in Tier 2: {e}")

            # Match items whose title/filename contains any of our candidate strings or token words
            tokens = set()
            for cand in candidates:
                tokens.add(cand.lower())
                for word in cand.lower().split():
                    if len(word) >= 3:
                        tokens.add(word)

            for item in curated_items:
                fname = str(item.get("FileName", "")).lower()
                if any(t in fname for t in tokens):
                    if item.get("LikeCnt", 0) >= min_likes:
                        add_results([item], fallback_pixel_size=1 if item.get("FileSize") == 4 else (2 if item.get("FileSize") == 2 else 4))
                        if len(results) >= return_cnt:
                            break

        # Tier 3: Supplementary Public Dataset Search (e.g. HuggingFace free-to-use-pixelart dataset)
        if not results:
            logger.info(f"Tier 2 yielded 0 catalog matches for '{game_name}'. Checking Tier 3: Public Tagged Dataset...")
            for query in candidates:
                try:
                    url = f"https://datasets-server.huggingface.co/search?dataset=bghira/free-to-use-pixelart&config=default&split=train&query={urllib.parse.quote(query)}"
                    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                    with urllib.request.urlopen(req, context=self.ctx, timeout=5) as r:
                        hf_res = json.loads(r.read().decode('utf-8'))
                        rows = hf_res.get("rows", [])
                        for row_entry in rows:
                            row_data = row_entry.get("row", {})
                            likes = row_data.get("likes_count", 0)
                            full_url = row_data.get("full_image_url")
                            if likes >= min_likes and full_url:
                                results.append({
                                    "FileId": str(row_entry.get("row_idx", "hf")),
                                    "FileName": row_data.get("title", query),
                                    "DownloadUrl": full_url,
                                    "LikeCnt": likes,
                                    "pixel_size": row_data.get("pixel_size", 1),
                                    "source": "pixilart"
                                })
                        if results:
                            logger.info(f"Tier 3 found {len(results)} matches for query '{query}'.")
                            break
                except Exception as e:
                    logger.debug(f"Tier 3 query '{query}' failed or returned no hits: {e}")

        if not results:
            logger.warning(f"Keyword search for '{game_name}' (keywords: {candidates}) yielded 0 matching items across all 3 tiers.")

        return results

    def get_recommend_list(self, return_cnt=50, base_cnt=0):
        """
        Gets recommended high-quality gallery items from Divoom open catalog.
        """
        data = {
            "ReturnCnt": return_cnt,
            "BaseCnt": base_cnt
        }
        response = self._post("Channel/GetRecommendList", data)
        results = []
        if response and response.get("ReturnCode") == 0:
            file_list = response.get("FileList", [])
            for item in file_list:
                file_id = item.get("FileId")
                if file_id:
                    item["DownloadUrl"] = f"{self.FILE_URL}{file_id}"
                results.append(item)
        return results

    def get_hot_files(self, size=32, return_cnt=20, base_cnt=0):
        """
        Gets trending (hot) files dynamically for requested size (16, 32, or 64).
        Falls back to Hot/GetHotFiles32 if Divoom API returns ReturnCode 10 for specific sizes.
        """
        if size not in [16, 32, 64]:
            size = 32
        endpoint = f"Hot/GetHotFiles{size}"

        data = {
            "ReturnCnt": return_cnt,
            "BaseCnt": base_cnt
        }

        response = self._post(endpoint, data)
        if not response or response.get("ReturnCode") != 0:
            logger.info(f"Endpoint {endpoint} not supported by Divoom server, falling back to Hot/GetHotFiles32")
            response = self._post("Hot/GetHotFiles32", data)

        results = []
        if response and response.get("ReturnCode") == 0:
            if "FileList" in response:
                file_list = response.get("FileList", [])
                for item in file_list:
                    file_id = item.get("FileId")
                    if file_id:
                        item["DownloadUrl"] = f"{self.FILE_URL}{file_id}"
                    results.append(item)
            elif "VendorList" in response:
                vendors = response.get("VendorList", [])
                for vendor in vendors:
                    file_list = vendor.get("FileList", [])
                    for item in file_list:
                        file_id = item.get("FileId")
                        if file_id:
                            item["DownloadUrl"] = f"{self.FILE_URL}{file_id}"
                        results.append(item)

        return results

    def get_hot_experts(self, return_cnt=10, base_cnt=0):
        """
        Fetches trending artists on Divoom.
        """
        data = {
            "ReturnCnt": return_cnt,
            "BaseCnt": base_cnt
        }
        response = self._post("Cloud/GetHotExpert", data)

        if response and response.get("ReturnCode") == 0:
            return response.get("ExpertList", [])
        return []

    def _parse_spil_frame(self, content):
        """
        Parses raw Divoom SPIL binary content (.bin) and returns (side, palette, indices).
        Accurately detects dimensions (8, 11, 16, 32, 64) and unpacks little-endian bitpacked pixel indices.
        """
        import struct
        import math

        if len(content) < 25:
            return None

        # Find valid block header (0xAA usually at pos=17)
        pos = -1
        for candidate in [17, 0]:
            if candidate + 10 <= len(content) and content[candidate] == 0xaa:
                pos = candidate
                break
        if pos == -1:
            for i in range(min(100, len(content) - 10)):
                if content[i] == 0xaa:
                    pos = i
                    break
        if pos == -1:
            return None

        length = struct.unpack('<H', content[pos+2:pos+4])[0]
        if pos + 4 + length > len(content) or length < 10:
            return None

        payload = content[pos+4 : pos+4+length]
        n_colors = struct.unpack('<H', payload[2:4])[0]
        if n_colors <= 0 or n_colors > 256 or (4 + n_colors * 3) > len(payload):
            return None

        palette = []
        for i in range(n_colors):
            r, g, b = payload[4 + i*3 : 4 + (i+1)*3]
            palette.append((r, g, b, 255))

        bit_width = max(1, math.ceil(math.log2(n_colors)))
        raw = payload[4 + n_colors*3 :]
        if not raw:
            return None

        # Unpack continuous little-endian bit stream
        bits = []
        for b in raw[: 4096 * bit_width // 8 + 10]:
            for bit_idx in range(8):
                bits.append((b >> bit_idx) & 1)

        # Detect exact side length (64, 32, 16, 8)
        detected_sz = None
        for candidate_sz in [64, 32, 16, 8]:
            frame_payload = candidate_sz * candidate_sz * bit_width // 8
            header_pos = frame_payload
            if header_pos + 8 <= len(raw) and raw[header_pos] == 0xaa:
                next_pos = header_pos + 8 + frame_payload
                if next_pos + 8 <= len(raw) and raw[next_pos] == 0xaa:
                    detected_sz = candidate_sz
                    break

        if not detected_sz:
            best_sz = 16
            best_score = 999.0
            for sz in [64, 32, 16, 8]:
                if sz * sz * bit_width // 8 > len(raw):
                    continue
                ind = [sum(bits[i*bit_width + j] * (1 << j) for j in range(bit_width)) for i in range(sz * sz)]
                rep = sum(1 for y in range(sz) if ind[y*sz : y*sz + sz//2] == ind[y*sz + sz//2 : y*sz + sz])
                if rep >= sz * 0.8:
                    continue
                diff_h = sum(1 for y in range(sz) for x in range(sz - 1) if ind[y*sz + x] != ind[y*sz + x + 1]) / max(1, sz * (sz - 1))
                diff_v = sum(1 for y in range(sz - 1) for x in range(sz) if ind[y*sz + x] != ind[(y + 1)*sz + x]) / max(1, sz * (sz - 1))
                score = diff_h + diff_v
                if sz == 64: score += 0.15
                elif sz == 32: score += 0.05
                if score < best_score:
                    best_score = score
                    best_sz = sz
            detected_sz = best_sz

        indices = []
        for i in range(detected_sz * detected_sz):
            chunk = bits[i*bit_width : (i+1)*bit_width]
            indices.append(sum(bit * (1 << j) for j, bit in enumerate(chunk)))

        return detected_sz, palette, indices

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
            parsed = self._parse_spil_frame(content)
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
                buf = BytesIO()
                img.save(buf, format="PNG")
                return buf.getvalue()
        except Exception:
            pass

        try:
            import struct
            import zlib

            parsed = self._parse_spil_frame(content)
            if not parsed:
                return None
            side, palette, indices = parsed

            raw_scanlines = bytearray()
            for y in range(side):
                raw_scanlines.append(0)  # PNG filter byte None (0)
                for x in range(side):
                    idx = indices[y * side + x]
                    if idx < len(palette):
                        rgba = palette[idx]
                    elif palette:
                        rgba = palette[idx % len(palette)]
                    else:
                        rgba = (0, 0, 0, 255)
                    raw_scanlines.extend(bytes(rgba[:3]))  # RGB bytes

            compressed = zlib.compress(raw_scanlines)

            def make_chunk(tag, data):
                return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data) & 0xffffffff)

            png_data = b'\x89PNG\r\n\x1a\n'
            png_data += make_chunk(b'IHDR', struct.pack('>IIBBBBB', side, side, 8, 2, 0, 0, 0))
            png_data += make_chunk(b'IDAT', compressed)
            png_data += make_chunk(b'IEND', b'')
            return png_data
        except Exception as e:
            logger.error(f"Pure Python PNG conversion failed: {e}")
            return None

if __name__ == "__main__":
    pass

