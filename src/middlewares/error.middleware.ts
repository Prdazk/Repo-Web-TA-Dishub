import { Request, Response, NextFunction } from "express";
import path from "path";

const errorPath = path.join(process.cwd(), "public", "errors");

function now() {
  return new Date().toISOString();
}

export function notFound(req: Request, res: Response) {
  console.warn(
    `[${now()}] 404 NOT FOUND - ${req.method} ${req.originalUrl}`
  );

  res.status(404).sendFile(path.join(errorPath, "404.html"));
}

export function serverError(
  err: any,
  req: Request,
  res: Response,
  next: NextFunction
) {
  console.error(
    `[${now()}] 500 SERVER ERROR - ${req.method} ${req.originalUrl}`,
    "\nMessage:", err?.message
  );

  res
    .status(err.status || 500)
    .sendFile(path.join(errorPath, "500.html"));
}
