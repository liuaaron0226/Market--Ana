// 國外同類股代號的寫法：和排程那一邊（watch_run.py peer_symbol，G 票）共用 fixtures/peer-symbols.json。
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { parsePeerSymbol } from "../lib/watch-peers.js";

const table = JSON.parse(readFileSync(new URL("./fixtures/peer-symbols.json", import.meta.url), "utf8"));

test("代號的寫法和排程那一邊一致", () => {
  for (const [text, expected] of table.cases) {
    const parsed = parsePeerSymbol(text);
    assert.deepEqual(parsed && { symbol: parsed.symbol, market: parsed.market }, expected, text);
  }
});
