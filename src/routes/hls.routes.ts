import { Router } from "express";
import path from "path";
import fs from "fs";

const router = Router();

const hlsPath = path.join(process.cwd(), "output");

router.get("/{*id}", (req: any, res: any, next: any) => {
  const raw = req.params.id;
  const file = Array.isArray(raw) ? raw.join("/") : String(raw);
  // ================= HEADERS =================k
  res.setHeader("Cache-Control", "no-cache");
  res.setHeader("Accept-Ranges", "bytes");

  // security
  if (file.includes("..")) {
    return res.status(403).end();
  }

  const filePath = path.join(hlsPath, file);

  // hanya HLS
  if (!file.endsWith(".m3u8") && !file.endsWith(".ts")) {
    return next();
  }

  // HLS tidak ada → 500
  if (!fs.existsSync(filePath)) {
    const err: any = new Error("HLS file missing");
    err.status = 500;
    return next(err);
  }

  res.sendFile(filePath, (err: any) => {
    if (err) next(err);
  });
});

export default router;
