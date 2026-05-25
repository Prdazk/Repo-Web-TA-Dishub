import express from "express";
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

  app.set("view engine", "ejs");

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
