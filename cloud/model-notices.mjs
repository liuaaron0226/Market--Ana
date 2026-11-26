// GitHub-only notification adapter. Reuses existing D1 HTTP + owner push subscriptions.
// No new bindings, tables or credentials; no writes to D1. Only fixed public-safe messages.
import { readFile, writeFile } from 'node:fs/promises';
import { pathToFileURL } from 'node:url';
import { d1OverHttp } from './lib/d1-http.js';
import { DATABASE_ID } from './watch-ready-notices.mjs';
import { pushConfigured, sendPush } from './lib/web-push.js';

export async function sendModelNotices(env, notices, { send = sendPush } = {}) {
  const delivered = [], discarded = [];
  const email = String(env.OWNER_EMAIL || '').toLowerCase();
  if (!email || !pushConfigured(env)) return { delivered, waiting: true };
  const { results: devices } = await env.DB.prepare('SELECT endpoint, p256dh, auth FROM push_subscriptions WHERE email = ? ORDER BY created_at').bind(email).all();
  for (const notice of notices) {
    if (!/^[a-f0-9]{64}$/.test(notice.id) || !['failed', 'replaced'].includes(notice.state)) continue;
    // A user may have changed the selection since this run; don't send obsolete alerts.
    if (notice.key !== 'catalog') {
      const row = await env.DB.prepare('SELECT model, effort, updated_at FROM ai_model_settings WHERE task = ?').bind(notice.key).first();
      if (row && (row.model !== notice.requested?.model || row.effort !== notice.requested?.effort || row.updated_at > notice.checked_at)) {
        discarded.push(notice.id); // obsolete, not a successful delivery
        continue;
      }
    }
    const payload = {
      title: 'AI 模型設定提醒',
      body: notice.state === 'replaced' ? '原選模型已停用，已使用通過檢查的 Gemini 替代模型。可在設定查看並手動改選。'
        : '模型清單或可用性檢查未完成。手動設定保持不變，請開啟設定查看。',
      url: '/minimal.html#ai-models', tag: `ai-models-${notice.key}`,
    };
    let ok = false;
    for (const device of devices) {
      try {
        const result = await send({ endpoint: device.endpoint, keys: { p256dh: device.p256dh, auth: device.auth } }, payload, env);
        ok ||= result.ok;
      } catch (_) { /* retry next scheduled check; never expose device endpoints */ }
    }
    if (ok) delivered.push(notice.id);
  }
  return { delivered, discarded, waiting: delivered.length + discarded.length < notices.length };
}

if (import.meta.url === pathToFileURL(process.argv[1] || '').href) {
  try {
    const env = { ...process.env, DB: d1OverHttp({ accountId: process.env.CLOUDFLARE_ACCOUNT_ID, databaseId: DATABASE_ID, token: process.env.CLOUDFLARE_API_TOKEN }) };
    const notices = JSON.parse(await readFile(process.argv[2], 'utf8'));
    const result = await sendModelNotices(env, notices);
    await writeFile(process.argv[3], JSON.stringify(result));
    console.log(`模型提醒處理 ${result.delivered.length} 筆；${result.waiting ? '尚有待送提醒' : '沒有待送提醒'}。`);
  } catch (_) { console.error('模型提醒暫時無法送出，留待下次重試。'); process.exitCode = 1; }
}
