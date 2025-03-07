import os
import json
import time
import subprocess
import threading
import psutil
from pathlib import Path
from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO

app = Flask(__name__)
socketio = SocketIO(app)

processing = False  # Stato dell'elaborazione
pause_processing = False  # Stato di pausa
task_progress = 0  # Percentuale di avanzamento
current_task = None  # File attualmente in elaborazione
start_time = None  # Tempo di avvio
task_queue = []  # Lista delle task in coda

def scan_vods():
    """Scansiona la cartella dei VOD per trovare quelli da processare"""
    twitch_downloads = Path("/mnt/twitch")
    task_queue.clear()
    for streamer_folder in twitch_downloads.iterdir():
        if streamer_folder.is_dir():
            for vod in streamer_folder.iterdir():
                if vod.is_dir():
                    task_queue.append(vod)
    return len(task_queue)

def process_vods():
    """Esegue la conversione dei VOD con monitoraggio"""
    global processing, pause_processing, task_progress, current_task, start_time
    while processing:
        if not task_queue:
            processing = False
            break

        if pause_processing:
            time.sleep(1)
            continue

        vod_folder = task_queue.pop(0)
        current_task = vod_folder
        start_time = time.time()
        task_progress = 0

        metadata_file = next(vod_folder.glob("*-info.json"), None)
        chat_video = next(vod_folder.glob("*-chat.mp4"), None)
        main_video = next(vod_folder.glob("*-video.mp4"), None)

        if not metadata_file or not main_video or not chat_video:
            continue

        with open(metadata_file, "r", encoding="utf-8") as f:
            metadata = json.load(f)

        streamer_name = metadata.get("user_name", "UnknownStreamer").replace(" ", "_")
        title = metadata.get("title", "UnknownTitle").replace(" ", "_")
        date = metadata.get("created_at", "0000-00-00").split("T")[0]
        category = metadata.get("category", "UnknownCategory")
        vod_url = metadata.get("url", "")

        output_video = Path(f"/mnt/twitch/processing/{streamer_name}/{date} - {title}.mp4")
        output_video.parent.mkdir(parents=True, exist_ok=True)

        ffmpeg_cmd = [
            "ffmpeg", "-i", str(main_video), "-i", str(chat_video),
            "-filter_complex", "[0:v]scale=1280:720[left]; [1:v]crop=340:720:0:360,scale=340:720[right]; [left][right]hstack=inputs=2[out]",
            "-map", "[out]", "-map", "0:a?", "-r", "30", "-c:v", "libx264", "-crf", "23", "-preset", "ultrafast",
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-threads", "1", str(output_video)
        ]

        process = subprocess.Popen(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        for line in process.stdout:
            if "frame=" in line:
                task_progress = min(task_progress + 1, 100)
                socketio.emit("update_progress", {"progress": task_progress})

        process.wait()
        task_progress = 100
        socketio.emit("update_progress", {"progress": 100})
        time.sleep(2)

@app.route('/')
def index():
    return render_template("index.html")

@app.route('/start', methods=['POST'])
def start():
    global processing, processing_thread
    if not processing:
        processing = True
        processing_thread = threading.Thread(target=process_vods, daemon=True)
        processing_thread.start()
    return jsonify(status="started")

@app.route('/pause', methods=['POST'])
def pause():
    global pause_processing
    pause_processing = not pause_processing
    return jsonify(status="paused" if pause_processing else "resumed")

@app.route('/stop', methods=['POST'])
def stop():
    global processing, pause_processing
    processing = False
    pause_processing = False
    return jsonify(status="stopped")

@app.route('/status')
def status():
    cpu_usage = psutil.cpu_percent()
    ram_usage = psutil.virtual_memory().percent
    disk_usage = psutil.disk_usage("/").percent
    elapsed_time = round(time.time() - start_time, 2) if start_time else 0
    return jsonify(
        processing=processing,
        paused=pause_processing,
        progress=task_progress,
        current_task=str(current_task) if current_task else "None",
        cpu=cpu_usage,
        ram=ram_usage,
        disk=disk_usage,
        elapsed_time=elapsed_time,
    )

if __name__ == "__main__":
    scan_vods()
    socketio.run(app, host="0.0.0.0", port=5000, debug=True, allow_unsafe_werkzeug=True)
