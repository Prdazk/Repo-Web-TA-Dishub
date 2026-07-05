import express from "express";
import helmet from "helmet";
import rateLimit from "express-rate-limit";
import pagesRouter from "./routes/pages.routes";
import apiRouter from "./routes/api.routes";
import hlsRouter from "./routes/hls.routes";
import { hlsMime } from "./middlewares/mime.middleware";
import { notFound, serverError } from "./middlewares/error.middleware";
import os from "os";
import dotenv from "dotenv";

dotenv.config();

function getServerIP() {
  const nets = os.networkInterfaces();
  const results: string[] = [];

  for (const name of Object.keys(nets)) {
    for (const net of nets[name] || []) {
      if (net.family === "IPv4" && !net.internal) {
        results.push(net.address);
      }
    }
  }
  return results;
}

const ips = getServerIP();
console.log("Server IP: http://" + (ips[0] || "localhost") + ":" + process.env.PORT);

export default function createApp() {
  const app = express();

  app.set("trust proxy", 1); // percaya 1 layer proxy (Cloudflare Tunnel) agar rate limit baca IP asli dengan benar

  app.set("view engine", "ejs");

  // Helmet: harus dipasang PALING AWAL, sebelum static/routes lain
  app.use(
    helmet({
      // Nonaktifkan CSP default dulu supaya tidak memblokir video player / inline script EJS
      // (bisa diaktifkan bertahap nanti setelah sidang, dengan whitelist yang tepat)
      contentSecurityPolicy: false,
      // Izinkan resource (segmen HLS) diakses cross-origin, misal dari IP/port lain
      crossOriginResourcePolicy: { policy: "cross-origin" },
      crossOriginEmbedderPolicy: false,
    })
  );

  // Rate limiting: batasi request ke /api supaya tidak mudah di-spam
  const apiLimiter = rateLimit({
    windowMs: 60 * 1000, // 1 menit
    max: 60, // maksimal 60 request per menit per IP
    standardHeaders: true,
    legacyHeaders: false,
    message: { success: false, message: "Terlalu banyak request, coba lagi nanti." },
  });
  app.use("/api", apiLimiter);

  // FIX: No-cache untuk semua file HLS agar tidak ada delay dari cache
  app.use("/hls", (req, res, next) => {
    res.setHeader("Cache-Control", "no-cache, no-store, must-revalidate");
    res.setHeader("Pragma", "no-cache");
    res.setHeader("Expires", "0");
    next();
  });

  app.use(hlsMime);
  app.use(express.static("public", { maxAge: 0, index: false }));
  app.use("/", pagesRouter);
  app.use("/hls", hlsRouter);
  app.use("/api", apiRouter);
  app.get("/.well-known/appspecific/com.chrome.devtools.json", (_req, res) => {
    res.status(204).end();
  });
  app.use(notFound);
  app.use(serverError);

  return app;
}