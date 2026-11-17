// 申請頁、站主後台、Gemini 設定頁不載入主站的樣式檔，account.css 把主站〈看盤桌〉的桌面色與板／鍵／槽陰影照抄一份
// （立體重設計 D 包第 2 片）。這裡把兩邊的數值解開成實際的值比對：主站改了、這裡沒跟上就失敗。
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const ROOT = new URL("../../", import.meta.url);
const read = (path) => readFileSync(new URL(path, ROOT), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");

// 找出某個選擇器的所有區塊（這幾個區塊裡沒有巢狀大括號），把 --變數 收進同一張表，後面的蓋前面的
function tokens(css, selector, into = {}) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  for (const match of css.matchAll(new RegExp(`(?:^|[\\s}])${escaped}\\s*\\{([^{}]*)\\}`, "g"))) {
    for (const part of match[1].split(";")) {
      const at = part.indexOf(":");
      const name = part.slice(0, at).trim();
      if (name.startsWith("--")) into[name] = part.slice(at + 1).replace(/\s+/g, " ").trim();
    }
  }
  return into;
}

function resolve(table, value, depth = 0) {
  assert.ok(depth < 10, `變數互相引用解不開：${value}`);
  const next = value.replace(/var\((--[\w-]+)\)/g, (_, name) => (name in table ? table[name] : `var(${name})`));
  return next === value ? value : resolve(table, next, depth + 1);
}

const minimal = read("web/minimal.css"), bold = read("web/bold.css"), account = read("cloud/public-src/account.css");
const site = {
  dark: tokens(bold, ":root", tokens(minimal, ":root")),
  light: tokens(bold, ':root[data-theme="light"]', tokens(minimal, ':root[data-theme="light"]', tokens(bold, ":root", tokens(minimal, ":root")))),
};
const accountLight = tokens(account, ":root");
const pages = { light: accountLight, dark: tokens(account, ':root:not([data-theme="light"])', { ...accountLight }) };

const COPIED = ["--r-key", "--r-slab", "--slab-thick", "--key-thick", "--desk", "--desk-hi", "--key", "--well", "--edge", "--rim",
  "--shade", "--shade-k", "--slab-shadow", "--key-shadow", "--key-shadow-press", "--key-shadow-ink", "--well-shadow",
  "--bg", "--panel", "--panel2", "--line", "--ink"];

for (const theme of ["light", "dark"]) {
  test(`${theme === "light" ? "淺色" : "深色"}：帳號頁的桌面色與板／鍵／槽數值和主站一樣`, () => {
    for (const name of COPIED) {
      assert.ok(name in pages[theme], `account.css 少了 ${name}`);
      assert.equal(resolve(pages[theme], pages[theme][name]), resolve(site[theme], site[theme][name]), name);
    }
  });
}
