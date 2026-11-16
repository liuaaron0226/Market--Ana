import { verifyAccessJwt } from "../../lib/jwt.js";
import { AiDigestError, handleAiDigest } from "../../lib/ai-digest.js";
import { policyForViewer, releasePolicy } from "../../lib/release-policy.js";
import { isOwnerEmail } from "../../lib/owner.js";

const HEADERS = {
  "content-type": "application/json; charset=utf-8",
  "cache-control": "private, no-store",
  "x-content-type-options": "nosniff",
  "x-robots-tag": "noindex, nofollow",
};
const fail = (status, message) => new Response(JSON.stringify({ error: message }), { status, headers: HEADERS });

export async function onRequest(context) {
  const { TEAM_DOMAIN, MAIN_AUD, OWNER_EMAIL, DB } = context.env;
  if (!TEAM_DOMAIN || !MAIN_AUD || !OWNER_EMAIL || !DB) return fail(503, "服務設定未完成");
  let claims;
  try {
    claims = await verifyAccessJwt(context.request.headers.get("cf-access-jwt-assertion"), {
      teamDomain: TEAM_DOMAIN,
      audiences: [MAIN_AUD],
      fetchImpl: context.fetch || fetch,
    });
  } catch {
    return fail(401, "需要登入");
  }
  const email = claims.email.toLowerCase();
  const owner = isOwnerEmail(context.env, email);
  if (!owner) {
    try {
      const row = await DB.prepare("SELECT status FROM users WHERE email = ?").bind(email).first();
      if (row?.status !== "approved") return fail(403, "帳號尚未核准或已停用");
    } catch {
      return fail(503, "暫時無法處理，請稍後再試");
    }
  }
  const policy = releasePolicy(context.env);
  // 站主那份只給站主（DECISIONS 第 5 題）：朋友的功能清單裡這項一律是關的，朋友讀自己那份走 /api/friend-ai/digest。
  if (!owner || !policy.features.ai_digest) {
    // 只給站主的功能，其他人拿到的功能清單裡一律是關的（和 /api/features 同一個規則）。
    const features = policyForViewer(policy, owner, context.env).features;
    return new Response(JSON.stringify({ error: "feature-disabled", message: "未驗證的 AI 摘要尚未啟用", features }),
      { status: 403, headers: HEADERS });
  }
  try {
    return await handleAiDigest(context.request, context.env, { owner });
  } catch (error) {
    if (error instanceof AiDigestError) return fail(error.status, error.message);
    return fail(503, "暫時無法處理，請稍後再試");
  }
}
