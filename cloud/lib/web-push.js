// 網頁標準推播的送出（RFC 8291 加密內容＋RFC 8292 VAPID 憑證），只用 WebCrypto，不經第三方通知服務。
// 網站（測試通知）與 cron-trigger 的每分鐘盯價共用這一份。金鑰由本人放進雲端機密設定：
//   VAPID_PUBLIC_KEY  未壓縮的 P-256 公開點（65 bytes）的 base64url
//   VAPID_PRIVATE_KEY 私鑰 d（32 bytes）的 base64url
const enc = new TextEncoder();
const b64 = (bytes) => {
  let text = "";
  for (const byte of new Uint8Array(bytes)) text += String.fromCharCode(byte);
  return btoa(text).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
};
const unb64 = (text) => Uint8Array.from(atob(String(text).replace(/-/g, "+").replace(/_/g, "/")), (c) => c.charCodeAt(0));
const concat = (...parts) => {
  const out = new Uint8Array(parts.reduce((n, part) => n + part.length, 0));
  let at = 0;
  for (const part of parts) { out.set(part, at); at += part.length; }
  return out;
};

// 只送往已知的瀏覽器推播服務（Apple、Chrome、Firefox、Edge），不讓登記的端點變成任意對外連線。
const PUSH_HOSTS = [/\.push\.apple\.com$/, /^fcm\.googleapis\.com$/, /\.push\.services\.mozilla\.com$/, /\.notify\.windows\.com$/];
export function validSubscription(subscription) {
  const endpoint = subscription?.endpoint;
  let url;
  try { url = new URL(endpoint); } catch { return false; }
  const { p256dh, auth } = subscription.keys || {};
  return url.protocol === "https:" && endpoint.length <= 1024 && PUSH_HOSTS.some((host) => host.test(url.hostname))
    && typeof p256dh === "string" && /^[A-Za-z0-9_-]{86,88}$/.test(p256dh)
    && typeof auth === "string" && /^[A-Za-z0-9_-]{21,24}$/.test(auth);
}

export const pushConfigured = (env) => Boolean(env?.VAPID_PUBLIC_KEY && env?.VAPID_PRIVATE_KEY);

async function vapidHeader(endpoint, env, nowMs) {
  const pub = unb64(env.VAPID_PUBLIC_KEY);
  const key = await crypto.subtle.importKey("jwk", {
    kty: "EC", crv: "P-256", d: env.VAPID_PRIVATE_KEY, x: b64(pub.slice(1, 33)), y: b64(pub.slice(33, 65)), ext: true,
  }, { name: "ECDSA", namedCurve: "P-256" }, false, ["sign"]);
  const header = b64(enc.encode(JSON.stringify({ typ: "JWT", alg: "ES256" })));
  // sub 用網站網址，不放任何人的電子郵件。
  const claims = b64(enc.encode(JSON.stringify({
    aud: new URL(endpoint).origin, exp: Math.floor(nowMs / 1000) + 12 * 3600, sub: env.PUSH_CONTACT || "https://market-ana.pages.dev",
  })));
  const signature = await crypto.subtle.sign({ name: "ECDSA", hash: "SHA-256" }, key, enc.encode(`${header}.${claims}`));
  return `vapid t=${header}.${claims}.${b64(signature)}, k=${env.VAPID_PUBLIC_KEY}`;
}

async function hkdf(ikm, salt, info, bytes) {
  const key = await crypto.subtle.importKey("raw", ikm, "HKDF", false, ["deriveBits"]);
  return new Uint8Array(await crypto.subtle.deriveBits({ name: "HKDF", hash: "SHA-256", salt, info }, key, bytes * 8));
}

async function encrypt(payload, subscription) {
  const clientPublic = unb64(subscription.keys.p256dh);
  const authSecret = unb64(subscription.keys.auth);
  const local = await crypto.subtle.generateKey({ name: "ECDH", namedCurve: "P-256" }, true, ["deriveBits"]);
  const localPublic = new Uint8Array(await crypto.subtle.exportKey("raw", local.publicKey));
  const clientKey = await crypto.subtle.importKey("raw", clientPublic, { name: "ECDH", namedCurve: "P-256" }, false, []);
  const shared = new Uint8Array(await crypto.subtle.deriveBits({ name: "ECDH", public: clientKey }, local.privateKey, 256));
  const ikm = await hkdf(shared, authSecret, concat(enc.encode("WebPush: info\0"), clientPublic, localPublic), 32);
  const salt = crypto.getRandomValues(new Uint8Array(16));
  const cek = await hkdf(ikm, salt, enc.encode("Content-Encoding: aes128gcm\0"), 16);
  const nonce = await hkdf(ikm, salt, enc.encode("Content-Encoding: nonce\0"), 12);
  const aes = await crypto.subtle.importKey("raw", cek, "AES-GCM", false, ["encrypt"]);
  const plain = concat(enc.encode(JSON.stringify(payload)), new Uint8Array([2]));
  const cipher = new Uint8Array(await crypto.subtle.encrypt({ name: "AES-GCM", iv: nonce }, aes, plain));
  const recordSize = new Uint8Array([0, 0, 16, 0]);   // 4096
  return concat(salt, recordSize, new Uint8Array([localPublic.length]), localPublic, cipher);
}

// 送一則通知到一台裝置。回傳 { ok, gone, status }：gone＝推播服務說這個訂閱已經不存在（404／410），呼叫端要刪掉它。
export async function sendPush(subscription, payload, env, { fetchImpl = fetch, now = Date.now, ttl = 3600 } = {}) {
  const body = await encrypt(payload, subscription);
  let response;
  try {
    response = await fetchImpl(subscription.endpoint, {
      method: "POST",
      headers: {
        authorization: await vapidHeader(subscription.endpoint, env, now()),
        "content-encoding": "aes128gcm", "content-type": "application/octet-stream",
        ttl: String(ttl), urgency: "high",
      },
      body,
    });
  } catch {
    return { ok: false, gone: false, status: 0 };
  }
  return { ok: response.status >= 200 && response.status < 300, gone: response.status === 404 || response.status === 410, status: response.status };
}

// 送給某位使用者登記的每一台裝置；失效的訂閱刪掉，其他失敗記在那一台上、不重試。回傳送達的台數。
export async function sendToAll(db, email, payload, env, { fetchImpl, now = Date.now } = {}) {
  const { results: rows } = await db.prepare(
    "SELECT endpoint, p256dh, auth FROM push_subscriptions WHERE email = ? ORDER BY created_at",
  ).bind(email).all();
  let delivered = 0;
  for (const row of rows) {
    const result = await sendOne(db, row, payload, env, { fetchImpl, now });
    if (result.ok) delivered += 1;
  }
  return delivered;
}

export async function sendOne(db, row, payload, env, { fetchImpl, now = Date.now }) {
  const subscription = { endpoint: row.endpoint, keys: { p256dh: row.p256dh, auth: row.auth } };
  const result = await sendPush(subscription, payload, env, { fetchImpl, now });
  const at = new Date(now()).toISOString();
  if (result.gone) {
    await db.prepare("DELETE FROM push_subscriptions WHERE endpoint = ?").bind(row.endpoint).run();
  } else if (result.ok) {
    await db.prepare("UPDATE push_subscriptions SET last_sent_at = ?, last_error = NULL WHERE endpoint = ?").bind(at, row.endpoint).run();
  } else {
    await db.prepare("UPDATE push_subscriptions SET last_error = ? WHERE endpoint = ?")
      .bind(`${at} 推播服務回 ${result.status || "連線失敗"}`, row.endpoint).run();
  }
  return result;
}
