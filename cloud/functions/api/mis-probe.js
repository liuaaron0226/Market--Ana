import { probeMis } from "../../lib/live-quotes.js";

const headers = {
  "content-type": "application/json; charset=utf-8",
  "cache-control": "no-store",
  "x-content-type-options": "nosniff",
  "x-robots-tag": "noindex, nofollow",
};

export async function onRequest(context) {
  try {
    const mis = await probeMis({ fetchImpl: context.fetch || fetch });
    return new Response(JSON.stringify({ environment: "cloudflare-pages", mis }), { status: 200, headers });
  } catch (error) {
    return new Response(JSON.stringify({
      environment: "cloudflare-pages",
      mis: { ok: false, error: error instanceof Error ? error.message : "MIS probe 失敗" },
    }), { status: 503, headers });
  }
}
