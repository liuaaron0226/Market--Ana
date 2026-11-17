// 基金對照頁（2026-11-17 本人定案）：把 /api/funds/<代碼>/vs-0050 的回應整理成畫面要的字。只驗輸入輸出，不碰畫面。
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const ROOT = new URL("../../", import.meta.url);
function load() {
  const window = {};
  vm.runInNewContext(readFileSync(new URL("web/fund-view.js", ROOT), "utf8"), { window });
  return (payload) => JSON.parse(JSON.stringify(window.FundView.read(payload)));
}
const read = load();

const ok = (period, label, fund, usd, bench, extra = {}) => ({
  period, label, status: "ok", from: "2025-11-07", to: "2026-11-09", fund_return_pct: fund, fund_usd_return_pct: usd,
  fx_effect_pct: Math.round((fund - usd) * 10) / 10, benchmark_return_pct: bench, excess_pct: Math.round((fund - bench) * 10) / 10,
  fund_max_drawdown_pct: -22.2, benchmark_max_drawdown_pct: -20.6, ...extra,
});
const PAYLOAD = {
  schema: "fund-vs-0050/v1",
  fund: { code: "BLKTENAU", isin: "LU2533723974", currency: "USD", class_launch: "2022-10-26", short_name: "貝萊德世界科技 A10",
          name: "貝萊德世界科技基金 A10美元(總報酬穩定配息)" },
  data_date: "2026-11-09", fund_nav_date: "2026-11-16", benchmark_date: "2026-11-09",
  latest: { nav: 17.53, nav_date: "2026-11-09", fx: 32.882, fx_date: "2026-11-09" },
  periods: [ok("1m", "近 1 個月", -16.7, -19.2, -15.2), ok("3m", "近 3 個月", -11.8, -13.4, -5.3), ok("1y", "近 1 年", 17.9, 11.3, 58.9),
            { period: "3y", label: "近 3 年", status: "insufficient_history", reason: "since_launch" },
            ok("since", "成立以來", 152.7, 135.4, 265.7)],
  chart_from: "2022-12-01", chart: [["2022-12-01", 1], ["2026-11-09", 2.527]], benchmark_chart: [["2022-12-01", 1], ["2026-11-09", 3.657]],
  distributions: { count: 47, by_year: [], latest: { ex_date: "2026-10-30", amount: 0.162 } },
  capital_note: "這檔配息可能來自本金：2023 年 12 次配息中 11 次全部來自本金。",
  issues: [], notes: ["a", "b"],
};

test("頁首：名稱、代碼與幣別、最新淨值與日期", () => {
  const view = read(PAYLOAD);
  assert.equal(view.title, "貝萊德世界科技 A10");
  assert.equal(view.sub, "BLKTENAU · 美元計價 · 月配息");
  assert.deepEqual([view.nav, view.navDate], ["17.53", "淨值日 2026-11-09"]);
});

test("結論一句：近 1 年和 0050 差幾個百分點（台幣、含息）", () => {
  assert.equal(read(PAYLOAD).conclusion, "近 1 年落後 0050 41.0 個百分點（台幣、含息再投入）。");
  const ahead = { ...PAYLOAD, periods: [ok("1y", "近 1 年", 30, 25, 20)] };
  assert.equal(read(ahead).conclusion, "近 1 年領先 0050 10.0 個百分點（台幣、含息再投入）。");
  const even = { ...PAYLOAD, periods: [ok("1y", "近 1 年", 20, 25, 20)] };
  assert.equal(read(even).conclusion, "近 1 年和 0050 持平（台幣、含息再投入）。");
  const none = { ...PAYLOAD, periods: [{ period: "1y", label: "近 1 年", status: "insufficient_history" }] };
  assert.equal(read(none).conclusion, "近 1 年資料不足，暫時無法和 0050 比較。");
});

test("五個期間鍵：方向、差距、四個數字，另一行寫匯率影響", () => {
  const periods = read(PAYLOAD).periods;
  assert.deepEqual(periods.map(p => [p.key, p.short, p.kind, p.gap]), [
    ["1m", "近1月", "bear", "-1.5%"], ["3m", "近3月", "bear", "-6.5%"], ["1y", "近1年", "bear", "-41.0%"],
    ["3y", "近3年", "none", "資料不足"], ["since", "成立以來", "bear", "-113.0%"]]);
  assert.deepEqual(periods[2].facts, [["基金報酬（台幣）", "+17.9%"], ["0050 報酬", "+58.9%"], ["基金最大回撤", "-22.2%"], ["0050 最大回撤", "-20.6%"]]);
  assert.equal(periods[2].fx, "其中匯率影響 +6.6 個百分點（只看美元是 +11.3%）");
  assert.equal(periods[3].fx, "");
});

test("資料日：兩邊比到同一天；哪一邊比較新就講明", () => {
  assert.equal(read(PAYLOAD).dateLine, "資料日 2026-11-09（基金淨值已到 2026-11-16）");
  const fundLate = { ...PAYLOAD, fund_nav_date: "2026-11-09", benchmark_date: "2026-11-13" };
  assert.equal(read(fundLate).dateLine, "資料日 2026-11-09（0050 已到 2026-11-13）");
  const same = { ...PAYLOAD, fund_nav_date: "2026-11-09" };
  assert.equal(read(same).dateLine, "資料日 2026-11-09");
});

test("提醒：缺日、太久沒更新、數字對不上，各一句白話", () => {
  const issues = [
    { kind: "gap", from: "2026-11-17", to: "2026-11-19", weekdays: 3 },
    { kind: "stale", latest: "2026-11-24", weekdays_behind: 9 },
    { kind: "conflict", date: "2026-11-13", kept: 17.73, other: 17.8 },
  ];
  assert.deepEqual(read({ ...PAYLOAD, issues }).issues, [
    "缺 2026-11-17～2026-11-19 的淨值（3 個平日沒抓到），這段報酬可能不準。",
    "基金淨值停在 2026-11-24，已經 9 個平日沒有更新。",
    "2026-11-13 的淨值兩份資料不一致（17.73／17.8），先用 17.73。",
  ]);
  assert.deepEqual(read(PAYLOAD).issues, []);
});

test("配息：來源可能是本金那一句，加上最近一次配息", () => {
  const view = read(PAYLOAD);
  assert.equal(view.capitalNote, PAYLOAD.capital_note);
  assert.equal(view.latestDistribution, "最近一次除息 2026-10-30，每單位配 0.162 美元。");
});

test("走勢圖兩條線：基金實線在前、0050 虛線；期初＝100", () => {
  const series = read(PAYLOAD).series;
  assert.deepEqual(series.map(s => [s.key, s.label, s.dash]), [["0050", "0050", "8 5"], ["BLKTENAU", "貝萊德世界科技 A10", ""]]);
  assert.deepEqual(series[1].points, [["2022-12-01", 100], ["2026-11-09", 252.7]]);
});

test("自選股頁那一列：只要名稱與近 1 年的差距", () => {
  const window = {};
  vm.runInNewContext(readFileSync(new URL("web/fund-view.js", ROOT), "utf8"), { window });
  assert.deepEqual(JSON.parse(JSON.stringify(window.FundView.entry(PAYLOAD))), { title: "貝萊德世界科技 A10", kind: "bear", gap: "近1年 落後 0050 41.0" });
  assert.deepEqual(JSON.parse(JSON.stringify(window.FundView.entry(null))), { title: "", kind: "none", gap: "" });
});
