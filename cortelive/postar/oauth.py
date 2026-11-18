"""Fluxos OAuth 2.0 oficiais no navegador (loopback local + PKCE) e refresh automatico.

Politica de segredos:
  - YouTube: cliente OAuth tipo 'Desktop app' com PKCE S256 (nenhum segredo no repo).
  - Instagram (Meta) e TikTok: NENHUM segredo fica no PC do usuario; a troca de codigo
    e a renovacao passam exclusivamente pelo Cloudflare Worker (`servidor_tokens`).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import html
import secrets
import time
import urllib.parse
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Callable

import requests
from requests_oauthlib import OAuth2Session

from . import cofre
from .config import (
    REDES_SUPORTADAS,
    carregar_config_postar,
    client_id_rede,
    segredo_desktop_youtube,
    url_servidor_tokens,
)

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
YOUTUBE_CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"
ESCOPOS_YOUTUBE = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]

TIKTOK_AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TIKTOK_USER_URL = "https://open.tiktokapis.com/v2/user/info/"
ESCOPOS_TIKTOK = ["user.info.basic", "video.upload", "video.publish"]

ESCOPOS_INSTAGRAM = [
    "instagram_basic",
    "instagram_content_publish",
    "pages_show_list",
    "pages_read_engagement",
]

MARGEM_REFRESH_S = 300


class ErroOAuth(RuntimeError):
    """Erro amigavel em portugues durante autenticacao ou renovacao OAuth."""


def gerar_pkce() -> tuple[str, str]:
    """Gera (code_verifier, code_challenge S256) conforme RFC 7636."""
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _pagina_retorno_html(sucesso: bool, rede: str, mensagem: str) -> bytes:
    titulo = f"{rede.capitalize()} conectado ao CorteLive" if sucesso else f"Nao foi possivel conectar {rede.capitalize()}"
    cor_borda = "#16a34a" if sucesso else "#dc2626"
    cor_icone = "#15803d" if sucesso else "#b91c1c"
    svg = (
        '<svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
        '<path d="M20 6 9 17l-5-5"/></svg>'
        if sucesso
        else '<svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
             'stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
             '<circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/>'
             '<line x1="12" y1="16" x2="12.01" y2="16"/></svg>'
    )
    corpo = f"""<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(titulo)}</title>
  <style>
    :root {{ color-scheme: light dark; }}
    body {{
      margin: 0; min-height: 100vh; display: grid; place-items: center; padding: 24px;
      font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
      background: #0f172a; color: #f8fafc; line-height: 1.6;
    }}
    main {{
      max-width: 460px; width: 100%; background: #1e293b;
      border: 1px solid rgba(255,255,255,0.10); border-top: 4px solid {cor_borda};
      border-radius: 12px; padding: 28px 24px;
    }}
    .icone {{ color: {cor_icone}; margin-bottom: 12px; }}
    h1 {{ font-size: 1.25rem; margin: 0 0 8px; letter-spacing: -0.01em; }}
    p {{ margin: 0; color: #cbd5e1; font-size: 1rem; }}
    @media (prefers-color-scheme: light) {{
      body {{ background: #f8fafc; color: #0f172a; }}
      main {{ background: #ffffff; border-color: rgba(15,23,42,0.12); border-top-color: {cor_borda}; }}
      p {{ color: #334155; }}
    }}
  </style>
</head>
<body>
  <main role="status">
    <div class="icone">{svg}</div>
    <h1>{html.escape(titulo)}</h1>
    <p>{html.escape(mensagem)}</p>
  </main>
</body>
</html>"""
    return corpo.encode("utf-8")


def _extrair_host_porta_caminho(redirect_uri: str, porta_padrao: int) -> tuple[str, int, str]:
    parsed = urllib.parse.urlparse(redirect_uri)
    host = parsed.hostname or "127.0.0.1"
    if host not in ("127.0.0.1", "localhost"):
        raise ErroOAuth(
            f"O redirect_uri '{redirect_uri}' precisa apontar para 127.0.0.1 ou localhost para login seguro local."
        )
    porta = parsed.port or porta_padrao
    caminho = parsed.path or "/callback"
    return host, porta, caminho


def aguardar_callback_loopback(
    redirect_uri: str,
    estado_esperado: str,
    rede: str,
    porta_padrao: int = 8765,
    timeout_s: float = 180.0,
    ao_escutar: Callable[[str], None] | None = None,
) -> str:
    """Sobe servidor HTTP temporario em loopback e aguarda o codigo OAuth."""
    host, porta, caminho_esperado = _extrair_host_porta_caminho(redirect_uri, porta_padrao)
    resultado: dict[str, str] = {}

    class _CallbackHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

        def do_GET(self) -> None:  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path == "/favicon.ico":
                self.send_response(204)
                self.end_headers()
                return
            if parsed.path.rstrip("/") != caminho_esperado.rstrip("/"):
                self.send_response(404)
                self.end_headers()
                return

            qs = urllib.parse.parse_qs(parsed.query)
            erro = (qs.get("error_description") or qs.get("error") or [""])[0]
            state_recebido = (qs.get("state") or [""])[0]
            codigo = (qs.get("code") or [""])[0]

            if erro:
                resultado["erro"] = f"Autorizacao recusada ou cancelada no navegador: {erro}"
                corpo = _pagina_retorno_html(False, rede, resultado["erro"])
                status_http = 400
            elif not state_recebido or not hmac.compare_digest(state_recebido, estado_esperado):
                resultado["erro"] = "Falha de seguranca: parametro 'state' invalido no retorno do login."
                corpo = _pagina_retorno_html(False, rede, resultado["erro"])
                status_http = 400
            elif not codigo:
                resultado["erro"] = "A rede social nao devolveu o codigo de autorizacao."
                corpo = _pagina_retorno_html(False, rede, resultado["erro"])
                status_http = 400
            else:
                resultado["code"] = urllib.parse.unquote(codigo)
                corpo = _pagina_retorno_html(
                    True,
                    rede,
                    "Autorizacao concluida com sucesso! Voce ja pode fechar esta aba e voltar para o CorteLive.",
                )
                status_http = 200

            self.send_response(status_http)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(corpo)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(corpo)

    try:
        servidor = HTTPServer((host, porta), _CallbackHandler)
    except OSError as exc:
        raise ErroOAuth(
            f"Nao consegui abrir a porta local {porta} ({exc}). "
            f"Feche outro login em andamento ou mude 'porta_oauth' na configuracao."
        ) from exc

    servidor.timeout = 0.5
    url_escuta = f"http://{host}:{servidor.server_port}{caminho_esperado}"
    if ao_escutar:
        ao_escutar(url_escuta)

    prazo = time.monotonic() + timeout_s
    try:
        while "code" not in resultado and "erro" not in resultado:
            if time.monotonic() >= prazo:
                raise ErroOAuth(
                    f"Tempo esgotado ({int(timeout_s)} s) esperando o login no navegador para {rede}."
                )
            servidor.handle_request()
    finally:
        servidor.server_close()

    if "erro" in resultado:
        raise ErroOAuth(resultado["erro"])
    return resultado["code"]


def montar_url_autorizacao(
    rede: str,
    cfg: dict[str, Any],
    state: str | None = None,
    code_challenge: str | None = None,
    redirect_uri_override: str | None = None,
) -> tuple[str, str, str]:
    """Monta a URL oficial de login OAuth com PKCE S256. Retorna (url, state, redirect_uri)."""
    cid = client_id_rede(cfg, rede)
    bloco = cfg.get(rede, {}) or {}
    porta_padrao = int(cfg.get("porta_oauth", 8765))
    redirect_uri = redirect_uri_override or str(
        bloco.get("redirect_uri") or f"http://127.0.0.1:{porta_padrao}/callback"
    )
    estado = state or secrets.token_urlsafe(24)

    if rede == "youtube":
        oauth = OAuth2Session(client_id=cid, redirect_uri=redirect_uri, scope=ESCOPOS_YOUTUBE, state=estado)
        extras: dict[str, str] = {"access_type": "offline", "prompt": "consent"}
        if code_challenge:
            extras["code_challenge"] = code_challenge
            extras["code_challenge_method"] = "S256"
        url, _ = oauth.authorization_url(GOOGLE_AUTH_URL, **extras)
        return url, estado, redirect_uri

    if rede == "instagram":
        versao = str(bloco.get("versao_api") or "v21.0")
        auth_base = f"https://www.facebook.com/{versao}/dialog/oauth"
        oauth = OAuth2Session(
            client_id=cid,
            redirect_uri=redirect_uri,
            scope=",".join(ESCOPOS_INSTAGRAM),
            state=estado,
        )
        extras = {}
        if code_challenge:
            extras["code_challenge"] = code_challenge
            extras["code_challenge_method"] = "S256"
        url, _ = oauth.authorization_url(auth_base, **extras)
        return url, estado, redirect_uri

    if rede == "tiktok":
        params = {
            "client_key": cid,
            "response_type": "code",
            "scope": ",".join(ESCOPOS_TIKTOK),
            "redirect_uri": redirect_uri,
            "state": estado,
        }
        if code_challenge:
            params["code_challenge"] = code_challenge
            params["code_challenge_method"] = "S256"
        url = f"{TIKTOK_AUTH_URL}?{urllib.parse.urlencode(params)}"
        return url, estado, redirect_uri

    raise ValueError(f"Rede invalida: {rede}")


def _trocar_via_servidor_tokens(
    rede: str,
    codigo: str,
    cid: str,
    redirect_uri: str,
    code_verifier: str,
    url_worker: str,
    sessao_http: requests.Session,
    extras: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Troca o codigo OAuth falando APENAS com o servidor_tokens (sem segredo no PC)."""
    payload: dict[str, Any] = {
        "rede": rede,
        "client_id": cid,
        "code": codigo,
        "redirect_uri": redirect_uri,
        "code_verifier": code_verifier,
    }
    if extras:
        payload.update(extras)

    try:
        resp = sessao_http.post(
            f"{url_worker.rstrip('/')}/oauth/trocar",
            json=payload,
            timeout=30,
        )
    except requests.RequestException as exc:
        raise ErroOAuth(f"Nao consegui contatar o servidor_tokens ({url_worker}): {exc}") from exc

    dados = _ler_json_seguro(resp)
    if resp.status_code >= 400 or "access_token" not in dados:
        detalhe = dados.get("erro") or dados.get("error_description") or dados.get("error") or resp.text[:200]
        raise ErroOAuth(f"Falha ao obter token de {rede.capitalize()} no servidor_tokens: {detalhe}")
    return dados


def _renovar_via_servidor_tokens(
    rede: str,
    refresh_token: str,
    cid: str,
    url_worker: str,
    sessao_http: requests.Session,
    extras: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Renova o token falando APENAS com o servidor_tokens (sem segredo no PC)."""
    payload: dict[str, Any] = {
        "rede": rede,
        "client_id": cid,
        "refresh_token": refresh_token,
    }
    if extras:
        payload.update(extras)

    try:
        resp = sessao_http.post(
            f"{url_worker.rstrip('/')}/oauth/renovar",
            json=payload,
            timeout=30,
        )
    except requests.RequestException as exc:
        raise ErroOAuth(f"Nao consegui contatar o servidor_tokens ({url_worker}) para renovar {rede}: {exc}") from exc

    dados = _ler_json_seguro(resp)
    if resp.status_code >= 400 or "access_token" not in dados:
        detalhe = dados.get("erro") or dados.get("error_description") or dados.get("error") or resp.text[:200]
        raise ErroOAuth(f"Nao foi possivel renovar o login de {rede.capitalize()} ({detalhe}). Conecte novamente.")
    return dados


def _trocar_codigo_youtube(
    codigo: str,
    cid: str,
    cfg: dict[str, Any],
    redirect_uri: str,
    code_verifier: str,
    sessao_http: requests.Session,
) -> dict[str, Any]:
    bloco_yt = cfg.get("youtube") or {}
    if bool(bloco_yt.get("usar_servidor_tokens", False)):
        url_worker = url_servidor_tokens(cfg, obrigatorio=True)
        dados = _trocar_via_servidor_tokens(
            "youtube", codigo, cid, redirect_uri, code_verifier, url_worker, sessao_http
        )
    else:
        form: dict[str, str] = {
            "client_id": cid,
            "code": codigo,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
        }
        seg_desktop = segredo_desktop_youtube(cfg)
        if seg_desktop:
            form["client_secret"] = seg_desktop
        resp = sessao_http.post(GOOGLE_TOKEN_URL, data=form, timeout=30)
        dados = _ler_json_seguro(resp)
        if resp.status_code >= 400 or "access_token" not in dados:
            detalhe = dados.get("error_description") or dados.get("error") or resp.text[:200]
            raise ErroOAuth(f"Falha ao obter token do YouTube: {detalhe}")

    agora = time.time()
    access_token = dados["access_token"]
    conta = None
    canal_id = None
    try:
        r_canal = sessao_http.get(
            YOUTUBE_CHANNELS_URL,
            params={"part": "snippet", "mine": "true"},
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=20,
        )
        if r_canal.ok:
            itens = r_canal.json().get("items") or []
            if itens:
                canal_id = itens[0].get("id")
                conta = (itens[0].get("snippet") or {}).get("title")
    except requests.RequestException:
        pass

    return {
        "access_token": access_token,
        "refresh_token": dados.get("refresh_token"),
        "token_type": dados.get("token_type", "Bearer"),
        "expira_em": agora + float(dados.get("expires_in", 3600)),
        "escopo": dados.get("scope", " ".join(ESCOPOS_YOUTUBE)),
        "conta": conta or "Canal YouTube",
        "canal_id": canal_id,
    }


def _trocar_codigo_instagram(
    codigo: str,
    cid: str,
    cfg: dict[str, Any],
    redirect_uri: str,
    code_verifier: str,
    bloco_cfg: dict[str, Any],
    sessao_http: requests.Session,
) -> dict[str, Any]:
    versao = str(bloco_cfg.get("versao_api") or "v21.0")
    url_worker = url_servidor_tokens(cfg, obrigatorio=True)

    dados_tok = _trocar_via_servidor_tokens(
        rede="instagram",
        codigo=codigo,
        cid=cid,
        redirect_uri=redirect_uri,
        code_verifier=code_verifier,
        url_worker=url_worker,
        sessao_http=sessao_http,
        extras={"versao_api": versao},
    )
    token_final = dados_tok["access_token"]
    expires_in = float(dados_tok.get("expires_in", 5184000))

    # Descobre a conta Instagram Profissional conectada a Pagina do Facebook (sem precisar de secret)
    ig_fixo = str(bloco_cfg.get("ig_user_id") or "").strip()
    resp_paginas = sessao_http.get(
        f"https://graph.facebook.com/{versao}/me/accounts",
        params={
            "fields": "id,name,instagram_business_account{id,username,name}",
            "access_token": token_final,
        },
        timeout=30,
    )
    dados_pag = _ler_json_seguro(resp_paginas)
    if resp_paginas.status_code >= 400:
        msg = (dados_pag.get("error") or {}).get("message") or resp_paginas.text[:200]
        raise ErroOAuth(f"Nao foi possivel listar Paginas do Facebook vinculadas: {msg}")

    ig_id = ""
    ig_user = ""
    pagina_nome = ""
    for pag in dados_pag.get("data") or []:
        ig_obj = pag.get("instagram_business_account") or {}
        cand_id = str(ig_obj.get("id") or "")
        if not cand_id:
            continue
        if ig_fixo and cand_id != ig_fixo:
            continue
        ig_id = cand_id
        ig_user = str(ig_obj.get("username") or ig_obj.get("name") or "")
        pagina_nome = str(pag.get("name") or "")
        break

    if not ig_id and ig_fixo:
        ig_id = ig_fixo

    if not ig_id:
        raise ErroOAuth(
            "Nenhuma conta Instagram Profissional (Criador ou Comercial) vinculada a uma Pagina do Facebook "
            "foi encontrada neste login. Mude sua conta do Instagram para Profissional e vincule-a a uma Pagina."
        )

    agora = time.time()
    return {
        "access_token": token_final,
        "refresh_token": dados_tok.get("refresh_token") or token_final,
        "token_type": "Bearer",
        "expira_em": agora + expires_in,
        "ig_user_id": ig_id,
        "conta": f"@{ig_user}" if ig_user and not ig_user.startswith("@") else (ig_user or f"IG {ig_id}"),
        "pagina_facebook": pagina_nome,
        "versao_api": versao,
    }


def _trocar_codigo_tiktok(
    codigo: str,
    cid: str,
    cfg: dict[str, Any],
    redirect_uri: str,
    code_verifier: str,
    sessao_http: requests.Session,
) -> dict[str, Any]:
    url_worker = url_servidor_tokens(cfg, obrigatorio=True)
    corpo = _trocar_via_servidor_tokens(
        rede="tiktok",
        codigo=codigo,
        cid=cid,
        redirect_uri=redirect_uri,
        code_verifier=code_verifier,
        url_worker=url_worker,
        sessao_http=sessao_http,
    )

    agora = time.time()
    access_token = corpo["access_token"]
    open_id = corpo.get("open_id")
    conta = None
    try:
        r_user = sessao_http.get(
            TIKTOK_USER_URL,
            params={"fields": "open_id,display_name,username"},
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=20,
        )
        if r_user.ok:
            u = ((r_user.json().get("data") or {}).get("user")) or {}
            nome = u.get("username") or u.get("display_name")
            if nome:
                conta = f"@{nome}" if u.get("username") and not str(nome).startswith("@") else str(nome)
    except requests.RequestException:
        pass

    return {
        "access_token": access_token,
        "refresh_token": corpo.get("refresh_token"),
        "expira_em": agora + float(corpo.get("expires_in", 86400)),
        "refresh_expira_em": agora + float(corpo.get("refresh_expires_in", 31536000)),
        "open_id": open_id,
        "escopo": corpo.get("scope", ",".join(ESCOPOS_TIKTOK)),
        "conta": conta or f"TikTok ({open_id or 'conectado'})",
    }


def conectar_rede(
    rede: str,
    cfg: dict[str, Any] | None = None,
    diretorio_contas: Path | None = None,
    abrir_navegador: bool = True,
    sessao_http: requests.Session | None = None,
    ao_url_gerada: Callable[[str], None] | None = None,
    porta_override: int | None = None,
) -> dict[str, Any]:
    """Executa o fluxo OAuth 2.0 completo (navegador + loopback + PKCE) e salva o token criptografado."""
    if rede not in REDES_SUPORTADAS:
        raise ErroOAuth(f"Rede invalida '{rede}'. Opcoes: {', '.join(REDES_SUPORTADAS)}.")

    cfg_efetiva = cfg or carregar_config_postar()
    cid = client_id_rede(cfg_efetiva, rede)
    if rede in ("instagram", "tiktok"):
        # Valida antes de abrir o navegador que o servidor_tokens esta configurado
        url_servidor_tokens(cfg_efetiva, obrigatorio=True)

    bloco = cfg_efetiva.get(rede, {}) or {}
    porta = int(porta_override or cfg_efetiva.get("porta_oauth", 8765))
    redirect_padrao = str(bloco.get("redirect_uri") or f"http://127.0.0.1:{porta}/callback")
    if porta_override:
        parsed = urllib.parse.urlparse(redirect_padrao)
        host = parsed.hostname or "127.0.0.1"
        caminho = parsed.path or "/callback"
        redirect_padrao = f"http://{host}:{porta_override}{caminho}"

    code_verifier, code_challenge = gerar_pkce()
    url_auth, estado, redirect_uri = montar_url_autorizacao(
        rede,
        cfg_efetiva,
        code_challenge=code_challenge,
        redirect_uri_override=redirect_padrao,
    )

    if ao_url_gerada:
        ao_url_gerada(url_auth)

    def _ao_escutar(_url_local: str) -> None:
        if abrir_navegador:
            webbrowser.open(url_auth, new=2)

    codigo = aguardar_callback_loopback(
        redirect_uri=redirect_uri,
        estado_esperado=estado,
        rede=rede,
        porta_padrao=porta,
        timeout_s=float(cfg_efetiva.get("timeout_oauth_s", 180)),
        ao_escutar=_ao_escutar,
    )

    http = sessao_http or requests.Session()
    if rede == "youtube":
        dados_token = _trocar_codigo_youtube(codigo, cid, cfg_efetiva, redirect_uri, code_verifier, http)
    elif rede == "instagram":
        dados_token = _trocar_codigo_instagram(codigo, cid, cfg_efetiva, redirect_uri, code_verifier, bloco, http)
    else:
        dados_token = _trocar_codigo_tiktok(codigo, cid, cfg_efetiva, redirect_uri, code_verifier, http)

    arq = cofre.salvar_token(rede, dados_token, diretorio=diretorio_contas)
    return {
        "rede": rede,
        "conectado": True,
        "conta": dados_token.get("conta"),
        "arquivo_token": str(arq),
        "criptografado_dpapi": cofre.token_esta_criptografado_em_disco(rede, diretorio=diretorio_contas),
        "expira_em": dados_token.get("expira_em"),
    }


def renovar_token_rede(
    rede: str,
    cfg: dict[str, Any] | None = None,
    diretorio_contas: Path | None = None,
    sessao_http: requests.Session | None = None,
) -> dict[str, Any]:
    """Renova automaticamente o access_token da rede usando o refresh_token salvo."""
    atual = cofre.carregar_token(rede, diretorio=diretorio_contas)
    if not atual:
        raise ErroOAuth(f"Conta '{rede}' nao esta conectada. Rode: python -m cortelive.postar conectar {rede}")

    cfg_efetiva = cfg or carregar_config_postar()
    cid = client_id_rede(cfg_efetiva, rede)
    http = sessao_http or requests.Session()
    agora = time.time()

    if rede == "youtube":
        rt = atual.get("refresh_token")
        if not rt:
            raise ErroOAuth("Token do YouTube expirou e nao possui refresh_token. Conecte novamente.")
        bloco_yt = cfg_efetiva.get("youtube") or {}
        if bool(bloco_yt.get("usar_servidor_tokens", False)):
            url_worker = url_servidor_tokens(cfg_efetiva, obrigatorio=True)
            dados = _renovar_via_servidor_tokens("youtube", str(rt), cid, url_worker, http)
        else:
            form: dict[str, str] = {
                "client_id": cid,
                "refresh_token": str(rt),
                "grant_type": "refresh_token",
            }
            seg_desktop = segredo_desktop_youtube(cfg_efetiva)
            if seg_desktop:
                form["client_secret"] = seg_desktop
            resp = http.post(GOOGLE_TOKEN_URL, data=form, timeout=30)
            dados = _ler_json_seguro(resp)
            if resp.status_code >= 400 or "access_token" not in dados:
                detalhe = dados.get("error_description") or dados.get("error") or resp.text[:200]
                raise ErroOAuth(f"Nao foi possivel renovar o login do YouTube ({detalhe}). Conecte novamente.")
        atual["access_token"] = dados["access_token"]
        if dados.get("refresh_token"):
            atual["refresh_token"] = dados["refresh_token"]
        atual["expira_em"] = agora + float(dados.get("expires_in", 3600))

    elif rede == "tiktok":
        rt = atual.get("refresh_token")
        if not rt:
            raise ErroOAuth("Token do TikTok expirou e nao possui refresh_token. Conecte novamente.")
        url_worker = url_servidor_tokens(cfg_efetiva, obrigatorio=True)
        corpo = _renovar_via_servidor_tokens("tiktok", str(rt), cid, url_worker, http)
        atual["access_token"] = corpo["access_token"]
        # TikTok rotaciona o refresh_token a cada renovacao
        if corpo.get("refresh_token"):
            atual["refresh_token"] = corpo["refresh_token"]
        atual["expira_em"] = agora + float(corpo.get("expires_in", 86400))
        if corpo.get("refresh_expires_in"):
            atual["refresh_expira_em"] = agora + float(corpo["refresh_expires_in"])

    elif rede == "instagram":
        versao = str(atual.get("versao_api") or (cfg_efetiva.get("instagram") or {}).get("versao_api") or "v21.0")
        token_base = atual.get("refresh_token") or atual.get("access_token")
        if not token_base:
            raise ErroOAuth("Token do Instagram ausente. Conecte novamente.")
        url_worker = url_servidor_tokens(cfg_efetiva, obrigatorio=True)
        dados = _renovar_via_servidor_tokens(
            "instagram",
            str(token_base),
            cid,
            url_worker,
            http,
            extras={"versao_api": versao},
        )
        atual["access_token"] = dados["access_token"]
        atual["refresh_token"] = dados.get("refresh_token") or dados["access_token"]
        atual["expira_em"] = agora + float(dados.get("expires_in", 5184000))

    atual["atualizado_em"] = agora
    cofre.salvar_token(rede, atual, diretorio=diretorio_contas)
    return atual


def obter_token_valido(
    rede: str,
    cfg: dict[str, Any] | None = None,
    diretorio_contas: Path | None = None,
    sessao_http: requests.Session | None = None,
    forcar_refresh: bool = False,
) -> dict[str, Any]:
    """Devolve os dados de token prontos para uso, renovando sozinho se estiver perto de vencer."""
    atual = cofre.carregar_token(rede, diretorio=diretorio_contas)
    if not atual:
        raise ErroOAuth(
            f"Conta de {rede.capitalize()} nao conectada. Use 'python -m cortelive.postar conectar {rede}' primeiro."
        )

    expira_em = float(atual.get("expira_em") or 0)
    precisa_refresh = forcar_refresh or (expira_em > 0 and time.time() + MARGEM_REFRESH_S >= expira_em)
    if precisa_refresh:
        return renovar_token_rede(
            rede,
            cfg=cfg,
            diretorio_contas=diretorio_contas,
            sessao_http=sessao_http,
        )
    return atual


def status_contas(
    cfg: dict[str, Any] | None = None,
    diretorio_contas: Path | None = None,
    sessao_http: requests.Session | None = None,
    renovar_se_expirado: bool = True,
) -> dict[str, dict[str, Any]]:
    """Retorna o estado de conexao e validade do token para cada rede suportada (modo direto)."""
    cfg_efetiva = cfg or carregar_config_postar()
    saida: dict[str, dict[str, Any]] = {}
    agora = time.time()

    for rede in REDES_SUPORTADAS:
        try:
            dados = cofre.carregar_token(rede, diretorio=diretorio_contas)
        except Exception as exc:
            saida[rede] = {
                "conectado": False,
                "valido": False,
                "conta": None,
                "expira_em": None,
                "renovavel": False,
                "criptografado_dpapi": False,
                "detalhe": f"Erro ao ler token salvo: {exc}",
            }
            continue

        if not dados:
            saida[rede] = {
                "conectado": False,
                "valido": False,
                "conta": None,
                "expira_em": None,
                "renovavel": False,
                "criptografado_dpapi": False,
                "detalhe": "Nao conectada",
            }
            continue

        expira_em = float(dados.get("expira_em") or 0)
        renovavel = bool(dados.get("refresh_token"))
        valido = bool(dados.get("access_token")) and (expira_em == 0 or expira_em > agora + 60)
        detalhe = "Token valido"

        if not valido and renovar_se_expirado and renovavel:
            try:
                dados = renovar_token_rede(
                    rede,
                    cfg=cfg_efetiva,
                    diretorio_contas=diretorio_contas,
                    sessao_http=sessao_http,
                )
                expira_em = float(dados.get("expira_em") or 0)
                valido = True
                detalhe = "Token renovado automaticamente"
            except Exception as exc:
                detalhe = f"Token expirado e falha ao renovar: {exc}"
        elif not valido:
            detalhe = "Token expirado (reconecte a conta)"

        iso_exp = (
            datetime.fromtimestamp(expira_em, tz=timezone.utc).isoformat()
            if expira_em > 0
            else None
        )
        saida[rede] = {
            "conectado": True,
            "valido": valido,
            "conta": dados.get("conta"),
            "expira_em": round(expira_em, 1) if expira_em > 0 else None,
            "expira_iso": iso_exp,
            "renovavel": renovavel,
            "criptografado_dpapi": cofre.token_esta_criptografado_em_disco(rede, diretorio=diretorio_contas),
            "detalhe": detalhe,
        }

    return saida


def _ler_json_seguro(resp: requests.Response) -> dict[str, Any]:
    try:
        dados = resp.json()
        return dados if isinstance(dados, dict) else {"data": dados}
    except ValueError:
        return {}
