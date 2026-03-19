import { Router } from "express";
import path from "path";
import fs from "fs";
import Database from "better-sqlite3";

const router = Router();

type CctvInfo = {
  lokasi: string;
  ws_url: string;
};


const USE_SQLITE_DB = true;

const DB_FILE = path.join(process.cwd(), "db", "traffic.db");
const JSON_FILE = path.join(process.cwd(), "db", "traffic.json");
const CCTV_CONFIG = path.join(process.cwd(), "config", "cctv.json");

let sqlite: Database.Database | null = null;
let _cctvMapCache: Map<string, CctvInfo> | null = null; // ← tambah ini

function getDb() {
  if (!sqlite) {
    sqlite = new Database(DB_FILE);
    sqlite.pragma("journal_mode = WAL");
    sqlite.pragma("busy_timeout = 5000");

    sqlite.exec(`
      CREATE TABLE IF NOT EXISTS traffic_data (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cctv_id TEXT NOT NULL,
        date TEXT NOT NULL,
        hour TEXT NOT NULL,
        samples INTEGER NOT NULL,
        car INTEGER NOT NULL,
        motorcycle INTEGER NOT NULL,
        bus INTEGER NOT NULL,
        truck INTEGER NOT NULL
      );
      CREATE INDEX IF NOT EXISTS idx_traffic_lookup ON traffic_data(cctv_id, date, hour);
      CREATE INDEX IF NOT EXISTS idx_traffic_date ON traffic_data(date);
    `);
  }
  return sqlite;
}

// ============================
// SERVER HEALTH
// ============================
router.get("/server-health", async (req, res) => {
  try {
    const response = await fetch("http://localhost:6327/");
    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`);
    }
    const data = await response.json();
    res.json({ status: "ok", data });
  } catch (error: any) {
    console.error("Fetch error:", error);
    res.status(500).json({
      status: "error",
      error: `Detektor tidak merespons, server sedang offline. Pesan: ${error.message}`,
    });
  }
});

// ============================
// HELPERS
// ============================
function loadCctvMap() {
  if (_cctvMapCache) return { cctvMap: _cctvMapCache };

  const cctvConfig = JSON.parse(fs.readFileSync(CCTV_CONFIG, "utf-8"));
  const streams = cctvConfig.streams || [];

  _cctvMapCache = new Map<string, CctvInfo>(
    streams.map((s: any) => [
      "cctv_" + s.id,
      { lokasi: s.lokasi, ws_url: s.ws_url }
    ])
  );

  return { cctvMap: _cctvMapCache };
}

function safeParseInt(v: any, fallback: number) {
  const n = parseInt(String(v), 10);
  return Number.isFinite(n) && n > 0 ? n : fallback;
}

// ============================
// ROUTE /db/list
// ============================
router.get("/db/list", (req: any, res: any) => {
  try {
    const { cctvMap } = loadCctvMap();

    const { ids, page = 1, limit = 10, startDate, endDate } = req.query;

    const pageNumber = safeParseInt(page, 1);
    const limitNumber = safeParseInt(limit, 10);
    const offset = (pageNumber - 1) * limitNumber;

    // Frontend kamu kirim ids = "1", jadi kita buat "cctv_1"
    // Support multi ID: "1,2" → ["cctv_1", "cctv_2"]
    const idList = ids
      ? String(ids).split(",").map(id => "cctv_" + id.trim())
      : [];

    // ============================
    // SQLITE MODE
    // ============================
    if (USE_SQLITE_DB) {
      const db = getDb();

      const where: string[] = [];
      const params: any[] = [];

      if (idList.length > 0) {
        const placeholders = idList.map(() => "?").join(", ");
        where.push(`cctv_id IN (${placeholders})`);
        params.push(...idList);
      }

      if (startDate) {
        where.push("date >= ?");
        params.push(String(startDate));
      }

      if (endDate) {
        where.push("date <= ?");
        params.push(String(endDate));
      }

      const whereSql = where.length ? `WHERE ${where.join(" AND ")}` : "";

      // TOTAL sesuai filter
      const totalRow = db
        .prepare<any[], { total: number }>(
          `SELECT COUNT(*) as total FROM traffic_data ${whereSql}`
        )
        .get(...params);

      const total = totalRow?.total ?? 0;

      // DATA sesuai filter + pagination
      const rows = db
        .prepare<any[], any>(
          `
          SELECT cctv_id, date, hour, motorcycle, car, bus, truck
          FROM traffic_data
          ${whereSql}
          ORDER BY date DESC, hour DESC
          LIMIT ? OFFSET ?
        `
        )
        .all(...params, limitNumber, offset);

      // MERGE lokasi (biar sama output versi JSON kamu)
      const merged = rows.map((item: any) => {
        const info = cctvMap.get(item.cctv_id);
        const lokasi = info?.lokasi || "Tidak diketahui";

        return {
          id: `${item.cctv_id} - ${lokasi}`,
          date: item.date,
          hour: item.hour,
          motorcycle: item.motorcycle,
          car: item.car,
          bus: item.bus,
          truck: item.truck,
          ws_url: info?.ws_url || null,
        };
      });

      return res.json({
        success: true,
        total,
        page: pageNumber,
        limit: limitNumber,
        data: merged,
        mode: "sqlite",
      });
    }

    // ============================
    // JSON MODE (TESTER)
    // ============================
    const trafficData = JSON.parse(fs.readFileSync(JSON_FILE, "utf-8"));

   let filtered = idList.length > 0
  ? trafficData.filter((d: any) => idList.includes(d.cctv_id))
  : trafficData;

    if (startDate) filtered = filtered.filter((d: any) => d.date >= startDate);
    if (endDate) filtered = filtered.filter((d: any) => d.date <= endDate);

    const merged = filtered.map((item: any) => {
      const info = cctvMap.get(item.cctv_id);
      const lokasi = info?.lokasi || "Tidak diketahui";

      return {
        id: `${item.cctv_id} - ${lokasi}`,
        date: item.date,
        hour: item.hour,
        motorcycle: item.motorcycle,
        car: item.car,
        bus: item.bus,
        truck: item.truck,
        ws_url: info?.ws_url || null,
      };
    });

    const paginatedData = merged.slice(offset, offset + limitNumber);

    return res.json({
      success: true,
      total: merged.length,
      page: pageNumber,
      limit: limitNumber,
      data: paginatedData,
      mode: "json",
    });
  } catch (e: any) {
  return res.status(500).json({
      success: false,
      message: e.message,
    });
  }
});

// ============================
// ROUTE /db/summary
// ============================
router.get("/db/summary", (req: any, res: any) => {
  try {
    const { ids, startDate, endDate } = req.query;

    const idList = ids
      ? String(ids).split(",").map(id => "cctv_" + id.trim())
      : [];

    const db = getDb();
    const where: string[] = [];
    const params: any[] = [];

    if (idList.length > 0) {
      const placeholders = idList.map(() => "?").join(", ");
      where.push(`cctv_id IN (${placeholders})`);
      params.push(...idList);
    }
    if (startDate) { where.push("date >= ?"); params.push(String(startDate)); }
    if (endDate)   { where.push("date <= ?"); params.push(String(endDate)); }

    const whereSql = where.length ? `WHERE ${where.join(" AND ")}` : "";

    const row = db.prepare<any[], any>(`
      SELECT
        COUNT(*) as total_record,
        COALESCE(SUM(motorcycle + car + bus + truck), 0) as total_kendaraan
      FROM traffic_data ${whereSql}
    `).get(...params);

    return res.json({
      success: true,
      total_record: row?.total_record ?? 0,
      total_kendaraan: row?.total_kendaraan ?? 0
    });

  } catch (e: any) {
    return res.status(500).json({ success: false, message: e.message });
  }
});


router.get("/db/jam-arus", (req: any, res: any) => {
  try {
    const { ids, startDate, endDate } = req.query;

    const idList = ids
      ? String(ids).split(",").map(id => "cctv_" + id.trim())
      : [];

    const db = getDb();
    const where: string[] = [];
    const params: any[] = [];

    if (idList.length > 0) {
      const placeholders = idList.map(() => "?").join(", ");
      where.push(`cctv_id IN (${placeholders})`);
      params.push(...idList);
    }
    if (startDate) { where.push("date >= ?"); params.push(String(startDate)); }
    if (endDate)   { where.push("date <= ?"); params.push(String(endDate)); }

    const whereSql = where.length ? `WHERE ${where.join(" AND ")}` : "";

const { cctvMap } = loadCctvMap();

    const rows = db.prepare<any[], any>(`
      SELECT
        cctv_id,
        hour,
        SUM(motorcycle + car + bus + truck) as total
      FROM traffic_data ${whereSql}
      GROUP BY cctv_id, hour
      ORDER BY cctv_id, CAST(hour AS INTEGER) ASC
    `).all(...params);

    const data = rows.map((row: any) => {
      const info = cctvMap.get(row.cctv_id);
      return {
        cctv_id: row.cctv_id,
        id: `${row.cctv_id} - ${info?.lokasi || "Tidak diketahui"}`,
        hour: row.hour,
        total: row.total
      };
    });

    return res.json({ success: true, data });

  } catch (e: any) {
    return res.status(500).json({ success: false, message: e.message });
  }
});

// ============================
// ROUTE /db/riwayat
// ============================
router.get("/db/riwayat", (req: any, res: any) => {
  try {
    const { cctvMap } = loadCctvMap();
    const { ids, page = 1, limit = 10, startDate, endDate } = req.query;

    const pageNumber = safeParseInt(page, 1);
    const limitNumber = safeParseInt(limit, 10);
    const offset = (pageNumber - 1) * limitNumber;

    const idList = ids
      ? String(ids).split(",").map(id => "cctv_" + id.trim())
      : [];

    const db = getDb();
    const where: string[] = [];
    const params: any[] = [];

    if (idList.length > 0) {
      const placeholders = idList.map(() => "?").join(", ");
      where.push(`cctv_id IN (${placeholders})`);
      params.push(...idList);
    }
    if (startDate) { where.push("date >= ?"); params.push(String(startDate)); }
    if (endDate)   { where.push("date <= ?"); params.push(String(endDate)); }

    const whereSql = where.length ? `WHERE ${where.join(" AND ")}` : "";

    const totalRow = db.prepare<any[], any>(`
      SELECT COUNT(*) as total FROM (
        SELECT date, cctv_id FROM traffic_data ${whereSql} GROUP BY date, cctv_id
      )
    `).get(...params);

    const rows = db.prepare<any[], any>(`
      SELECT
        cctv_id,
        date,
        SUM(motorcycle + car + bus + truck) as total
      FROM traffic_data ${whereSql}
      GROUP BY date, cctv_id
      ORDER BY date DESC
      LIMIT ? OFFSET ?
    `).all(...params, limitNumber, offset);

    const data = rows.map((row: any) => {
      const info = cctvMap.get(row.cctv_id);
      return {
        id: `${row.cctv_id} - ${info?.lokasi || "Tidak diketahui"}`,
        date: row.date,
        total: row.total
      };
    });

    return res.json({
      success: true,
      total: totalRow?.total ?? 0,
      page: pageNumber,
      limit: limitNumber,
      data
    });

  } catch (e: any) {
    return res.status(500).json({ success: false, message: e.message });
  }
});

// ============================
// ROUTE /db/per-lokasi
// ============================
router.get("/db/per-lokasi", (req: any, res: any) => {
  try {
    const { ids, startDate, endDate } = req.query;
    const { cctvMap } = loadCctvMap();

    const idList = ids
      ? String(ids).split(",").map(id => "cctv_" + id.trim())
      : [];

    const db = getDb();
    const where: string[] = [];
    const params: any[] = [];

    if (idList.length > 0) {
      const placeholders = idList.map(() => "?").join(", ");
      where.push(`cctv_id IN (${placeholders})`);
      params.push(...idList);
    }
    if (startDate) { where.push("date >= ?"); params.push(String(startDate)); }
    if (endDate)   { where.push("date <= ?"); params.push(String(endDate)); }

    const whereSql = where.length ? `WHERE ${where.join(" AND ")}` : "";

    const rows = db.prepare<any[], any>(`
      SELECT
        cctv_id,
        SUM(motorcycle + car + bus + truck) as total,
        COUNT(DISTINCT hour) as jam_unik
      FROM traffic_data ${whereSql}
      GROUP BY cctv_id
      ORDER BY cctv_id ASC
    `).all(...params);

    const data = rows.map((row: any) => {
      const info = cctvMap.get(row.cctv_id);
      return {
        cctv_id: row.cctv_id,
        id: `${row.cctv_id} - ${info?.lokasi || "Tidak diketahui"}`,
        total: row.total,
        jam_unik: row.jam_unik
      };
    });

    return res.json({ success: true, data });

  } catch (e: any) {
    return res.status(500).json({ success: false, message: e.message });
  }
});

export default router;