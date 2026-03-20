import cv2
import numpy as np
import subprocess
import os
import sys
import json
import time
import traceback
import logging
from datetime import datetime
from multiprocessing import Process, Lock
from ultralytics import YOLO
import threading
import sqlite3
from src.routes.apiSignal import app

# ================= CONFIG =================
MODEL_PATH = "models/yolov8n.pt"
DB_PATH = "db/traffic.db"

WIDTH = 640
HEIGHT = 360
CONF_TH = 0.55
FRAME_SKIP = 8
COUNT_INTERVAL = 7
MAX_PROC = 2

VEHICLE_CLASSES = {"car", "motorcycle", "bus", "truck"}

# PENTING: kamu mau format ini selalu -> cctv_1, cctv_2, dst
USE_PREFIX_CCTV = True
# =========================================

db_lock = Lock()

# ========= LOGGING SETUP =========
os.makedirs("logs", exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler("logs/system.log"),
        logging.StreamHandler(sys.stdout)
    ]
)

def log(cid, level, message):
    msg = f"[{cid}] {message}"
    if level == "info":
        logging.info(msg)
    elif level == "error":
        logging.error(msg)
    elif level == "warning":
        logging.warning(msg)
    else:
        logging.debug(msg)

# ========= CCTV ID NORMALIZER =========
def normalize_cctv_id(raw_id) -> str:
    """
    Pastikan selalu 'cctv_<id>'
    """
    s = str(raw_id).strip()
    if not USE_PREFIX_CCTV:
        return s
    if s.startswith("cctv_"):
        return s
    return "cctv_" + s


# ========= LOAD YOLO =========
def load_yolo():
    logging.info("Loading YOLO model...")
    model = YOLO(MODEL_PATH)
    model.fuse()
    logging.info("YOLO loaded successfully")
    return model


# ========= DRAW BOX =========
COLORS = {
    "car": ((255, 100, 0), (200, 70, 0)),
    "motorcycle": ((0, 200, 255), (0, 160, 200)),
    "bus": ((0, 0, 255), (0, 0, 180)),
    "truck": ((180, 0, 255), (140, 0, 200)),
}

def draw_modern_box(frame, x1, y1, x2, y2, label, conf):
    box_color, bg_color = COLORS.get(label, ((200, 200, 200), (120, 120, 120)))
    font = cv2.FONT_HERSHEY_SIMPLEX
    padding = 4

    cv2.rectangle(frame, (x1, y1), (x2, y2), box_color, 2)

    text = f"{label} {conf:.2f}"
    (tw, th), _ = cv2.getTextSize(text, font, 0.45, 1)

    bg_y1 = y1 - th - padding * 2
    bg_y2 = y1

    if bg_y1 < 0:
        bg_y1 = y1
        bg_y2 = y1 + th + padding * 2

    cv2.rectangle(frame, (x1, bg_y1), (x1 + tw + padding * 2, bg_y2), bg_color, -1)
    cv2.putText(frame, text, (x1 + padding, bg_y2 - padding), font, 0.45, (255, 255, 255), 1, cv2.LINE_AA)


# ========= DB (SQLite) =========
def create_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS traffic_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cctv_id TEXT NOT NULL,
            date TEXT NOT NULL,
            hour TEXT NOT NULL,
            samples INTEGER NOT NULL,
            car INTEGER NOT NULL,
            motorcycle INTEGER NOT NULL,
            bus INTEGER NOT NULL,
            truck INTEGER NOT NULL
        )
    ''')

    # biar 1 record per (cctv_id, date, hour)
    cursor.execute('''
        CREATE UNIQUE INDEX IF NOT EXISTS uq_traffic_key
        ON traffic_data (cctv_id, date, hour)
    ''')

    conn.commit()
    conn.close()


def migrate_old_ids_to_prefixed():
    """
    Jika ada data lama yang tersimpan cctv_id = '1' tanpa prefix,
    kita ubah jadi 'cctv_1' biar konsisten.
    """
    if not USE_PREFIX_CCTV:
        return

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        UPDATE traffic_data
        SET cctv_id = 'cctv_' || cctv_id
        WHERE cctv_id NOT LIKE 'cctv_%'
    """)
    conn.commit()
    conn.close()


def update_traffic_db(cctv_id, counts):
    cctv_id = normalize_cctv_id(cctv_id)
    now = datetime.now()

    row = {
        "cctv_id": cctv_id,
        "date": now.strftime("%Y-%m-%d"),
        "hour": now.strftime("%H"),  # "07", "12", dst (2 digit)
        "samples": 1,
        "car": counts.get("car", 0),
        "motorcycle": counts.get("motorcycle", 0),
        "bus": counts.get("bus", 0),
        "truck": counts.get("truck", 0)
    }

    with db_lock:
        conn = None
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()

            cursor.execute("""
                SELECT id FROM traffic_data
                WHERE cctv_id = ? AND date = ? AND hour = ?
            """, (row["cctv_id"], row["date"], row["hour"]))
            existing = cursor.fetchone()

            if existing:
                cursor.execute("""
                    UPDATE traffic_data
                    SET samples = samples + 1,
                        car = car + ?,
                        motorcycle = motorcycle + ?,
                        bus = bus + ?,
                        truck = truck + ?
                    WHERE cctv_id = ? AND date = ? AND hour = ?
                """, (
                    row["car"], row["motorcycle"], row["bus"], row["truck"],
                    row["cctv_id"], row["date"], row["hour"]
                ))
            else:
                cursor.execute("""
                    INSERT INTO traffic_data (cctv_id, date, hour, samples, car, motorcycle, bus, truck)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    row["cctv_id"], row["date"], row["hour"], row["samples"],
                    row["car"], row["motorcycle"], row["bus"], row["truck"]
                ))

            conn.commit()
            log(cctv_id, "info", f"Saved: {row['cctv_id']} {row['date']} {row['hour']} counts={counts}")

        except Exception as e:
            log(cctv_id, "error", f"DB ERROR: {str(e)}")
            log(cctv_id, "error", traceback.format_exc())
            if conn:
                conn.rollback()
        finally:
            if conn:
                conn.close()


# ========= CCTV PROCESS =========
def run_cctv(cctv_id, hls_url):
    cctv_id = normalize_cctv_id(cctv_id)

    try:
        log(cctv_id, "info", "Starting CCTV process")
        model = load_yolo()

        out_dir = f"output/{cctv_id}"
        os.makedirs(out_dir, exist_ok=True)
        out_hls = os.path.join(out_dir, "output.m3u8")

        frame_size = WIDTH * HEIGHT * 3
        last_count_time = time.time()

        ffmpeg_in = [
            "ffmpeg", "-threads", "1",
            "-loglevel", "error",
            "-fflags", "nobuffer",
            "-flags", "low_delay",
            "-i", hls_url,
            "-vf", f"scale={WIDTH}:{HEIGHT}",
            "-pix_fmt", "bgr24",
            "-f", "rawvideo", "-"
        ]

        ffmpeg_out = [
            "ffmpeg", "-y",
            "-loglevel", "error",
            "-f", "rawvideo",
            "-pix_fmt", "bgr24",
            "-s", f"{WIDTH}x{HEIGHT}",
            "-r", "12",
            "-i", "-",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-tune", "zerolatency",
            "-g", "24",
            "-sc_threshold", "0",
            "-f", "hls",
            "-hls_time", "1",
            "-hls_list_size", "5",
            "-hls_flags", "delete_segments+independent_segments",
            out_hls
        ]

        while True:
            try:
                log(cctv_id, "info", "CONNECTING to stream...")
                pipe_in = subprocess.Popen(ffmpeg_in, stdout=subprocess.PIPE)
                pipe_out = subprocess.Popen(ffmpeg_out, stdin=subprocess.PIPE)
                log(cctv_id, "info", "CONNECTED")

                frame_id = 0
                last_boxes = []

                while True:
                    raw = pipe_in.stdout.read(frame_size)
                    if not raw or len(raw) != frame_size:
                        raise RuntimeError("Stream lost")

                    frame = np.frombuffer(raw, np.uint8).reshape((HEIGHT, WIDTH, 3)).copy()
                    frame_id += 1

                    if frame_id % FRAME_SKIP == 0:
                        results = model.predict(
                            frame,
                            imgsz=416,
                            conf=CONF_TH,
                            device="cpu",
                            verbose=False
                        )[0]

                        last_boxes.clear()
                        counts = {k: 0 for k in VEHICLE_CLASSES}

                        for box in results.boxes:
                            cls_id = int(box.cls[0])
                            label = model.names[cls_id]
                            if label not in VEHICLE_CLASSES:
                                continue

                            x1, y1, x2, y2 = map(int, box.xyxy[0])
                            conf = float(box.conf[0])

                            last_boxes.append((x1, y1, x2, y2, label, conf))
                            counts[label] += 1

                        if time.time() - last_count_time >= COUNT_INTERVAL:
                            update_traffic_db(cctv_id, counts)
                            last_count_time = time.time()

                    for x1, y1, x2, y2, label, conf in last_boxes:
                        draw_modern_box(frame, x1, y1, x2, y2, label, conf)

                    pipe_out.stdin.write(frame.tobytes())

            except Exception as e:
                log(cctv_id, "error", f"STREAM ERROR: {str(e)}")
                log(cctv_id, "error", traceback.format_exc())
                log(cctv_id, "warning", "RECONNECTING in 3 seconds...")
                time.sleep(3)

    except Exception as e:
        log(cctv_id, "error", f"FATAL ERROR: {str(e)}")
        log(cctv_id, "error", traceback.format_exc())


# ========= MAIN =========
def run_flask():
    logging.info("Starting Flask API on port 6327")
    app.run(host="0.0.0.0", port=6327, debug=False, use_reloader=False)


if __name__ == "__main__":
    logging.info("System starting...")

    with open("config/videos.json") as f:
        cctvs = json.load(f)

    create_db()
    migrate_old_ids_to_prefixed()

    threading.Thread(target=run_flask, daemon=True).start()

    processes = []
    for cam in cctvs[:MAX_PROC]:
        # pastikan yang masuk proses sudah cctv_<id>
        cam_id = normalize_cctv_id(cam["id"])
        p = Process(target=run_cctv, args=(cam_id, cam["hls"]))
        p.start()
        processes.append(p)

    for p in processes:
        p.join()