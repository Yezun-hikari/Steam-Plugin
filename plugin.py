import os
import time
import logging
import urllib.request
from io import BytesIO

try:
    from PIL import Image
except ImportError:
    Image = None

try:
    from app.plugin_api import PixooPluginBase
except ImportError:
    class PixooPluginBase:
        def __init__(self, config=None):
            self.config = config or {}
            self.running = False
            self.setup()
        def setup(self): pass
        def get_pixoo_instance(self): return None
        def get_playing_game(self): return None
        def release_screen(self): pass

logger = logging.getLogger(__name__)

class SteamPlugin(PixooPluginBase):
    def setup(self):
        self.steam_api_key = self.config.get("steam_api_key", "")
        self.steam_id = self.config.get("steam_id", "")
        self.min_likes = int(self.config.get("min_likes", 10))
        self.display_duration = max(1, int(self.config.get("display_duration", 10)))
        self.update_interval = max(10, int(self.config.get("update_interval", 30)))

        self.current_game = None
        self.art_list = []
        self.art_index = 0
        self.last_steam_check = 0
        self.last_display_update = 0
        
        self.cache_dir = os.path.join(os.path.dirname(__file__), "cache")
        os.makedirs(self.cache_dir, exist_ok=True)
        self.current_art_path = os.path.join(self.cache_dir, "current_art.png")
        
        from divoom_api import DivoomGalleryAPI
        self.divoom_api = DivoomGalleryAPI(config=self.config)
        
        self.pixoo = self.get_pixoo_instance()
        
        if not self.steam_api_key or not self.steam_id:
            logger.warning("Steam credentials missing. Please configure them in settings.")

    def get_playing_game(self):
        if not self.steam_api_key or not self.steam_id:
            return None
        
        try:
            url = f"https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v0002/?key={self.steam_api_key}&steamids={self.steam_id}"
            res = requests.get(url, timeout=5).json()
            players = res.get("response", {}).get("players", [])
            if players:
                player = players[0]
                return player.get("gameextrainfo")  # Returns the game name if playing
        except Exception as e:
            logger.error(f"Error fetching Steam status: {e}")
        return None

    def fetch_pixel_arts(self, game_name):
        logger.info(f"Searching pixel arts for game: {game_name}")
        artworks = []
        try:
            results = self.divoom_api.smart_search_gallery(game_name, size=64, min_likes=self.min_likes, return_cnt=100)
            for item in results:
                artworks.append({
                    "title": item.get("FileName", game_name),
                    "url": item.get("DownloadUrl"),
                    "likes": item.get("LikeCnt", 0),
                    "pixel_size": item.get("pixel_size", 1),
                    "file_id": item.get("FileId")
                })
        except Exception as e:
            logger.error(f"Error fetching from Divoom API: {e}")
        return artworks

    def download_and_process_art(self, art_item):
        try:
            # Download image or Divoom SPIL binary using standard library
            req = urllib.request.Request(art_item["url"], headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=10) as response:
                content = response.read()
            
            if Image is not None:
                # Use divoom_api to decode both standard PNG/GIFs and Divoom SPIL binary animations into PIL Image
                img = self.divoom_api.decode_image(content)
                if not img:
                    raise ValueError("Could not decode image or SPIL binary format from Divoom CDN")
                
                pixel_size = max(1, int(art_item.get("pixel_size", 1)))
                
                # Downscale by exactly the pixel_size to extract the pure 1:1 original pixel art
                # Nearest neighbor scaling prevents ANY blur or pixel mush
                if pixel_size > 1:
                    new_width = max(1, img.width // pixel_size)
                    new_height = max(1, img.height // pixel_size)
                    img = img.resize((new_width, new_height), Image.Resampling.NEAREST)
                
                # Create a 64x64 black canvas
                canvas = Image.new("RGBA", (64, 64), (0, 0, 0, 255))
                
                # Crop or pad the image to fit 64x64
                offset_x = (64 - img.width) // 2
                offset_y = (64 - img.height) // 2
                canvas.paste(img, (offset_x, offset_y), img)
                
                # Convert to RGB and save
                canvas.convert("RGB").save(self.current_art_path)
                return True
            else:
                # Pure Python fallback (no PIL installed): decode direct to PNG bytes and save
                png_bytes = self.divoom_api.decode_to_png_bytes(content)
                if not png_bytes:
                    raise ValueError("Could not decode raw Divoom content to PNG bytes via pure Python fallback")
                with open(self.current_art_path, 'wb') as f:
                    f.write(png_bytes)
                return True
        except Exception as e:
            logger.error(f"Error processing art '{art_item['title']}': {e}")
            return False

    def cleanup_cache(self):
        """
        Completely deletes ('restlos löschen') all temporary art files when game stops or plugin exits.
        """
        try:
            if os.path.exists(self.current_art_path):
                os.remove(self.current_art_path)
                logger.info("Deleted current_art.png from cache.")
            if os.path.exists(self.cache_dir):
                for filename in os.listdir(self.cache_dir):
                    filepath = os.path.join(self.cache_dir, filename)
                    if os.path.isfile(filepath):
                        os.remove(filepath)
                logger.info("Cleared all temporary images from cache directory.")
        except Exception as e:
            logger.error(f"Error cleaning up cache directory: {e}")

    def release_screen(self):
        """
        Clears the Pixoo display, resets memory state, and deletes all cached images completely.
        """
        self.art_list = []
        self.art_index = 0
        self.cleanup_cache()
        if self.pixoo:
            try:
                self.pixoo.fill((0, 0, 0))
                self.pixoo.push()
            except Exception as e:
                logger.error(f"Error releasing screen: {e}")

    def loop(self):
        while self.running:
            now = time.time()
            
            # Check Steam API periodically
            if now - self.last_steam_check > self.update_interval:
                new_game = self.get_playing_game()
                if new_game != self.current_game:
                    logger.info(f"Game changed to: {new_game}")
                    self.current_game = new_game
                    if self.current_game:
                        self.art_list = self.fetch_pixel_arts(self.current_game)
                        self.art_index = 0
                        self.last_display_update = 0 # Force immediate update
                    else:
                        self.art_list = []
                        self.release_screen()
                self.last_steam_check = now

            # Update Pixoo display if there is art to show
            if self.current_game and self.art_list and now - self.last_display_update > self.display_duration:
                art_item = self.art_list[self.art_index]
                success = self.download_and_process_art(art_item)
                
                if success:
                    self.pixoo.fill((0, 0, 0))
                    self.pixoo.draw_image(self.current_art_path)
                    
                    # Optionally draw the likes count or game name (uncomment if desired)
                    # self.pixoo.draw_text(str(art_item["likes"]), (2, 56), (255, 255, 0))
                    
                    self.pixoo.push()
                
                # Cycle to next art, wraps around when exhausted
                self.art_index = (self.art_index + 1) % len(self.art_list)
                self.last_display_update = now
                
            time.sleep(1)
