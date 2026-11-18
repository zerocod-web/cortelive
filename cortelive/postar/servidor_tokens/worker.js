/**
 * CorteLive — Servidor Intermediário de Tokens OAuth (Cloudflare Worker)
 *
 * Objetivo de segurança:
 *   Meta (Instagram) e TikTok exigem o segredo confidencial do aplicativo na troca
 *   do código OAuth e na renovação (refresh) do token. Como o CorteLive roda no PC
 *   dos amigos e o repositório é público, NENHUM segredo vai para o PC nem para o Git.
 *   Os segredos moram exclusivamente nas variáveis criptografadas deste Worker
 *   (`wrangler secret put META_APP_SECRET`, `wrangler secret put TIKTOK_CLIENT_SECRET`).
 *
 * Rotas expostas:
 *   GET  /health        -> Confirma que o Worker está no ar (sem expor segredos)
 *   POST /oauth/trocar  -> Troca `code` + `code_verifier` (PKCE) por tokens
 *   POST /oauth/renovar -> Renova `refresh_token` / token longo da Meta
 */

const TIKTOK_TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/";
const GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token";

/**
 * @typedef {Object} Env
 * @property {string} [META_APP_ID]
 * @property {string} [META_APP_SECRET]
 * @property {string} [TIKTOK_CLIENT_KEY]
 * @property {string} [TIKTOK_CLIENT_SECRET]
 * @property {string} [YOUTUBE_CLIENT_ID]
 * @property {string} [YOUTUBE_CLIENT_SECRET]
 * @property {string} [VERSAO_META_API]
 */

function respostaJson(dados, status = 200) {
  return new Response(JSON.stringify(dados), {
    status,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "no-store",
      "X-Content-Type-Options": "nosniff",
    },
  });
}

function redirectLoopbackValido(uri) {
  if (typeof uri !== "string" || !uri.trim()) return false;
  try {
    const parsed = new URL(uri);
    return (
      parsed.protocol === "http:" &&
      (parsed.hostname === "127.0.0.1" || parsed.hostname === "localhost")
    );
  } catch {
    return false;
  }
}

async function lerJsonSeguro(resp) {
  try {
    const j = await resp.json();
    return j && typeof j === "object" ? j : { data: j };
  } catch {
    return {};
  }
}

async function trocarTikTok(corpo, env) {
  const secret = (env.TIKTOK_CLIENT_SECRET || "").trim();
  if (!secret) {
    return respostaJson({ erro: "TIKTOK_CLIENT_SECRET nao configurado no Worker." }, 500);
  }
  const clientKey = (env.TIKTOK_CLIENT_KEY || corpo.client_id || "").trim();
  if (!clientKey) {
    return respostaJson({ erro: "client_id (client_key) do TikTok ausente." }, 400);
  }
  if (env.TIKTOK_CLIENT_KEY && corpo.client_id && corpo.client_id !== env.TIKTOK_CLIENT_KEY) {
    return respostaJson({ erro: "client_key divergente do configurado no Worker." }, 403);
  }

  const form = new URLSearchParams({
    client_key: clientKey,
    client_secret: secret,
    code: String(corpo.code || ""),
    grant_type: "authorization_code",
    redirect_uri: String(corpo.redirect_uri || ""),
  });
  if (corpo.code_verifier) {
    form.set("code_verifier", String(corpo.code_verifier));
  }

  const resp = await fetch(TIKTOK_TOKEN_URL, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: form.toString(),
  });
  const dados = await lerJsonSeguro(resp);
  const payload =
    dados.data && typeof dados.data === "object" && dados.data.access_token
      ? dados.data
      : dados;

  if (!resp.ok || !payload.access_token) {
    const msg =
      payload.error_description ||
      dados.error_description ||
      payload.error ||
      "Falha na troca de token com o TikTok.";
    return respostaJson({ erro: String(msg) }, resp.status >= 400 ? resp.status : 400);
  }

  return respostaJson({
    access_token: payload.access_token,
    refresh_token: payload.refresh_token || null,
    expires_in: Number(payload.expires_in || 86400),
    refresh_expires_in: Number(payload.refresh_expires_in || 31536000),
    open_id: payload.open_id || null,
    scope: payload.scope || "",
    token_type: payload.token_type || "Bearer",
  });
}

async function renovarTikTok(corpo, env) {
  const secret = (env.TIKTOK_CLIENT_SECRET || "").trim();
  if (!secret) {
    return respostaJson({ erro: "TIKTOK_CLIENT_SECRET nao configurado no Worker." }, 500);
  }
  const clientKey = (env.TIKTOK_CLIENT_KEY || corpo.client_id || "").trim();
  if (!clientKey) {
    return respostaJson({ erro: "client_id (client_key) do TikTok ausente." }, 400);
  }

  const form = new URLSearchParams({
    client_key: clientKey,
    client_secret: secret,
    grant_type: "refresh_token",
    refresh_token: String(corpo.refresh_token || ""),
  });

  const resp = await fetch(TIKTOK_TOKEN_URL, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: form.toString(),
  });
  const dados = await lerJsonSeguro(resp);
  const payload =
    dados.data && typeof dados.data === "object" && dados.data.access_token
      ? dados.data
      : dados;

  if (!resp.ok || !payload.access_token) {
    const msg =
      payload.error_description ||
      dados.error_description ||
      payload.error ||
      "Falha ao renovar token do TikTok.";
    return respostaJson({ erro: String(msg) }, resp.status >= 400 ? resp.status : 400);
  }

  return respostaJson({
    access_token: payload.access_token,
    refresh_token: payload.refresh_token || corpo.refresh_token,
    expires_in: Number(payload.expires_in || 86400),
    refresh_expires_in: payload.refresh_expires_in ? Number(payload.refresh_expires_in) : undefined,
    open_id: payload.open_id || undefined,
    scope: payload.scope || undefined,
    token_type: payload.token_type || "Bearer",
  });
}

async function trocarInstagram(corpo, env) {
  const secret = (env.META_APP_SECRET || "").trim();
  if (!secret) {
    return respostaJson({ erro: "META_APP_SECRET nao configurado no Worker." }, 500);
  }
  const appId = (env.META_APP_ID || corpo.client_id || "").trim();
  if (!appId) {
    return respostaJson({ erro: "client_id (App ID da Meta) ausente." }, 400);
  }
  if (env.META_APP_ID && corpo.client_id && corpo.client_id !== env.META_APP_ID) {
    return respostaJson({ erro: "App ID da Meta divergente do configurado no Worker." }, 403);
  }

  const versao = String(corpo.versao_api || env.VERSAO_META_API || "v21.0");
  const baseUrl = `https://graph.facebook.com/${encodeURIComponent(versao)}/oauth/access_token`;

  const qsCurto = new URLSearchParams({
    client_id: appId,
    client_secret: secret,
    redirect_uri: String(corpo.redirect_uri || ""),
    code: String(corpo.code || ""),
  });
  if (corpo.code_verifier) {
    qsCurto.set("code_verifier", String(corpo.code_verifier));
  }

  const respCurto = await fetch(`${baseUrl}?${qsCurto.toString()}`);
  const dadosCurto = await lerJsonSeguro(respCurto);
  if (!respCurto.ok || !dadosCurto.access_token) {
    const msg =
      (dadosCurto.error && dadosCurto.error.message) ||
      "Falha ao obter token inicial da Meta.";
    return respostaJson({ erro: String(msg) }, respCurto.status >= 400 ? respCurto.status : 400);
  }

  let tokenFinal = dadosCurto.access_token;
  let expiresIn = Number(dadosCurto.expires_in || 3600);

  // Converte ja dentro do Worker para token longo (~60 dias) sem expor o secret
  const qsLongo = new URLSearchParams({
    grant_type: "fb_exchange_token",
    client_id: appId,
    client_secret: secret,
    fb_exchange_token: tokenFinal,
  });
  const respLongo = await fetch(`${baseUrl}?${qsLongo.toString()}`);
  const dadosLongo = await lerJsonSeguro(respLongo);
  if (respLongo.ok && dadosLongo.access_token) {
    tokenFinal = dadosLongo.access_token;
    expiresIn = Number(dadosLongo.expires_in || 5184000);
  }

  return respostaJson({
    access_token: tokenFinal,
    refresh_token: tokenFinal,
    expires_in: expiresIn,
    token_type: "Bearer",
    versao_api: versao,
  });
}

async function renovarInstagram(corpo, env) {
  const secret = (env.META_APP_SECRET || "").trim();
  if (!secret) {
    return respostaJson({ erro: "META_APP_SECRET nao configurado no Worker." }, 500);
  }
  const appId = (env.META_APP_ID || corpo.client_id || "").trim();
  if (!appId) {
    return respostaJson({ erro: "client_id (App ID da Meta) ausente." }, 400);
  }

  const versao = String(corpo.versao_api || env.VERSAO_META_API || "v21.0");
  const baseUrl = `https://graph.facebook.com/${encodeURIComponent(versao)}/oauth/access_token`;
  const qs = new URLSearchParams({
    grant_type: "fb_exchange_token",
    client_id: appId,
    client_secret: secret,
    fb_exchange_token: String(corpo.refresh_token || ""),
  });

  const resp = await fetch(`${baseUrl}?${qs.toString()}`);
  const dados = await lerJsonSeguro(resp);
  if (!resp.ok || !dados.access_token) {
    const msg =
      (dados.error && dados.error.message) ||
      "Falha ao renovar token longo da Meta.";
    return respostaJson({ erro: String(msg) }, resp.status >= 400 ? resp.status : 400);
  }

  return respostaJson({
    access_token: dados.access_token,
    refresh_token: dados.access_token,
    expires_in: Number(dados.expires_in || 5184000),
    token_type: "Bearer",
    versao_api: versao,
  });
}

async function trocarYouTube(corpo, env) {
  const clientId = (env.YOUTUBE_CLIENT_ID || corpo.client_id || "").trim();
  if (!clientId) {
    return respostaJson({ erro: "client_id do YouTube ausente." }, 400);
  }
  const form = new URLSearchParams({
    client_id: clientId,
    code: String(corpo.code || ""),
    grant_type: "authorization_code",
    redirect_uri: String(corpo.redirect_uri || ""),
  });
  if (corpo.code_verifier) {
    form.set("code_verifier", String(corpo.code_verifier));
  }
  if (env.YOUTUBE_CLIENT_SECRET) {
    form.set("client_secret", env.YOUTUBE_CLIENT_SECRET.trim());
  }

  const resp = await fetch(GOOGLE_TOKEN_URL, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: form.toString(),
  });
  const dados = await lerJsonSeguro(resp);
  if (!resp.ok || !dados.access_token) {
    const msg = dados.error_description || dados.error || "Falha ao obter token do YouTube.";
    return respostaJson({ erro: String(msg) }, resp.status >= 400 ? resp.status : 400);
  }
  return respostaJson({
    access_token: dados.access_token,
    refresh_token: dados.refresh_token || null,
    expires_in: Number(dados.expires_in || 3600),
    scope: dados.scope || "",
    token_type: dados.token_type || "Bearer",
  });
}

async function renovarYouTube(corpo, env) {
  const clientId = (env.YOUTUBE_CLIENT_ID || corpo.client_id || "").trim();
  if (!clientId) {
    return respostaJson({ erro: "client_id do YouTube ausente." }, 400);
  }
  const form = new URLSearchParams({
    client_id: clientId,
    grant_type: "refresh_token",
    refresh_token: String(corpo.refresh_token || ""),
  });
  if (env.YOUTUBE_CLIENT_SECRET) {
    form.set("client_secret", env.YOUTUBE_CLIENT_SECRET.trim());
  }

  const resp = await fetch(GOOGLE_TOKEN_URL, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: form.toString(),
  });
  const dados = await lerJsonSeguro(resp);
  if (!resp.ok || !dados.access_token) {
    const msg = dados.error_description || dados.error || "Falha ao renovar token do YouTube.";
    return respostaJson({ erro: String(msg) }, resp.status >= 400 ? resp.status : 400);
  }
  return respostaJson({
    access_token: dados.access_token,
    refresh_token: dados.refresh_token || undefined,
    expires_in: Number(dados.expires_in || 3600),
    token_type: dados.token_type || "Bearer",
  });
}

export default {
  /**
   * @param {Request} request
   * @param {Env} env
   */
  async fetch(request, env) {
    const url = new URL(request.url);
    const caminho = url.pathname.replace(/\/+$/, "") || "/";

    if (request.method === "GET" && caminho === "/health") {
      return respostaJson({
        ok: true,
        servico: "cortelive-servidor-tokens",
        redes: ["instagram", "tiktok", "youtube"],
      });
    }

    if (request.method !== "POST") {
      return respostaJson({ erro: "Metodo nao permitido." }, 405);
    }

    let corpo;
    try {
      corpo = await request.json();
    } catch {
      return respostaJson({ erro: "JSON invalido no corpo da requisicao." }, 400);
    }

    const rede = String((corpo && corpo.rede) || "").toLowerCase().trim();
    if (!["tiktok", "instagram", "youtube"].includes(rede)) {
      return respostaJson({ erro: "Rede invalida. Use tiktok, instagram ou youtube." }, 400);
    }

    if (caminho === "/oauth/trocar") {
      if (!corpo.code) {
        return respostaJson({ erro: "Campo 'code' obrigatorio." }, 400);
      }
      if (!redirectLoopbackValido(corpo.redirect_uri)) {
        return respostaJson(
          { erro: "Por seguranca, apenas redirect_uri em 127.0.0.1 ou localhost e aceito." },
          400
        );
      }
      if (rede === "tiktok") return trocarTikTok(corpo, env);
      if (rede === "instagram") return trocarInstagram(corpo, env);
      return trocarYouTube(corpo, env);
    }

    if (caminho === "/oauth/renovar") {
      if (!corpo.refresh_token) {
        return respostaJson({ erro: "Campo 'refresh_token' obrigatorio." }, 400);
      }
      if (rede === "tiktok") return renovarTikTok(corpo, env);
      if (rede === "instagram") return renovarInstagram(corpo, env);
      return renovarYouTube(corpo, env);
    }

    return respostaJson({ erro: "Rota nao encontrada." }, 404);
  },
};
