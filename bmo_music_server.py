import os
import json
import re
import urllib.parse
import threading
from flask import Flask, request, jsonify, send_from_directory, render_template_string
from fuzzywuzzy import fuzz
import yt_dlp

import socket

def get_local_ip():
    """Returns the primary LAN IP of this machine."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "127.0.0.1"

LAN_IP = get_local_ip()
print(f"[NETWORK] Resolved LAN IP: {LAN_IP}")

app = Flask(__name__)
MUSIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "music")
METADATA_FILE = os.path.join(MUSIC_DIR, "metadata.json")
os.makedirs(MUSIC_DIR, exist_ok=True)

def load_metadata():
    if os.path.exists(METADATA_FILE):
        try:
            with open(METADATA_FILE, "r") as f:
                return json.load(f)
        except: return {}
    return {}

def save_metadata(db):
    try:
        with open(METADATA_FILE, "w") as f:
            json.dump(db, f, indent=4)
    except: pass

def update_song_metadata(video_id, title, artist):
    db = load_metadata()
    db[video_id] = {"title": title, "artist": artist}
    save_metadata(db)

def repair_library_metadata():
    """Scans the music folder for any files missing from metadata and tries to recover their names."""
    print("[REPAIR] Starting Library Metadata Recovery...")
    db = load_metadata()
    files = [f for f in os.listdir(MUSIC_DIR) if f.lower().endswith(('.mp3', '.m4a', '.webm', '.wav', '.flac', '.ogg'))]
    modified = False
    
    # ydl setup just for extraction (no download)
    ydl_opts = {'quiet': True, 'nocheckcertificate': True, 'noplaylist': True, 'no_warnings': True}
    
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        for f in files:
            vid_id = f.rsplit('.', 1)[0]
            if vid_id not in db or not db[vid_id].get("title"):
                # 🟢 ONLY repair if it looks like a YouTube ID (11 chars)
                # This stops errors for human-readable filenames!
                if len(vid_id) == 11:
                    try:
                        print(f"[REPAIR] Recovering info for: {vid_id}")
                        info = ydl.extract_info(f"https://www.youtube.com/watch?v={vid_id}", download=False)
                        if info:
                            db[vid_id] = {
                                "title": info.get('title', vid_id),
                                "artist": info.get('uploader', 'YouTube')
                            }
                            modified = True
                    except: pass
                    
    if modified:
        save_metadata(db)
        print("[REPAIR] Library metadata updated!")
    else:
        print("[REPAIR] No missing metadata found.")

# --- STATE VARIABLES FOR QUEUING & MODES ---
play_queue = []
play_mode = "normal"  # "normal", "loop", "random", "artist"
current_playing = None
current_artist_focus = ""

# HTML Template for the Dashboard
INDEX_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>BMO Music Backend</title>
    <style>
        body { font-family: -apple-system, Roboto, sans-serif; background-color: #121212; color: white; padding: 20px; }
        h1 { color: #1DB954; }
        .container { max-width: 800px; margin: auto; background: #1e1e1e; padding: 20px; border-radius: 10px; }
        ul { list-style: none; padding: 0; }
        li { background: #282828; margin: 10px 0; padding: 15px; border-radius: 5px; }
        .status { color: #1DB954; font-weight: bold; }
        .queue-box { background: #333; padding: 10px; border-radius: 8px; margin-top: 20px; border-left: 4px solid #1DB954; }
    </style>
</head>
<body>
    <div class="container">
        <h1>🟢 BMO Music Server</h1>
        <div class="queue-box">
            <b>Current Mode:</b> {{ mode | upper }}<br>
            <b>Now Playing:</b> {{ current.title if current else 'Nothing' }}<br>
            <b>Up Next (Queue):</b> {{ queue | join(', ') if queue else 'Empty' }}
        </div>
        
        <h2>Local Library:</h2>
        <ul>
            {% for song in songs %}
            <li><span>READY - {{ song }}</span></li>
            {% else %}
            <li>No local music found in 'music/' folder.</li>
            {% endfor %}
        </ul>
        
        <p style="margin-top:30px; font-size:0.9em; color:#888;">
            <b>How it works:</b> When BMO asks for a song, I check this library first. 
            If it's missing, I instantly download and stream it right from Youtube!
        </p>
    </div>
</body>
</html>
"""

@app.route("/")
def index():
    songs = [f for f in os.listdir(MUSIC_DIR) if f.endswith(('.mp3', '.m4a', '.webm', '.wav', '.flac', '.ogg'))]
    return render_template_string(INDEX_HTML, songs=songs, mode=play_mode, current=current_playing, queue=play_queue)

@app.route("/stream/<path:filename>")
def stream_music(filename):
    return send_from_directory(MUSIC_DIR, filename)

def fetch_song(query, host_url):
    """Internal function to handle grabbing a song either locally or via YouTube."""
    local_files = [f for f in os.listdir(MUSIC_DIR) if f.lower().endswith(('.mp3', '.m4a', '.webm', '.wav', '.flac', '.ogg'))]
    db = load_metadata()
    
    # 🟢 High-Priority: Direct filename match (case-insensitive)
    query_clean = query.lower().strip()
    match = next((f for f in local_files if f.lower() == query_clean), None)
    
    if match:
        best_match = match
        best_score = 100
    else:
        best_match = None
        best_score = 0
        
        for f in local_files:
            vid_id = f.rsplit('.', 1)[0]
            meta = db.get(vid_id, {})
            # Search by real title if we have it, else filename
            search_target = meta.get("title", f)
            
            score = fuzz.token_set_ratio(query.lower(), search_target.lower())
            if score > best_score:
                best_score = score
                best_match = f
            
    if best_match and best_score > 80:
        vid_id = best_match.rsplit('.', 1)[0]
        meta = db.get(vid_id, {})
        stream_url = f"{host_url}/stream/{best_match}"
        return {
            "title": meta.get("title", vid_id),
            "artist": meta.get("artist", "Local Library"),
            "url": stream_url,
            "correction_speech": ""
        }
        
    print(f"[YOUTUBE] Searching: {query}")
    try:
        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': os.path.join(MUSIC_DIR, '%(id)s.%(ext)s'),
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            'noplaylist': True,
            'quiet': True,
            'nocheckcertificate': True,
            'default_search': 'ytsearch'
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            try:
                # 🛡️ If it looks like a URL, try it direct. If not, use search.
                if query.startswith("http"):
                    info = ydl.extract_info(query, download=True)
                else:
                    info = ydl.extract_info(f"ytsearch:{query}", download=True)
            except Exception as e:
                # 🛡️ SMART FALLBACK: If direct URL failed, try a search for it instead!
                print(f"[YOUTUBE] Extraction failed, falling back to smart search...")
                # Strip the URL part if possible to just get the "Title" part of the query
                clean_q = query.replace("https://www.youtube.com/watch?v=", "").replace("http://", "").replace("https://", "")
                info = ydl.extract_info(f"ytsearch:{clean_q}", download=True)
                
            if not info: return None
            
            # If search entry found, use the first one
            if 'entries' in info:
                if not info['entries']: return None
                info = info['entries'][0]
            real_title = info.get('title', query)
            artist = info.get('uploader', 'YouTube')
            
            # Clean up YouTube title formatting
            if " - " in real_title:
                parts = real_title.split(" - ", 1)
                artist = parts[0].strip()
                real_title = parts[1].strip()
                
            real_title = re.sub(r'\(.*?\)|\[.*?\]', '', real_title).strip()
            
            # 🛡️ DETERMINISTIC FILENAME: Use Video ID
            video_id = info.get('id', 'unknown')
            filename = f"{video_id}.mp3"
            
            # 🟢 Save human-readable names to our database!
            update_song_metadata(video_id, real_title, artist)
            
            # 🛡️ SKIP FUZZY CHECK FOR DIRECT URLS:
            correction = ""
            if not query.startswith("http"):
                match_score = fuzz.token_set_ratio(query.lower(), real_title.lower())
                if match_score < 55:  
                    correction = f"Maybe what you mean is {real_title}... Okay then!"
                
            return {
                "title": real_title,
                "artist": artist,
                "url": f"{host_url}/stream/{filename}",
                "correction_speech": correction
            }
    except Exception as e:
        print(f"[ERROR] {e}")
        return None

@app.route("/api/search")
def search_music():
    global play_queue, play_mode, current_playing, current_artist_focus
    
    query = request.args.get("q", "").lower().strip()
    host_url = f"http://{LAN_IP}:5001"
    
    if not query: 
        return jsonify({"error": "No query"}), 400
    
    # --- PARSE COMMANDS ---
    is_queuing = False
    
    if query.endswith(" next"):
        query = query.replace(" next", "").strip()
        is_queuing = True
    elif query.endswith(" on loop"):
        play_mode = "loop"
        query = query.replace(" on loop", "").strip()
        play_queue.clear()
    elif query in ("music random", "random music"):
        play_mode = "random"
        play_queue.clear()
        query = "top hits playlist"
    elif query.startswith("music from "):
        play_mode = "artist"
        current_artist_focus = query.replace("music from ", "").strip()
        play_queue.clear()
        query = current_artist_focus + " song"
    else:
        play_mode = "normal"
        play_queue.clear()
        
    if is_queuing:
        # 🟢 SMART QUEUE: If nothing is playing, the "Queue" button auto-plays instead!
        if not current_playing and len(play_queue) == 0:
            is_queuing = False
            print("[HUB] Queue requested but idle - auto-playing instead.")
        else:
            play_queue.append(query)
            print(f"[QUEUE] Added: {query}")
            return jsonify({
                "title": "Queued",
                "artist": "System",
                "url": "",
                "correction_speech": f"I've added {query} to your queue, Finn!"
            })

    data = fetch_song(query, host_url)
    if data:
        current_playing = data
        return jsonify(data)
    else:
        return jsonify({"error": "Song not found or unavailable"}), 404

@app.route("/api/next")
def next_music():
    global play_queue, play_mode, current_playing, current_artist_focus
    import random
    
    host_url = f"http://{LAN_IP}:5001"
    
    if play_mode == "loop" and current_playing:
        return jsonify(current_playing)
        
    if len(play_queue) > 0:
        next_q = play_queue.pop(0)
        data = fetch_song(next_q, host_url)
        if data:
            current_playing = data
            return jsonify(data)
            
    if play_mode == "random":
        local_files = [f for f in os.listdir(MUSIC_DIR) if f.endswith(('.mp3', '.m4a', '.webm'))]
        if local_files:
            random_song = random.choice(local_files)
            data = {
                "title": random_song.rsplit('.', 1)[0],
                "artist": "Local Library",
                "url": f"{host_url}/stream/{random_song}",
                "correction_speech": ""
            }
            current_playing = data
            return jsonify(data)
            
    if play_mode == "artist" and current_artist_focus:
        data = fetch_song(f"{current_artist_focus} audio track {random.randint(1, 100)}", host_url)
        if data:
            current_playing = data
            return jsonify(data)
            
    if current_playing:
        return jsonify(current_playing)
    
    current_playing = None
    play_mode = "normal"
    return jsonify({"error": "End of queue"}), 404

@app.route("/api/current")
def get_current_song():
    global current_playing
    if current_playing:
        return jsonify(current_playing)
    return jsonify({"error": "Nothing currently playing"}), 404

@app.route("/api/library")
def get_music_library():
    """Returns list of local music files in the music folder with real names."""
    files = [f for f in os.listdir(MUSIC_DIR) if f.lower().endswith(('.mp3', '.m4a', '.wav', '.webm', '.flac', '.ogg'))]
    db = load_metadata()
    library = []
    host_url = f"http://{LAN_IP}:5001"
    for f in files:
        vid_id = f.rsplit('.', 1)[0]
        meta = db.get(vid_id, {})
        title = meta.get("title", vid_id.title())
        artist = meta.get("artist", "Local")
        
        library.append({
            "filename": f,
            "title": f"{title}",
            "artist": artist,
            "url": f"{host_url}/stream/{urllib.parse.quote(f)}"
        })
    # Sort library alphabetically by title
    library.sort(key=lambda x: x["title"])
    return jsonify(library)

@app.route("/api/status")
def api_status():
    return jsonify({
        "mode": play_mode,
        "current": current_playing,
        "queue": play_queue
    })

@app.route("/shutdown")
def shutdown():
    os._exit(0)
    return "Shutting down..."

# ✅ FLASK STARTUP - DO NOT USE app.mainloop() WITH FLASK!
if __name__ == "__main__":
    print("\n" + "="*50)
    print(">>> BMO CLOUD SERVER STARTING")
    print(f"IP: {LAN_IP}")
    print("Upload standard MP3s to the 'music' folder to skip YouTube!")
    print("="*50 + "\n")
    
    # 🟢 Auto-repair the library metadata on start
    threading.Thread(target=repair_library_metadata, daemon=True).start()
    
    # 🟢 Detect the Cloud Port (Render provides this)
    port = int(os.environ.get("PORT", 5001))
    
    app.run(host="0.0.0.0", port=port)