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
# CONF_TH = 0.45 → batas minimum keyakinan model, objek di bawah 45% diabaikan
CONF_TH = 0.45
FRAME_SKIP = 4
COUNT_INTERVAL = 10

ID_EXPIRE_FRAMES = 15 * FRAME_SKIP
MAX_PROC = 2

# Berapa kali track_id baru harus muncul berturut-turut sebelum
# resmi dihitung sebagai kendaraan baru. Ini meredam kasus ID-swap
# akibat oklusi yang biasanya cuma bikin ID "hantu" muncul 1-2 frame lalu hilang.
CONFIRM_FRAMES = 2
VEHICLE_CLASSES = {"car", "motorcycle", "bus", "truck"}

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

# warna bounding box: mobil=biru, motor=kuning, bus=merah, truk=pink
COLORS = {
    "car": ((255, 100, 0), (200, 70, 0)),
    "motorcycle": ((0, 200, 255), (0, 160, 200)),
    "bus": ((0, 0, 255), (0, 0, 180)),
    "truck": ((180, 0, 255), (140, 0, 200)),
}

def draw_modern_box(frame, x1, y1, x2, y2, label, conf, track_id=None):
    box_color, bg_color = COLORS.get(label, ((200, 200, 200), (120, 120, 120)))
    font = cv2.FONT_HERSHEY_SIMPLEX
    padding = 4

    cv2.rectangle(frame, (x1, y1), (x2, y2), box_color, 2)

    # Baris 1: label + confidence
    text_main = f"{label} {conf:.2f}"
    (tw1, th1), _ = cv2.getTextSize(text_main, font, 0.45, 1)

    # Baris 2: track_id
    text_id = f"id={track_id}" if track_id is not None else "id=?"
    (tw2, th2), _ = cv2.getTextSize(text_id, font, 0.38, 1)

    box_w    = max(tw1, tw2) + padding * 2
    total_h  = th1 + th2 + padding * 3

    bg_y1 = y1 - total_h
    bg_y2 = y1

    if bg_y1 < 0:
        bg_y1 = y1
        bg_y2 = y1 + total_h

    cv2.rectangle(frame, (x1, bg_y1), (x1 + box_w, bg_y2), bg_color, -1)
    cv2.putText(frame, text_main,
                (x1 + padding, bg_y1 + th1 + padding),
                font, 0.45, (255, 255, 255), 1, cv2.LINE_AA)
    cv2.putText(frame, text_id,
                (x1 + padding, bg_y1 + th1 + th2 + padding * 2),
                font, 0.38, (200, 230, 255), 1, cv2.LINE_AA)

# def draw_modern_box(frame, x1, y1, x2, y2, label, conf, track_id=None):
#     box_color, bg_color = COLORS.get(label, ((200, 200, 200), (120, 120, 120)))
#     font = cv2.FONT_HERSHEY_SIMPLEX
#     padding = 4

#     cv2.rectangle(frame, (x1, y1), (x2, y2), box_color, 2)

#     # Baris label + confidence (satu-satunya yang ditampilkan)
#     text_main = f"{label} {conf:.2f}"
#     (tw1, th1), _ = cv2.getTextSize(text_main, font, 0.45, 1)

#     box_w   = tw1 + padding * 2
#     total_h = th1 + padding * 2

#     bg_y1 = y1 - total_h
#     bg_y2 = y1
#     if bg_y1 < 0:
#         bg_y1 = y1
#         bg_y2 = y1 + total_h

#     cv2.rectangle(frame, (x1, bg_y1), (x1 + box_w, bg_y2), bg_color, -1)
#     cv2.putText(frame, text_main,
#                 (x1 + padding, bg_y2 - padding),
#                 font, 0.45, (255, 255, 255), 1, cv2.LINE_AA)

#     # track_id sengaja tidak digambar, tapi tetap diterima sebagai parameter
#     # supaya pemanggil di run_cctv() tidak perlu diubah — tracking/counting tetap normal
#     _ = track_id

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

        out_dir = f"output/{cctv_id}_det"
        os.makedirs(out_dir, exist_ok=True)
        out_hls = os.path.join(out_dir, "output.m3u8")

        frame_size = WIDTH * HEIGHT * 3

        ffmpeg_in = [
            "ffmpeg", "-threads", "2",
            "-loglevel", "error",
            "-fflags", "nobuffer+discardcorrupt",
            "-flags", "low_delay",
            "-analyzeduration", "100000",
            "-probesize", "100000",
            "-i", hls_url,
            "-vf", f"scale={WIDTH}:{HEIGHT}",
            "-pix_fmt", "bgr24",
            "-vsync", "drop",
            "-f", "rawvideo", "-"
        ]

        ffmpeg_out = [
            "ffmpeg", "-y",
            "-loglevel", "error",
            "-fflags", "nobuffer",
            "-f", "rawvideo",
            "-pix_fmt", "bgr24",
            "-s", f"{WIDTH}x{HEIGHT}",
            "-r", "8",
            "-i", "-",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-tune", "zerolatency",
            "-profile:v", "baseline",
            "-pix_fmt", "yuv420p",
            "-g", "8",
            "-sc_threshold", "0",
            "-bufsize", "256k",
            "-maxrate", "600k",
            "-f", "hls",
            "-hls_time", "1",
            "-hls_list_size", "3",
            "-hls_flags", "independent_segments+delete_segments+omit_endlist",
            "-hls_delete_threshold", "1",
            "-hls_allow_cache", "0",
            "-hls_segment_filename", os.path.join(out_dir, "seg_%03d.ts"),
            out_hls
        ]

        # Load total hari ini dari DB saat startup/restart
        def _load_today(cid):
            today = datetime.now().strftime("%Y-%m-%d")
            try:
                conn = sqlite3.connect(DB_PATH)
                cur = conn.cursor()
                cur.execute("""
                    SELECT COALESCE(SUM(car),0), COALESCE(SUM(motorcycle),0),
                        COALESCE(SUM(bus),0), COALESCE(SUM(truck),0)
                    FROM traffic_data WHERE cctv_id = ? AND date = ?
                """, (cid, today))
                row = cur.fetchone()
                conn.close()
                return {"car": row[0], "motorcycle": row[1], "bus": row[2], "truck": row[3]}
            except:
                return {k: 0 for k in VEHICLE_CLASSES}

        total_counts        = _load_today(cctv_id)
        fallback_count      = 0
        counted_ids         = set()
        # Dedup buffer GABUNGAN: dipakai baik oleh jalur ID (ByteTrack) maupun fallback,
        # supaya kendaraan yang sama tidak terhitung dua kali hanya karena
        # sumber deteksinya berpindah (fallback -> dapat ID, atau ID -> sempat hilang -> fallback)
        recent_counted       = []  # isi: (cx, cy, label, frame_id)
        log(cctv_id, "info", f"Resumed today totals: {total_counts}")
        DEDUP_DIST_TH        = 50   # pixel — jarak minimum antar centroid beda kendaraan
        DEDUP_EXPIRE         = 15   # frame — entri lama dihapus setelah N frame

        pipe_out = subprocess.Popen(ffmpeg_out, stdin=subprocess.PIPE)

        # ── RECONNECT COUNTER ──
        session_id = 0

        # ── SAFETY INIT ────────────────────────────────────────────
        # Inisialisasi di sini agar blok except tidak NameError
        # jika exception terjadi sebelum loop dalam sempat jalan
        interval_counts = {k: 0 for k in VEHICLE_CLASSES}
        # ───────────────────────────────────────────────────────────

        while True:
            try:
                log(cctv_id, "info", "CONNECTING to stream...")
                pipe_in = subprocess.Popen(ffmpeg_in, stdout=subprocess.PIPE)
                log(cctv_id, "info", f"CONNECTED (session={session_id})")

                frame_id = 0
                last_boxes = []

                id_tracker         = {}
                pending_hits       = {}   # track_id baru yang belum lolos konfirmasi
                interval_counts    = {k: 0 for k in VEHICLE_CLASSES}
                recent_counted     = []
                last_count_time    = time.time()

                while True:
                    raw = pipe_in.stdout.read(frame_size)
                    if not raw or len(raw) != frame_size:
                        raise RuntimeError("Stream lost")

                    frame = np.frombuffer(raw, np.uint8).reshape((HEIGHT, WIDTH, 3)).copy()
                    frame_id += 1

                    if frame_id % FRAME_SKIP == 0:
                        # conf < 0.45 langsung dibuang sebelum dapat ID
                        results = model.track(
                            frame,
                            imgsz=640,
                            conf=CONF_TH,
                            device="cpu",
                            persist=True,
                            tracker="bytetrack.yaml",  # algoritma tracking, sumber pemberi ID per kendaraan
                            verbose=False
                        )[0]

                        last_boxes.clear()

                        # Bersihkan entri dedup yang sudah kadaluarsa (sekali per frame deteksi)
                        recent_counted[:] = [
                            c for c in recent_counted
                            if (frame_id - c[3]) <= DEDUP_EXPIRE
                        ]

                        for box in results.boxes:
                            cls_id = int(box.cls[0])
                            label  = model.names[cls_id]
                            if label not in VEHICLE_CLASSES:
                                continue

                            x1, y1, x2, y2 = map(int, box.xyxy[0])
                            conf = float(box.conf[0])
                            tid = int(box.id[0]) if box.id is not None else None
                            last_boxes.append((x1, y1, x2, y2, label, conf, tid))

                            cx = (x1 + x2) // 2
                            cy = (y1 + y2) // 2

                            # Cari entri dedup terdekat — ini kunci fix double count:
                            # dicek lintas sumber (ID maupun fallback), bukan terpisah lagi
                            match_idx = None
                            for idx, c in enumerate(recent_counted):
                                if label == c[2] and abs(cx - c[0]) < DEDUP_DIST_TH and abs(cy - c[1]) < DEDUP_DIST_TH:
                                    match_idx = idx
                                    break

                            if box.id is not None:
                                track_id = int(box.id[0])

                                # tiap kendaraan dapat ID unik dari ByteTrack
                                # digabung session agar tidak bentrok saat reconnect
                                unique_key = (session_id, track_id)

                                # Update kapan terakhir ID ini muncul di frame
                                id_tracker[track_id] = {
                                    "label": label,
                                    "last_frame": frame_id
                                }

                                if unique_key in counted_ids:
                                    # ID ini sudah pernah dihitung → cukup refresh posisi terakhirnya
                                    if match_idx is not None:
                                        recent_counted[match_idx] = (cx, cy, label, frame_id)
                                elif match_idx is not None:
                                    # Kendaraan ini SEBELUMNYA sudah dihitung lewat fallback
                                    # (saat itu belum punya ID) → JANGAN dihitung dua kali,
                                    # cukup daftarkan ID-nya supaya frame berikutnya konsisten
                                    counted_ids.add(unique_key)
                                    recent_counted[match_idx] = (cx, cy, label, frame_id)
                                    log(cctv_id, "info",
                                        f"ID ASSIGNED (skip recount, sudah dihitung via fallback) "
                                        f"session={session_id} id={track_id} label={label} conf={conf:.2f}")
                                else:
                                    # Track baru → JANGAN langsung dihitung.
                                    # Tunggu track_id ini muncul CONFIRM_FRAMES kali berturut-turut
                                    # dulu, biar ID hantu akibat oklusi/ID-swap tidak ikut terhitung.
                                    pending_hits[track_id] = pending_hits.get(track_id, 0) + 1

                                    if pending_hits[track_id] >= CONFIRM_FRAMES:
                                        counted_ids.add(unique_key)
                                        interval_counts[label] += 1
                                        total_counts[label]    += 1
                                        recent_counted.append((cx, cy, label, frame_id))
                                        pending_hits.pop(track_id, None)
                                        log(cctv_id, "info",
                                            f"NEW vehicle session={session_id} id={track_id} label={label} "
                                            f"conf={conf:.2f} total={sum(total_counts.values())}")
                                    else:
                                        log(cctv_id, "info",
                                            f"PENDING id={track_id} label={label} "
                                            f"hit={pending_hits[track_id]}/{CONFIRM_FRAMES}")
                            else:
                                if match_idx is not None:
                                    # Sudah pernah dihitung sebelumnya (via ID ATAU fallback) → skip
                                    recent_counted[match_idx] = (cx, cy, label, frame_id)
                                    log(cctv_id, "info",
                                        f"FALLBACK SKIP duplicate centroid label={label} cx={cx} cy={cy}")
                                else:
                                    recent_counted.append((cx, cy, label, frame_id))
                                    interval_counts[label] += 1
                                    total_counts[label]    += 1
                                    fallback_count         += 1
                                    log(cctv_id, "warning",
                                        f"FALLBACK (no track_id) label={label} conf={conf:.2f} "
                                        f"cx={cx} cy={cy} total_fallback={fallback_count}")

                        expired_ids = [
                            tid for tid, info in id_tracker.items()
                            if (frame_id - info["last_frame"]) > ID_EXPIRE_FRAMES
                        ]
                        for tid in expired_ids:
                            del id_tracker[tid]
                            pending_hits.pop(tid, None)

                        if len(counted_ids) > 10000:
                            # active_keys = semua (session_id, track_id) yang masih aktif di frame ini
                            active_keys  = {(session_id, tid) for tid in id_tracker.keys()}
                            safe_to_trim = counted_ids - active_keys
                            trim_count   = max(0, len(counted_ids) - 8000)
                            if trim_count > 0 and safe_to_trim:
                                to_remove = set(list(safe_to_trim)[:trim_count])
                                counted_ids -= to_remove
                                log(cctv_id, "warning",
                                    f"counted_ids trimmed {trim_count} inactive keys → sisa {len(counted_ids)}")

                        shared_counts[cctv_id] = {
                            "car":           total_counts["car"],
                            "motorcycle":    total_counts["motorcycle"],
                            "bus":           total_counts["bus"],
                            "truck":         total_counts["truck"],
                            "unique_ids":    len(counted_ids),
                            "fallback_count": fallback_count,
                            "timestamp":     time.time()
                        }

                        if time.time() - last_count_time >= COUNT_INTERVAL:
                            snapshot = dict(interval_counts)
                            interval_counts = {k: 0 for k in VEHICLE_CLASSES}
                            if any(v > 0 for v in snapshot.values()):  # jangan simpan jika semua 0
                                update_traffic_db(cctv_id, snapshot)
                                log(cctv_id, "info",
                                    f"DB saved interval={snapshot} "
                                    f"total={total_counts} fallback={fallback_count}")

                    for x1, y1, x2, y2, label, conf, tid in last_boxes:
                        draw_modern_box(frame, x1, y1, x2, y2, label, conf, tid)

                    if pipe_out.stdin and not pipe_out.stdin.closed:
                        pipe_out.stdin.write(frame.tobytes())
                        pipe_out.stdin.flush()

            except Exception as e:
                log(cctv_id, "error", f"STREAM ERROR: {str(e)}")
                log(cctv_id, "error", traceback.format_exc())
                log(cctv_id, "warning", "RECONNECTING in 3 seconds...")

                # Simpan sisa interval_counts yang belum sempat disimpan ke DB
                if any(v > 0 for v in interval_counts.values()):
                    update_traffic_db(cctv_id, interval_counts)
                    log(cctv_id, "warning", f"Flushed interval on disconnect: {interval_counts}")

                try:
                    pipe_in.kill()
                    pipe_in.wait()
                except (NameError, Exception):
                    pass  # pipe_in belum terdefinisi atau sudah mati

                # ── NAIKKAN SESSION ID ─────────────────────────────────────
                # ByteTrack akan reset ID dari 1 lagi setelah reconnect.
                # Dengan menaikkan session_id, semua ID lama otomatis
                # berbeda namespace → tidak ada collision, tidak ada miss count
                session_id += 1
                log(cctv_id, "info", f"Session naik → session_id={session_id}")
                # ──────────────────────────────────────────────────────────

                # pipe_out JANGAN di-kill saat reconnect input
                # biarkan tetap hidup supaya segment counter tidak reset
                time.sleep(3)

    except Exception as e:
        log(cctv_id, "error", f"FATAL ERROR: {str(e)}")
        log(cctv_id, "error", traceback.format_exc())

def run_flask(shared_counts):
    logging.info("Starting Flask API on port 6327")
    from src.routes.apiSignal import set_shared_counts
    set_shared_counts(shared_counts)
    app.run(host="127.0.0.1", port=6327, debug=False, use_reloader=False)


if __name__ == "__main__":
    logging.info("System starting...")

    with open("config/cctv.json") as f:
        config = json.load(f)
        cctvs = config["streams"]

    create_db()
    migrate_old_ids_to_prefixed()

    manager = Manager()
    shared_counts = manager.dict()

    threading.Thread(target=run_flask, args=(shared_counts,), daemon=True).start()

    processes = []
    for cam in cctvs[:MAX_PROC]:
        cam_id = normalize_cctv_id(cam["id"])
        hls_url = f"http://localhost:3000/hls/cctv_{cam['id']}/output.m3u8"
        p = Process(target=run_cctv, args=(cam_id, hls_url, shared_counts))
        p.start()
        processes.append(p)

    for p in processes:
        p.join()