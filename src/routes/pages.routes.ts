import { Router } from "express";
import fs from "fs";
import path from "path";

const router = Router();

router.get("/", (req, res) => {
  const jsonPath = path.join(process.cwd(), "config", "cctv.json");
  const data = JSON.parse(fs.readFileSync(jsonPath, "utf-8"));

  const streams = data.streams.map((s: any) => ({
    id: s.id,
    lokasi: s.lokasi,
    hls: `/hls/${s.id}/output.m3u8`,
    ws_url: s.ws_url,
    coordinate: s.coordinate || null,
  }));

  res.render("pages/cctv", {
    title: "CCTV Monitoring",
    streams
  });
});

router.get("/monitor/:id", (req, res) => {
  const id = req.params.id;
  const jsonPath = path.join(process.cwd(), "config", "cctv.json");
  try {
  const data = JSON.parse(fs.readFileSync(jsonPath, "utf-8"));
  const stream = data.streams.find((s: any) => s.id === id);
  
   res.render("pages/monitor", {
    title: `Monitor CCTV ${stream.lokasi}`,
    hls: `/hls/${stream.id}/output.m3u8`,
    hls_o: `/hls/cctv_${stream.id}/output.m3u8`,
    stream: {
      id: stream.id,
      lokasi: stream.lokasi,
      hls: `/hls/${stream.id}/output.m3u8`,
      ws_url: stream.ws_url,
      coordinate: stream.coordinate || null
    }
  });
  } catch(e) {
    res.status(404).send();
  }
});

export default router;
