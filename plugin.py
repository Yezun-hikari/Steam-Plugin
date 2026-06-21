import os
import time
import logging
import requests
from io import BytesIO
from PIL import Image
from app.plugin_api import PixooPluginBase

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
        
        self.pixoo = self.get_pixoo_instance()
        
        if not self.steam_api_key or not self.steam_id:
            logger.warning("Steam credentials missing. Please configure them in settings.")

    def get_playing_game(self):
        if not self.steam_api_key or not self.steam_id:
            return None
        
        try:
            url = f"http://api.steampowered.com/ISteamUser/GetPlayerSummaries/v0002/?key={self.steam_api_key}&steamids={self.steam_id}"
            res = requests.get(url, timeout=5).json()
            players = res.get("response", {}).get("players", [])
            if players:
                player = players[0]
                return player.get("gameextrainfo") # Returns the game name if playing
        except Exception as e:
            logger.error(f"Error fetching Steam status: {e}")
        return None

    def fetch_pixel_arts(self, game_name):
        logger.info(f"Searching pixel arts for game: {game_name}")
        artworks = []
        try:
            # Query HuggingFace datasets server for Pixilart dataset
            url = f"https://datasets-server.huggingface.co/search?dataset=bghira/free-to-use-pixelart&config=default&split=train&query={game_name}"
            res = requests.get(url, timeout=10).json()
            rows = res.get("rows", [])
            
            for r in rows:
                item = r.get("row", {})
                likes = item.get("likes_count", 0)
                if likes >= self.min_likes:
                    artworks.append({
                        "title": item.get("title", "Unknown"),
                        "likes": likes,
                        "pixel_size": item.get("pixel_size", 1),
                        "url": item.get("full_image_url")
                    })
            
            # Sort by likes descending
            artworks.sort(key=lambda x: x["likes"], reverse=True)
            logger.info(f"Found {len(artworks)} artworks matching criteria.")
        except Exception as e:
            logger.error(f"Error fetching pixel arts: {e}")
        return artworks

    def download_and_process_art(self, art_item):
        try:
            # Download image
            res = requests.get(art_item["url"], timeout=10)
            res.raise_for_status()
            img = Image.open(BytesIO(res.content)).convert("RGBA")
            
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
            # We center the 1:1 pixel art on the canvas
            offset_x = (64 - img.width) // 2
            offset_y = (64 - img.height) // 2
            
            canvas.paste(img, (offset_x, offset_y), img)
            
            # Convert to RGB and save
            canvas.convert("RGB").save(self.current_art_path)
            return True
        except Exception as e:
            logger.error(f"Error processing art '{art_item['title']}': {e}")
            return False

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
