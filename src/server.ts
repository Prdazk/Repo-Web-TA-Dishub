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

/* ===================== EXPRESS ===================== */
const PORT = process.env.PORT || 3000;
app.listen(PORT, () => {
  console.log(`Server running http://localhost:${PORT}`);
});

/* ===================== LOAD JSON ===================== */
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

/* ===================== HELPERS ===================== */
const ensureDir = (d: string) =>
  !fs.existsSync(d) && fs.mkdirSync(d, { recursive: true });

/* ===================== STREAM ===================== */
function startStream({ id, ws_url, lokasi }: StreamConfig) {
  if (!id || !ws_url) return;

  const outputDir = path.join(process.cwd(), "output", id);
  const thumbDir = path.join(process.cwd(), "public", "thumb", id);

  ensureDir(outputDir);
  ensureDir(thumbDir);

  const playlist = path.join(outputDir, "output.m3u8");
  const thumb = path.join(thumbDir, "latest.jpg");

  console.log(`▶ ${lokasi} (${id})`);

  /* ===================== FFMPEG (SEKALI SAJA) ===================== */
  const ffmpeg: ChildProcessWithoutNullStreams = spawn("ffmpeg", [
    "-loglevel", "error",

    // INPUT
    "-fflags", "nobuffer",
    "-flags", "low_delay",
    "-analyzeduration", "0",
    "-probesize", "32",
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
    "-x264opts", "keyint=10:min-keyint=10:no-scenecut",
    "-bf", "0",

    // HLS
    "-f", "hls",
    "-hls_time", "0.4",
    "-hls_list_size", "3",
    "-hls_flags", "delete_segments+independent_segments",
    "-hls_allow_cache", "0",
    "-hls_segment_filename",
    path.join(outputDir, "seg_%03d.ts"),
    playlist
  ]);

  ffmpeg.on("exit", code => {
    console.warn(`⚠ FFMPEG exit (${id}) code=${code}`);
  });

  /* ===================== WS RECONNECT ===================== */
  let ws: WebSocket | null = null;
  let reconnectTimer: NodeJS.Timeout | null = null;

  function connectWS() {
    if (ws && ws.readyState === WebSocket.OPEN) return;

    console.log(`🔌 WS connecting (${id})`);
    ws = new WebSocket(ws_url, { perMessageDeflate: false });

    ws.on("open", () => {
      console.log(`✅ WS connected (${id})`);
    });

    ws.on("message", (d) => {
      try {
        if (ffmpeg.stdin.writable) {
          ffmpeg.stdin.write(d as Buffer);
        }
      } catch {
        // skip frame
      }
    });

    ws.on("close", () => {
      console.warn(`⚠ WS closed (${id})`);
      scheduleReconnect();
    });

    ws.on("error", (err) => {
      console.warn(`⚠ WS error (${id}): ${err.message}`);
      scheduleReconnect();
    });
  }

  function scheduleReconnect() {
    if (reconnectTimer) return;

    reconnectTimer = setTimeout(() => {
      reconnectTimer = null;
      connectWS();
    }, 1000); // 1 detik reconnect
  }

  connectWS();

  /* ===================== THUMB ===================== */
  setInterval(() => {
    if (!fs.existsSync(playlist)) return;

    spawn("ffmpeg", [
      "-loglevel", "error",
      "-y",
      "-i", playlist,
      "-frames:v", "1",
      "-q:v", "5",
      thumb
    ]);
  }, 10_000);
}

/* ===================== START ===================== */
streams.forEach(startStream);
}