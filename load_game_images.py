#!/usr/bin/env python3
import os
import sys
import argparse
import urllib.request
from divoom_api import DivoomGalleryAPI

def main():
    parser = argparse.ArgumentParser(description="Download Divoom Gallery artwork for a specific game using intelligent keyword search.")
    parser.add_argument("--game", default="Orcs Must Die! Deathtrap", help="Name of the game to search for")
    parser.add_argument("--folder", default="Orcs Must Die! Deathtrap", help="Target folder to save downloaded .bin and .png files")
    parser.add_argument("--count", type=int, default=20, help="Maximum number of artworks to download")
    parser.add_argument("--size", type=int, default=64, help="Target pixel art size (64, 32, or 16)")
    
    args = parser.parse_args()
    
    # Ensure target directory exists
    target_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), args.folder)
    os.makedirs(target_dir, exist_ok=True)
    
    api = DivoomGalleryAPI()
        
    keywords = api.extract_logical_keywords(args.game)
    print(f"🔍 Analysierte Suchbegriffe für '{args.game}': {keywords}")
    
    results = api.smart_search_gallery(args.game, size=args.size, return_cnt=args.count)
    
    if not results:
        print("\n⚠️ Keine Treffer gefunden (0 Ergebnisse von Divoom Server zurückgegeben).")
        return

    print(f"\n✅ {len(results)} passende Kunstwerke gefunden! Lade Dateien nach '{target_dir}' herunter...\n")
    
    downloaded = 0
    for idx, item in enumerate(results, start=1):
        file_id = item.get("FileId", f"item_{idx}")
        url = item.get("DownloadUrl")
        title = item.get("FileName", f"Art_{idx}")
        likes = item.get("LikeCnt", 0)
        pixel_size = item.get("pixel_size", 1) # Scaling factor if fallback from 32x32 or 16x16
        
        if not url:
            continue
            
        try:
            print(f"[{idx}/{len(results)}] Lade '{title}' (Likes: {likes}, FileId: {file_id})...")
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=10) as response:
                content = response.read()
                
            base_name = f"divoom_art_{idx:02d}_{file_id.replace('/', '_')}"
            bin_path = os.path.join(target_dir, f"{base_name}.bin")
            png_path = os.path.join(target_dir, f"{base_name}.png")
            
            # Save raw binary if it's a .bin file
            if url.endswith('.bin') or content[:16].find(b'\xaa\xc1') != -1 or len(content) > 100 and content[17] == 0xaa:
                with open(bin_path, "wb") as f:
                    f.write(content)
                    
            # Decode directly to clean PNG using pure Python decoder fallback or PIL
            png_bytes = api.decode_to_png_bytes(content)
            if png_bytes:
                with open(png_path, "wb") as f:
                    f.write(png_bytes)
                print(f"    -> Gespeichert: {base_name}.png ({len(png_bytes)} Bytes)")
                downloaded += 1
            else:
                print(f"   ❌ Konvertierung zu PNG fehlgeschlagen für {title}")
                
        except Exception as e:
            print(f"   ❌ Fehler beim Download von {url}: {e}")
            
    print(f"\n🎉 Download abgeschlossen! {downloaded} Bilder befinden sich jetzt in '{args.folder}/'.")

if __name__ == "__main__":
    main()
