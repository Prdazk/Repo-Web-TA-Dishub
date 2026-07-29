import { Router } from "express";
import path from "path";
import fs from "fs";

const router = Router();
const hlsPath = path.join(process.cwd(), "output");

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

  if (file.endsWith(".m3u8")) {
  res.setHeader("Cache-Control", "no-cache, no-store, must-revalidate");
  res.setHeader("Pragma", "no-cache");
  res.setHeader("Expires", "0");
  } else {
    res.setHeader("Cache-Control", "no-cache");
  }
 
  if (file.endsWith(".m3u8")) {
    res.setHeader("Accept-Ranges", "none");
  } else {
    res.setHeader("Accept-Ranges", "bytes");
  }

  if (file.includes("..")) {
    return res.status(403).end();
  }

  if (!file.endsWith(".m3u8") && !file.endsWith(".ts")) {
    return next();
  }

  const filePath = path.join(hlsPath, file);

  const found = file.endsWith(".ts")
    ? await waitForFile(filePath, 3000)
    : fs.existsSync(filePath);

  if (!found) {
    return res.status(404).end();
  }

  res.sendFile(filePath, (err: any) => {
    if (err && !res.headersSent) next(err);
  });
});

export default router;