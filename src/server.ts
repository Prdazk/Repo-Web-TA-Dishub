import WebSocket from "ws";
import { spawn, ChildProcessWithoutNullStreams } from "child_process";
import fs from "fs";
import path from "path";
import createApp from "./app";
import dotenv from "dotenv";

dotenv.config();
const app = createApp();

const STREAM_FPS = Number(process.env.STREAM_FPS) || 20;
const STREAM_HEIGHT = Number(process.env.STREAM_HEIGHT) || 360;

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => {
  console.log(`Server running http://localhost:${PORT}`);
});

const configPath = path.join(process.cwd(), "config", "cctv.json");
const config = fs.existsSync(configPath)
  ? JSON.parse(fs.readFileSync(configPath, "utf-8"))
  : { streams: [] };

interface StreamConfig {
  id: string;
  ws_url: string;
  lokasi: string;
}
if(!process.env.HLS_CONVERTER_STOP) {
const streams: StreamConfig[] = config.streams || [];

const ensureDir = (d: string) =>
  !fs.existsSync(d) && fs.mkdirSync(d, { recursive: true });

const activeCleanups: Array<() => void> = [];

function startStream({ id, ws_url, lokasi }: StreamConfig) {
  if (!id || !ws_url) return;

  const outputDir = path.join(process.cwd(), "output", `cctv_${id}`);
  const thumbDir = path.join(process.cwd(), "public", "thumb", `cctv_${id}`);

  ensureDir(outputDir);
  ensureDir(thumbDir);

  const playlist = path.join(outputDir, "output.m3u8").replace(/\\/g, "/");
  const thumb = path.join(thumbDir, "latest.jpg");

  console.log(`▶ ${lokasi} (${id})`);

  let ffmpeg: ChildProcessWithoutNullStreams | null = null;
  let ws: WebSocket | null = null;
  let reconnectTimer: NodeJS.Timeout | null = null;
  let ffmpegRestartTimer: NodeJS.Timeout | null = null;
  let isDown = false;            // status: sedang gagal connect berturut-turut?
  let confirmUpTimer: NodeJS.Timeout | null = null; // konfirmasi koneksi benar2 stabil sebelum dianggap pulih

  /* ===================== FFMPEG ===================== */
  function startFFmpeg() {
    if (ffmpeg) {
      try { ffmpeg.stdin.destroy(); ffmpeg.kill("SIGKILL"); } catch {}
      ffmpeg = null;
    }

    ffmpeg = spawn("ffmpeg", [
      "-loglevel", "error",

      "-fflags", "nobuffer+discardcorrupt",
      "-flags", "low_delay",
      "-analyzeduration", "100000",
      "-probesize", "100000",
      "-err_detect", "ignore_err",
      "-f", "mpegts",
      "-i", "pipe:0",

      // DROP FRAME
      "-vsync", "drop",

      // FPS + SCALE
      "-r", String(STREAM_FPS),
      "-vf", `scale=-2:${STREAM_HEIGHT}`,

      // ENCODE
      "-c:v", "libx264",
      "-preset", "ultrafast",
      "-tune", "zerolatency",
      "-profile:v", "baseline",
      "-pix_fmt", "yuv420p",
      "-x264opts", `keyint=${STREAM_FPS}:min-keyint=${STREAM_FPS}:no-scenecut`,
      "-bf", "0",

      // HLS
      "-f", "hls",
      "-hls_time", "1",
      "-hls_list_size", "3",
      "-hls_flags", "delete_segments+independent_segments+omit_endlist",
      "-hls_allow_cache", "0",
      "-hls_delete_threshold", "1",
      "-hls_segment_filename",
      path.join(outputDir, "seg_%03d.ts").replace(/\\/g, "/"),
      playlist
    ]);

    ffmpeg.stderr.on("data", (data) => {
      const msg = data.toString().trim();
      if (
        !msg.includes("Invalid frame dimensions") &&
        !msg.includes("File not found") &&
        !msg.includes("No such file")
      ) {
        console.warn(`[FFmpeg ${id}] ${msg}`);
      }
    });

    // ✅ Auto restart kalau FFmpeg mati
    ffmpeg.on("exit", (code) => {
    console.warn(`⚠ FFmpeg exit (${id}) code=${code}, restart dalam 2 detik...`);
    ffmpeg = null;
    if (ffmpegRestartTimer) return;
    ffmpegRestartTimer = setTimeout(() => {
      ffmpegRestartTimer = null;
      startFFmpeg();
    }, 2000);
  });
  }

  /* ===================== WS RECONNECT ===================== */
  let downLogTimer: NodeJS.Timeout | null = null;
  const DOWN_GRACE_MS = 10_000; // toleransi sebelum dianggap benar2 mati

  function connectWS() {
    if (ws && ws.readyState === WebSocket.OPEN) return;

    ws = new WebSocket(ws_url, { perMessageDeflate: false });

    ws.on("open", () => {
      // Reconnect berhasil sebelum sempat dianggap "mati" -> batalkan log down, diam saja
      if (downLogTimer) { clearTimeout(downLogTimer); downLogTimer = null; }

      if (confirmUpTimer) clearTimeout(confirmUpTimer);
      confirmUpTimer = setTimeout(() => {
        confirmUpTimer = null;
        if (isDown) {
          console.log(`✅ WS pulih kembali (${id})`);
        }
        isDown = false;
      }, 5000);
    });

    ws.on("message", (d) => {
      try {
        if (ffmpeg && ffmpeg.stdin.writable) {
          ffmpeg.stdin.write(d as Buffer);
        }
      } catch {}
    });

    ws.on("close", () => {
      if (confirmUpTimer) { clearTimeout(confirmUpTimer); confirmUpTimer = null; }
      scheduleDownLog();
      scheduleReconnect();
    });

    ws.on("error", () => {
      if (confirmUpTimer) { clearTimeout(confirmUpTimer); confirmUpTimer = null; }
      scheduleDownLog();
      scheduleReconnect();
    });
  }

  function scheduleDownLog() {
    // Sudah tercatat down, atau sudah dijadwalkan -> tidak perlu ulang
    if (isDown || downLogTimer) return;
    downLogTimer = setTimeout(() => {
      downLogTimer = null;
      isDown = true;
      console.warn(`⚠ WS terputus (${id}), reconnect otomatis di background...`);
    }, DOWN_GRACE_MS);
  }

  function scheduleReconnect() {
    if (reconnectTimer) return;
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null;
      connectWS();
    }, 1000);
  }

  // ✅ Start keduanya
  startFFmpeg();
  connectWS();

  /* ===================== THUMB ===================== */
  const thumbInterval = setInterval(() => {
    if (!fs.existsSync(playlist)) return;
    spawn("ffmpeg", [
      "-loglevel", "error",
      "-y",
      "-i", playlist,
      "-frames:v", "1",
      "-q:v", "5",
      thumb
    ]);
  }, 30_000);
  
  /* ===================== CLEANUP ===================== */
  activeCleanups.push(() => {
    console.log(`🛑 Stopping stream (${id})`);
    clearInterval(thumbInterval);
    if (reconnectTimer) clearTimeout(reconnectTimer);
    if (ffmpegRestartTimer) clearTimeout(ffmpegRestartTimer);
    if (confirmUpTimer) clearTimeout(confirmUpTimer);
    if (downLogTimer) clearTimeout(downLogTimer); // tambahan
    try { ws?.removeAllListeners(); ws?.close(); } catch {}
    try { ffmpeg?.stdin.destroy(); ffmpeg?.kill("SIGKILL"); } catch {}
  });
}

/* ===================== START ===================== */
streams.forEach(startStream);

function shutdown(signal: string) {
  console.log(`\n🛑 Menerima ${signal}, mematikan semua stream...`);
  activeCleanups.forEach((cleanup) => cleanup());
  process.exit(0);
}

process.on("SIGINT", () => shutdown("SIGINT"));
process.on("SIGTERM", () => shutdown("SIGTERM"));
}