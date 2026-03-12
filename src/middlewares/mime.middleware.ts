import { Request, Response, NextFunction } from "express";

export function hlsMime(req: Request, res: Response, next: NextFunction) {
  if (req.url.endsWith(".m3u8")) {
    res.setHeader("Content-Type", "application/vnd.apple.mpegurl");
  }

  if (req.url.endsWith(".ts")) {
    res.setHeader("Content-Type", "video/mp2t");
  }

  next();
}
