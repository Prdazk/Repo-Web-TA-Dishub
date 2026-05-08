import { Router } from "express";
import path from "path";
import fs from "fs";

const router = Router();
const hlsPath = path.join(process.cwd(), "output");

// Tunggu file muncul, max `maxMs` milidetik
function waitForFile(filePath: string, maxMs = 3000, intervalMs = 100): Promise<boolean> {
  return new Promise((resolve) => {
    if (fs.existsSync(filePath)) return resolve(true);

    let elapsed = 0;
    const timer = setInterval(() => {
      elapsed += intervalMs;
      if (fs.existsSync(filePath)) {
        clearInterval(timer);
        return resolve(true);
      }
      if (elapsed >= maxMs) {
        clearInterval(timer);
        return resolve(false);
      }
    }, intervalMs);
  });
}

router.get("/{*id}", async (req: any, res: any, next: any) => {
  const raw = req.params.id;
  const file = Array.isArray(raw) ? raw.join("/") : String(raw);

  res.setHeader("Cache-Control", "no-cache");
  res.setHeader("Accept-Ranges", "bytes");

  // Security
  if (file.includes("..")) {
    return res.status(403).end();
  }

  // Hanya HLS
  if (!file.endsWith(".m3u8") && !file.endsWith(".ts")) {
    return next();
  }

  const filePath = path.join(hlsPath, file);

  // Untuk .ts → tunggu dulu sebelum nyerah
  // Untuk .m3u8 → langsung cek (playlist harus sudah ada)
  const found = file.endsWith(".ts")
    ? await waitForFile(filePath, 3000)
    : fs.existsSync(filePath);

  if (!found) {
    // 404 lebih tepat dari 500 (file memang tidak ada, bukan server crash)
    console.warn(`[HLS] File not found: ${file}`);
    return res.status(404).end();
  }

  res.sendFile(filePath, (err: any) => {
    if (err && !res.headersSent) next(err);
  });
});

export default router;