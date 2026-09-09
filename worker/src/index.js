/**
 * 族群定義的雲端儲存（Cloudflare Worker + KV）
 *
 * 存在的理由：靜態站沒有後端，族群改動只能留在瀏覽器的 localStorage，
 * 換一台裝置就看不到。這層把族群存進 KV，讓多裝置共用同一份，
 * 排程重建時也會把它寫回 repo 的 static_data/groups.json。
 *
 *   GET /groups                      讀目前族群（公開，給頁面與排程用）
 *   PUT /groups   x-edit-key: ...    覆寫族群（要金鑰）
 *
 * 讀是公開的——反正站台本身就是公開的，族群內容藏不住。
 * 寫需要金鑰，否則任何人都能改掉你的分類。
 */

const KEY = "groups";
const MAX_GROUPS = 300;
const MAX_PER_GROUP = 3000;
const MAX_BYTES = 512 * 1024;
const NAME_MAX = 40;
const CODE_RE = /^[0-9A-Z]{4,6}$/;

function corsHeaders(env, request) {
  const allowed = String(env.ALLOWED_ORIGINS || "")
    .split(",").map((s) => s.trim()).filter(Boolean);
  const origin = request.headers.get("Origin") || "";
  const h = {
    "Access-Control-Allow-Methods": "GET,PUT,OPTIONS",
    "Access-Control-Allow-Headers": "content-type,x-edit-key",
    "Access-Control-Max-Age": "86400",
    Vary: "Origin",
  };
  if (origin && allowed.includes(origin)) {
    h["Access-Control-Allow-Origin"] = origin;
  }
  return h;
}

function json(body, status, headers) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      ...headers,
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
    },
  });
}

/** 長度不同也走完整個迴圈，不要用長度或提前 return 洩漏資訊 */
function safeEqual(a, b) {
  const x = String(a), y = String(b);
  let diff = x.length ^ y.length;
  for (let i = 0; i < Math.max(x.length, y.length); i++) {
    diff |= x.charCodeAt(i % (x.length || 1)) ^ y.charCodeAt(i % (y.length || 1));
  }
  return diff === 0;
}

/** 回傳 {value} 或 {error}。代號去重並排序，族群名去頭尾空白。 */
function validate(obj) {
  if (!obj || typeof obj !== "object" || Array.isArray(obj)) {
    return { error: '格式要是 {"族群名": ["2330", ...]}' };
  }
  const names = Object.keys(obj);
  if (names.length > MAX_GROUPS) {
    return { error: `族群數上限 ${MAX_GROUPS} 個` };
  }
  const out = {};
  for (const raw of names) {
    const name = String(raw).trim();
    if (!name) return { error: "族群名稱不能是空的" };
    if (name.length > NAME_MAX) {
      return { error: `族群名稱過長（上限 ${NAME_MAX} 字）：${name.slice(0, 20)}…` };
    }
    const list = obj[raw];
    if (!Array.isArray(list)) {
      return { error: `「${name}」的成分股要是陣列` };
    }
    if (list.length > MAX_PER_GROUP) {
      return { error: `「${name}」成分股超過 ${MAX_PER_GROUP} 檔` };
    }
    const seen = new Set();
    for (const c of list) {
      const code = String(c).trim().toUpperCase();
      if (!CODE_RE.test(code)) {
        return { error: `代號不合法：${String(c).slice(0, 12)}` };
      }
      seen.add(code);
    }
    out[name] = [...seen].sort();
  }
  return { value: out };
}

export default {
  async fetch(request, env) {
    const headers = corsHeaders(env, request);

    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers });
    }
    const path = new URL(request.url).pathname.replace(/\/+$/, "") || "/";
    if (path !== "/groups" && path !== "/") {
      return json({ error: "not found" }, 404, headers);
    }
    if (!env.GROUPS) {
      return json({ error: "KV namespace 尚未綁定（binding 名稱要是 GROUPS）" },
        500, headers);
    }

    if (request.method === "GET") {
      const raw = await env.GROUPS.get(KEY);
      if (!raw) {
        // 還沒存過任何東西：頁面會退回用內建的預設族群
        return json({ groups: null, updated_at: null, version: 0 }, 200, headers);
      }
      return new Response(raw, {
        status: 200,
        headers: {
          ...headers,
          "content-type": "application/json; charset=utf-8",
          "cache-control": "no-store",
        },
      });
    }

    if (request.method === "PUT") {
      if (!env.EDIT_KEY) {
        return json({ error: "伺服器還沒設定 EDIT_KEY，請先 wrangler secret put EDIT_KEY" },
          500, headers);
      }
      if (!safeEqual(request.headers.get("x-edit-key") || "", env.EDIT_KEY)) {
        return json({ error: "編輯金鑰不正確" }, 401, headers);
      }

      const text = await request.text();
      if (text.length > MAX_BYTES) {
        return json({ error: `內容過大（上限 ${MAX_BYTES / 1024} KB）` }, 413, headers);
      }
      let parsed;
      try {
        parsed = JSON.parse(text);
      } catch (e) {
        return json({ error: "不是合法的 JSON" }, 400, headers);
      }
      // 允許直接送 {"族群": [...]}，也允許送 {groups: {...}}
      const body = parsed && parsed.groups && typeof parsed.groups === "object"
        ? parsed.groups : parsed;

      const checked = validate(body);
      if (checked.error) return json({ error: checked.error }, 400, headers);

      const prev = await env.GROUPS.get(KEY, "json");
      const record = {
        groups: checked.value,
        updated_at: new Date().toISOString(),
        version: ((prev && prev.version) || 0) + 1,
      };
      await env.GROUPS.put(KEY, JSON.stringify(record));
      return json({
        ok: true,
        version: record.version,
        updated_at: record.updated_at,
        groups: Object.keys(checked.value).length,
        stocks: Object.values(checked.value).reduce((a, v) => a + v.length, 0),
      }, 200, headers);
    }

    return json({ error: "method not allowed" }, 405, headers);
  },
};
