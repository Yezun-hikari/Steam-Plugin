#!/usr/bin/env python3
import os
import sys
import json
import argparse
import urllib.request
import re

script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path:
    sys.path.insert(0, script_dir)

try:
    from .divoom_api import DivoomGalleryAPI
except ImportError:
    from divoom_api import DivoomGalleryAPI


def load_credentials_from_settings():
    candidates = [
        os.path.join(script_dir, "settings.json"),
        os.path.join(script_dir, "..", "settings.json"),
        os.path.join(script_dir, "..", "..", "settings.json"),
        os.path.join(os.path.expanduser("~"), ".pixoohub", "settings.json"),
        os.path.join(os.path.expanduser("~"), "AppData", "Roaming", "PixooHub", "settings.json"),
    ]
    for cand in candidates:
        if os.path.exists(cand):
            try:
                with open(cand, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        plug_cfg = data.get("Steam-Plugin", data.get("plugins", {}).get("Steam-Plugin", data))
                        email = plug_cfg.get("divoom_email") or data.get("divoom_email")
                        pwd = plug_cfg.get("divoom_password") or data.get("divoom_password")
                        if email and pwd:
                            return email, pwd
            except Exception:
                pass
    return None, None


def main():
    parser = argparse.ArgumentParser(description="Download Divoom Gallery artwork for a specific game using intelligent keyword search.")
    parser.add_argument("--game", default="Orcs Must Die! Deathtrap", help="Name of the game to search for")
    parser.add_argument("--folder", default="Orcs Must Die! Deathtrap", help="Target folder to save downloaded .bin and .png files")
    parser.add_argument("--count", type=int, default=20, help="Maximum number of artworks to download")
    parser.add_argument("--size", type=int, default=64, help="Target pixel art size (64, 32, or 16)")
    parser.add_argument("--email", default="", help="Divoom account email for login")
    parser.add_argument("--password", default="", help="Divoom account password for login")
    
    args = parser.parse_args()
    
    # Ensure target directory exists
    target_dir = os.path.join(script_dir, args.folder)
    os.makedirs(target_dir, exist_ok=True)
    
    email = args.email or os.environ.get("DIVOOM_EMAIL")
    password = args.password or os.environ.get("DIVOOM_PASSWORD")
    if not email or not password:
        s_email, s_pwd = load_credentials_from_settings()
        email = email or s_email
        password = password or s_pwd

    if not email or not password:
        print("⚠️ Hinweis: Keine Divoom Login-Daten gefunden (weder per --email/--password noch in settings.json).")
        print("Die Tag-Suche benötigt für manche Abfragen einen eingeloggten Account, da Divoom sonst ReturnCode=11 liefert.")

    api = DivoomGalleryAPI(email=email, password=password)
    if email and password and not api.token:
        api.login()
        
    keywords = api.extract_logical_keywords(args.game)
    print(f"🔍 Analysierte Suchbegriffe für '{args.game}': {keywords}")
    
    results = api.smart_search_gallery(args.game, size=args.size, return_cnt=args.count)
    
    if not results:
        print("\n⚠️ Keine Treffer gefunden (0 Ergebnisse von Divoom Server zurückgegeben).")
        if not api.token:
            print("💡 Tipp: Bitte starte das Skript mit deinen Divoom Login-Daten:\n   python3 load_game_images.py --email DEINE_EMAIL --password DEIN_PASSWORT")
        return

    print(f"\n✅ {len(results)} passende Kunstwerke gefunden! Lade Dateien nach '{target_dir}' herunter...\n")
    
    downloaded_items = []
    downloaded = 0
    for idx, item in enumerate(results, start=1):
        file_id = item.get("FileId", f"item_{idx}")
        url = item.get("DownloadUrl")
        title = item.get("FileName", f"Art_{idx}")
        likes = item.get("LikeCnt", 0)
        
        if not url:
            continue
            
        try:
            print(f"[{idx}/{len(results)}] Lade '{title}' (Likes: {likes}, FileId: {file_id})...")
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=10) as response:
                content = response.read()
                
            safe_file_id = str(file_id).replace('/', '_').replace('\\', '_')
            base_name = f"divoom_art_{idx:02d}_{safe_file_id}"
            bin_path = os.path.join(target_dir, f"{base_name}.bin")
            png_path = os.path.join(target_dir, f"{base_name}.png")
            
            # Always save raw CDN binary
            with open(bin_path, "wb") as f:
                f.write(content)
                    
            # Decode directly to clean 64x64 PNG
            png_bytes = api.decode_to_png_bytes(content)
            if png_bytes:
                with open(png_path, "wb") as f:
                    f.write(png_bytes)
                print(f"    -> Gespeichert: {base_name}.png ({len(png_bytes)} Bytes) & {base_name}.bin ({len(content)} Bytes)")
                downloaded += 1
                downloaded_items.append({
                    "idx": idx,
                    "title": title,
                    "likes": likes,
                    "file_id": file_id,
                    "png_name": f"{base_name}.png",
                    "bin_name": f"{base_name}.bin",
                    "url": url
                })
            else:
                print(f"   ❌ Konvertierung zu PNG fehlgeschlagen für {title}")
                
        except Exception as e:
            print(f"   ❌ Fehler beim Download von {url}: {e}")
            
    # Generate index.html preview gallery
    html_path = os.path.join(target_dir, "index.html")
    html_content = [
        "<!DOCTYPE html>",
        "<html lang='de'>",
        "<head>",
        "  <meta charset='utf-8'>",
        "  <title>Divoom Pixel Art Vorschau - Orcs Must Die! Deathtrap</title>",
        "  <style>",
        "    body { font-family: sans-serif; background: #1a1a1a; color: #eee; margin: 20px; }",
        "    h1 { color: #4CAF50; }",
        "    .grid { display: flex; flex-wrap: wrap; gap: 20px; margin-top: 20px; }",
        "    .card { background: #2a2a2a; border: 1px solid #444; border-radius: 8px; padding: 15px; width: 220px; text-align: center; box-shadow: 0 4px 6px rgba(0,0,0,0.3); }",
        "    .pixel-art { width: 192px; height: 192px; image-rendering: pixelated; border: 2px solid #555; background: #000; margin-bottom: 10px; }",
        "    .meta { font-size: 13px; color: #ccc; margin: 4px 0; }",
        "    .title { font-weight: bold; font-size: 15px; color: #fff; margin-bottom: 8px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }",
        "    a { color: #64B5F6; text-decoration: none; font-size: 12px; }",
        "    a:hover { text-decoration: underline; }",
        "  </style>",
        "</head>",
        "<body>",
        f"  <h1>🎨 Divoom Pixel Art Galerie: {args.game}</h1>",
        f"  <p>Insgesamt <b>{len(downloaded_items)}</b> Bilder erfolgreich heruntergeladen und zu 64x64 PNG dekodiert.</p>",
        "  <div class='grid'>"
    ]
    for item in downloaded_items:
        html_content.append(f"""    <div class='card'>
      <div class='title' title='{item["title"]}'>{item["title"]}</div>
      <img class='pixel-art' src='{item["png_name"]}' alt='{item["title"]}'>
      <div class='meta'>👍 Likes: <b>{item["likes"]}</b></div>
      <div class='meta'>ID: {item["file_id"]}</div>
      <div class='meta'><a href='{item["bin_name"]}' download>📦 Raw .bin</a> | <a href='{item["png_name"]}' download>🖼️ 64x64 .png</a></div>
    </div>""")
    html_content.extend([
        "  </div>",
        "</body>",
        "</html>"
    ])
    try:
        with open(html_path, "w", encoding="utf-8") as f:
            f.write("\n".join(html_content))
        print(f"\n🌐 HTML-Vorschau generiert! Öffne '{args.folder}/index.html' in deinem Browser zur Kontrolle.")
    except Exception as e:
        print(f"Fehler beim Erstellen der HTML-Vorschau: {e}")

    print(f"\n🎉 Download abgeschlossen! {downloaded} Bilder befinden sich jetzt im Ordner '{args.folder}/'.")

if __name__ == "__main__":
    main()
