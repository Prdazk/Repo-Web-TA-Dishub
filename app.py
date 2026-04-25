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
from multiprocessing import Process, Lock, Manager
from ultralytics import YOLO
import threading
import sqlite3
from src.routes.apiSignal import app

# ================= CONFIG =================
MODEL_PATH = "models/yolov8n.pt"
DB_PATH = "db/traffic.db"

WIDTH = 640
HEIGHT = 360
# 1. CONF_TH = 0.35  → batas minimum keyakinan model, objek di bawah 35% diabaikan
CONF_TH = 0.45
FRAME_SKIP = 5
COUNT_INTERVAL = 10

# Berapa frame sebuah ID boleh "hilang" sebelum dianggap keluar
ID_EXPIRE_FRAMES = 15
MAX_PROC = 2

VEHICLE_CLASSES = {"car", "motorcycle", "bus", "truck"}

# Memori tracking ID per kamera — 1 kendaraan dihitung 1 kali
tracked_ids_per_cctv = {}

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

    # gambar bounding box (kotak persegi) di sekitar objek yang terdeteksi
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
def run_cctv(cctv_id, hls_url, shared_counts):
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
            "-hls_time", "2",
            "-hls_list_size", "10",
            "-hls_flags", "independent_segments",
            "-hls_delete_threshold", "10",
            out_hls
        ]

        # total_counts & fallback_count di luar loop reconnect
        # supaya tidak reset saat stream putus lalu konek ulang
        total_counts    = {k: 0 for k in VEHICLE_CLASSES}
        fallback_count  = 0
        counted_ids     = set()

        while True:
            try:
                log(cctv_id, "info", "CONNECTING to stream...")
                pipe_in = subprocess.Popen(ffmpeg_in, stdout=subprocess.PIPE)
                pipe_out = subprocess.Popen(ffmpeg_out, stdin=subprocess.PIPE)
                log(cctv_id, "info", "CONNECTED")

                frame_id = 0
                last_boxes = []

                id_tracker      = {}
                interval_counts = {k: 0 for k in VEHICLE_CLASSES}

                while True:
                    raw = pipe_in.stdout.read(frame_size)
                    if not raw or len(raw) != frame_size:
                        raise RuntimeError("Stream lost")

                    frame = np.frombuffer(raw, np.uint8).reshape((HEIGHT, WIDTH, 3)).copy()
                    frame_id += 1

                    if frame_id % FRAME_SKIP == 0:
                        results = model.track(
                            frame,
                            imgsz=640,
                            conf=CONF_TH,
                            device="cpu",
                            persist=True,
                            verbose=False
                        )[0]

                        last_boxes.clear()
                        current_frame_ids = set()

                        for box in results.boxes:
                            cls_id = int(box.cls[0])
                            label  = model.names[cls_id]
                            if label not in VEHICLE_CLASSES:
                                continue

                            x1, y1, x2, y2 = map(int, box.xyxy[0])
                            conf = float(box.conf[0])
                            last_boxes.append((x1, y1, x2, y2, label, conf))

                            if box.id is not None:
                                track_id = int(box.id[0])
                                current_frame_ids.add(track_id)

                                # Update kapan terakhir ID ini muncul di frame
                                id_tracker[track_id] = {
                                    "label": label,
                                    "last_frame": frame_id
                                }

                                # Hitung HANYA kalau belum pernah dihitung sama sekali
                                # counted_ids tidak pernah dihapus → anti double count
                                if track_id not in counted_ids:
                                    counted_ids.add(track_id)
                                    interval_counts[label] += 1
                                    total_counts[label]    += 1
                                    log(cctv_id, "info",
                                        f"NEW vehicle id={track_id} label={label} "
                                        f"conf={conf:.2f} total={sum(total_counts.values())}")
                            else:
                                # Fallback: YOLO detect tapi tidak dapat track_id
                                # Hitung ke interval & total, tapi catat sebagai fallback
                                interval_counts[label] += 1
                                total_counts[label]    += 1
                                fallback_count         += 1
                                log(cctv_id, "warning",
                                    f"FALLBACK (no track_id) label={label} conf={conf:.2f} "
                                    f"total_fallback={fallback_count}")

                        # Hapus dari id_tracker kalau sudah lama tidak muncul
                        # TIDAK dihapus dari counted_ids → ini yang fix double count
                        expired_ids = [
                            tid for tid, info in id_tracker.items()
                            if (frame_id - info["last_frame"]) > ID_EXPIRE_FRAMES
                        ]
                        for tid in expired_ids:
                            del id_tracker[tid]
                            # counted_ids.discard(tid) ← DIHAPUS, ini sumber double count

                        if len(counted_ids) > 10000:
                            log(cctv_id, "warning",
                             f"counted_ids besar ({len(counted_ids)}) tapi TIDAK ditrim → anti double count terjaga")

                        # Tulis ke shared dict yang dibaca Flask → Node.js → Frontend
                        # Pakai total_counts (akumulasi) bukan interval_counts
                        # supaya angka di frontend tidak turun/reset tiap 10 detik
                        shared_counts[cctv_id] = {
                            "car":           total_counts["car"],
                            "motorcycle":    total_counts["motorcycle"],
                            "bus":           total_counts["bus"],
                            "truck":         total_counts["truck"],
                            "unique_ids":    len(counted_ids),
                            "fallback_count": fallback_count,
                            "timestamp":     time.time()
                        }

                        # Simpan ke DB tiap COUNT_INTERVAL detik
                        # Hanya interval_counts yang di-reset, bukan total_counts
                        # counted_ids juga TIDAK di-reset → anti double count terjaga
                        if time.time() - last_count_time >= COUNT_INTERVAL:
                            update_traffic_db(cctv_id, interval_counts)
                            log(cctv_id, "info",
                                f"DB saved interval={interval_counts} "
                                f"total={total_counts} fallback={fallback_count}")
                            interval_counts = {k: 0 for k in VEHICLE_CLASSES}
                            last_count_time = time.time()

                    for x1, y1, x2, y2, label, conf in last_boxes:
                        draw_modern_box(frame, x1, y1, x2, y2, label, conf)

                    pipe_out.stdin.write(frame.tobytes())

            except Exception as e:
                log(cctv_id, "error", f"STREAM ERROR: {str(e)}")
                log(cctv_id, "error", traceback.format_exc())
                log(cctv_id, "warning", "RECONNECTING in 3 seconds...")

                # Matikan proses FFmpeg lama agar tidak menumpuk di background
                try:
                    pipe_in.kill()
                    pipe_out.kill()
                    pipe_in.wait()   # tunggu sampai benar-benar mati
                    pipe_out.wait()  # sebelum spawn FFmpeg baru
                except Exception:
                    pass

                time.sleep(3)

    except Exception as e:
        log(cctv_id, "error", f"FATAL ERROR: {str(e)}")
        log(cctv_id, "error", traceback.format_exc())


# ========= MAIN =========
def run_flask(shared_counts):
    logging.info("Starting Flask API on port 6327")
    from src.routes.apiSignal import set_shared_counts
    set_shared_counts(shared_counts)
    app.run(host="0.0.0.0", port=6327, debug=False, use_reloader=False)


if __name__ == "__main__":
    logging.info("System starting...")

    with open("config/videos.json") as f:
        cctvs = json.load(f)

    create_db()
    migrate_old_ids_to_prefixed()

    # Shared dict antar process — ini yang bikin data bisa dibaca Flask
    manager = Manager()
    shared_counts = manager.dict()

    threading.Thread(target=run_flask, args=(shared_counts,), daemon=True).start()

    processes = []
    for cam in cctvs[:MAX_PROC]:
        cam_id = normalize_cctv_id(cam["id"])
        p = Process(target=run_cctv, args=(cam_id, cam["hls"], shared_counts))
        p.start()
        processes.append(p)

    for p in processes:
        p.join()