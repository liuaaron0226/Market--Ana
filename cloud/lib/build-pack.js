// build 時使用：讀 web/*-data.js（window.X = {...};）組成雲端資料包。只在 Node 執行，不進 Functions。
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { buildMarketPack } from "./market-data.js";

export const hash12 = (value) => createHash("sha256").update(JSON.stringify(value)).digest("hex").slice(0, 12);

export function readAssignment(url) {
  const text = readFileSync(url, "utf8").trim();
  const start = text.indexOf("=");
  if (!text.startsWith("window.") || start < 0) throw new Error(`${url} 不是 window.X = {...} 格式`);
  return JSON.parse(text.slice(start + 1).trim().replace(/;$/, ""));
}

export function packFromWeb(webDir) {
  const read = (f) => readAssignment(new URL(f, webDir));
  return buildMarketPack({
    analysis: read("analysis-data.js"),
    dashboard: read("dashboard-data.js"),
    chips: read("chips-data.js"),
    plain: read("plain-data.js"),
    detail: read("detail-data.js"),
  }, hash12);
}
