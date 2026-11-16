// 推播的測試替身：一組測試用推播金鑰、一台假裝置（有自己的私鑰，收得到就解得開），以及攔下送往推播服務請求的假網路。
// 解密與驗章用 Node 自己的 crypto（不是被測程式用的 WebCrypto 寫法），照 RFC 8291／8292 從頭做一次。
import { createDecipheriv, createECDH, createPublicKey, hkdfSync, verify } from "node:crypto";

const b64 = (bytes) => Buffer.from(bytes).toString("base64url");

export async function makeVapid() {
  const pair = await crypto.subtle.generateKey({ name: "ECDSA", namedCurve: "P-256" }, true, ["sign", "verify"]);
  const jwk = await crypto.subtle.exportKey("jwk", pair.privateKey);
  const raw = new Uint8Array(await crypto.subtle.exportKey("raw", pair.publicKey));
  return { publicKey: b64(raw), privateKey: jwk.d };
}

export async function makeDevice(endpoint) {
  const ecdh = createECDH("prime256v1");
  ecdh.generateKeys();
  const authSecret = crypto.getRandomValues(new Uint8Array(16));
  const subscription = { endpoint, keys: { p256dh: b64(ecdh.getPublicKey()), auth: b64(authSecret) } };
  // RFC 8291：收件端用自己的私鑰與 auth 解開 aes128gcm 內容。
  const decrypt = (body) => {
    const buf = Buffer.from(body);
    const salt = buf.subarray(0, 16);
    const idlen = buf[20];
    const senderPublic = buf.subarray(21, 21 + idlen);
    const ciphertext = buf.subarray(21 + idlen);
    const shared = ecdh.computeSecret(senderPublic);
    const keyInfo = Buffer.concat([Buffer.from("WebPush: info\0"), ecdh.getPublicKey(), senderPublic]);
    const ikm = Buffer.from(hkdfSync("sha256", shared, authSecret, keyInfo, 32));
    const cek = Buffer.from(hkdfSync("sha256", ikm, salt, Buffer.from("Content-Encoding: aes128gcm\0"), 16));
    const nonce = Buffer.from(hkdfSync("sha256", ikm, salt, Buffer.from("Content-Encoding: nonce\0"), 12));
    const decipher = createDecipheriv("aes-128-gcm", cek, nonce);
    decipher.setAuthTag(ciphertext.subarray(ciphertext.length - 16));
    const padded = Buffer.concat([decipher.update(ciphertext.subarray(0, ciphertext.length - 16)), decipher.final()]);
    let end = padded.length - 1;
    while (end >= 0 && padded[end] === 0) end -= 1;
    assert(padded[end] === 2, "最後一筆紀錄要以 0x02 結尾");
    return JSON.parse(padded.subarray(0, end).toString("utf8"));
  };
  return { subscription, decrypt };
}

function assert(ok, message) { if (!ok) throw new Error(message); }

// 驗推播服務會檢查的那張 VAPID 憑證：ES256 簽章、aud 是推播服務的來源、還沒過期、有 sub。
export function checkVapid(authorization, endpoint, publicKey, nowMs) {
  const match = /^vapid t=([^,]+),\s*k=(.+)$/.exec(authorization || "");
  assert(match, "Authorization 要是 vapid t=…, k=…");
  const [token, key] = [match[1], match[2]];
  assert(key === publicKey, "k 要是推播公開金鑰");
  const [h, p, s] = token.split(".");
  const header = JSON.parse(Buffer.from(h, "base64url"));
  const claims = JSON.parse(Buffer.from(p, "base64url"));
  assert(header.alg === "ES256", "ES256");
  const raw = Buffer.from(publicKey, "base64url");
  const pub = createPublicKey({ key: { kty: "EC", crv: "P-256", x: b64(raw.subarray(1, 33)), y: b64(raw.subarray(33, 65)) }, format: "jwk" });
  assert(verify("sha256", Buffer.from(`${h}.${p}`), { key: pub, dsaEncoding: "ieee-p1363" }, Buffer.from(s, "base64url")), "簽章要對");
  assert(claims.aud === new URL(endpoint).origin, "aud 是推播服務的來源");
  assert(claims.exp > nowMs / 1000 && claims.exp <= nowMs / 1000 + 24 * 3600, "期限在 24 小時內");
  assert(/^(mailto:|https:)/.test(claims.sub || ""), "sub 是 https 或 mailto");
  return claims;
}

// 假的推播服務：記下每一個請求；statusFor(endpoint) 決定回什麼（預設 201）。
export function pushRecorder(statusFor = () => 201) {
  const sent = [];
  return {
    sent,
    fetch: async (url, init) => {
      sent.push({ endpoint: String(url), headers: new Headers(init.headers), body: new Uint8Array(init.body) });
      return new Response(null, { status: statusFor(String(url)) });
    },
  };
}
