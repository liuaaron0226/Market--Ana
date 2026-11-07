// 驗 Cloudflare Access 發的 JWT：RS256 簽章、issuer、audience、期限。不信任任何身分 header。

const JWKS_TTL_MS = 10 * 60 * 1000;
const SKEW_S = 60;
const caches = new WeakMap();

function b64urlBytes(s) {
  const bin = atob(s.replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(bin, (c) => c.charCodeAt(0));
}

function b64urlJson(s) {
  return JSON.parse(new TextDecoder().decode(b64urlBytes(s)));
}

async function loadKeys(teamDomain, fetchImpl, force) {
  let cache = caches.get(fetchImpl);
  if (!cache) caches.set(fetchImpl, (cache = new Map()));
  const hit = cache.get(teamDomain);
  if (hit && !force && Date.now() - hit.at < JWKS_TTL_MS) return hit.keys;
  const resp = await fetchImpl(`${teamDomain}/cdn-cgi/access/certs`);
  if (!resp.ok) throw new Error("jwks fetch failed");
  const keys = (await resp.json()).keys || [];
  cache.set(teamDomain, { at: Date.now(), keys });
  return keys;
}

async function findKey(teamDomain, kid, fetchImpl) {
  let jwk = (await loadKeys(teamDomain, fetchImpl, false)).find((k) => k.kid === kid);
  if (!jwk) jwk = (await loadKeys(teamDomain, fetchImpl, true)).find((k) => k.kid === kid);
  if (!jwk) throw new Error("unknown kid");
  return crypto.subtle.importKey("jwk", jwk, { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" }, false, ["verify"]);
}

// 成功回傳 claims；任何不符都丟錯。
export async function verifyAccessJwt(token, { teamDomain, audiences, fetchImpl }) {
  if (typeof token !== "string") throw new Error("no token");
  const parts = token.split(".");
  if (parts.length !== 3) throw new Error("malformed");
  const header = b64urlJson(parts[0]);
  if (header.alg !== "RS256" || typeof header.kid !== "string") throw new Error("bad alg");
  const key = await findKey(teamDomain, header.kid, fetchImpl);
  const ok = await crypto.subtle.verify(
    "RSASSA-PKCS1-v1_5",
    key,
    b64urlBytes(parts[2]),
    new TextEncoder().encode(`${parts[0]}.${parts[1]}`),
  );
  if (!ok) throw new Error("bad signature");
  const claims = b64urlJson(parts[1]);
  const now = Math.floor(Date.now() / 1000);
  if (claims.iss !== teamDomain) throw new Error("bad iss");
  const aud = Array.isArray(claims.aud) ? claims.aud : [claims.aud];
  if (!aud.some((a) => audiences.includes(a))) throw new Error("bad aud");
  if (typeof claims.exp !== "number" || claims.exp < now - SKEW_S) throw new Error("expired");
  if (typeof claims.nbf === "number" && claims.nbf > now + SKEW_S) throw new Error("not yet valid");
  if (typeof claims.email !== "string" || !claims.email.includes("@")) throw new Error("no email");
  return { ...claims, aud };
}
