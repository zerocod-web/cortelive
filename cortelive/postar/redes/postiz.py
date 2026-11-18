"""Provedor Postiz Public API (`--via postiz`) para YouTube, Instagram e TikTok.

Documentacao oficial consultada (docs.postiz.com):
  - Visao geral, autenticacao (`Authorization: <api-key>` sem prefixo Bearer),
    URL base (`https://api.postiz.com/public/v1` no Cloud; `https://{dominio}/api/public/v1`
    no self-host), limites e codigos de erro:
      https://docs.postiz.com/public-api/introduction
  - Listagem de canais conectados (`GET /integrations`):
      https://docs.postiz.com/public-api/integrations/list
  - Upload multipart de arquivo MP4 (`POST /upload`) e status (`GET /media/{id}/status`):
      https://docs.postiz.com/public-api/uploads/upload-file
      https://docs.postiz.com/public-api/uploads/media-status
  - Criacao imediata/agendada de post (`POST /posts` com `type="now"`):
      https://docs.postiz.com/public-api/posts/create
  - Schemas especificos de cada rede (`settings.__type`):
      https://docs.postiz.com/public-api/providers/youtube
      https://docs.postiz.com/public-api/providers/instagram
      https://docs.postiz.com/public-api/providers/instagram-standalone
      https://docs.postiz.com/public-api/providers/tiktok
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re
import time
import urllib.parse
from typing import Any

import requests

from .. import cofre
from ..config import REDES_SUPORTADAS, _eh_placeholder
from . import CallbackProgresso, ErroAutenticacao, ErroEnvio, ErroTemporario, classificar_erro_http
from .instagram import _montar_legenda as _montar_legenda_ig
from .tiktok import _montar_legenda_tiktok
from .youtube import _extrair_tags as _extrair_tags_yt, _preparar_titulo_shorts

POSTIZ_CLOUD_API_URL = "https://api.postiz.com/public/v1"
POSTIZ_CLOUD_WEB_URL = "https://platform.postiz.com"

IDENTIFICADORES_POR_REDE: dict[str, tuple[str, ...]] = {
    "youtube": ("youtube",),
    "instagram": ("instagram", "instagram-standalone"),
    "tiktok": ("tiktok",),
}


def obter_base_url_postiz(cfg: dict[str, Any], dados_token: dict[str, Any] | None = None) -> str:
    """Retorna a URL base da Public API do Postiz (Cloud por padrao ou instancia self-host)."""
    bloco = cfg.get("postiz") or {}
    candidata = str(
        bloco.get("base_url")
        or (dados_token or {}).get("base_url")
        or POSTIZ_CLOUD_API_URL
    ).strip()
    if not candidata:
        candidata = POSTIZ_CLOUD_API_URL
    return candidata.rstrip("/")


def _derivar_web_url_postiz(base_url: str, cfg: dict[str, Any]) -> str:
    bloco = cfg.get("postiz") or {}
    web_cfg = str(bloco.get("web_url") or "").strip()
    if web_cfg:
        return web_cfg.rstrip("/")
    if "api.postiz.com" in base_url:
        return POSTIZ_CLOUD_WEB_URL
    parsed = urllib.parse.urlparse(base_url)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return POSTIZ_CLOUD_WEB_URL


def _erro_postiz(resp: requests.Response) -> str:
    try:
        dados = resp.json()
        if isinstance(dados, dict):
            msg = dados.get("message") or dados.get("error") or ""
            if isinstance(msg, list):
                return "; ".join(str(x) for x in msg)
            if isinstance(msg, dict):
                return str(msg.get("message") or msg)
            if msg:
                return str(msg)
    except ValueError:
        pass
    return resp.text[:220].strip()


def _cabecalhos_postiz(api_key: str) -> dict[str, str]:
    # Fato da doc oficial (docs.postiz.com/public-api/introduction):
    # O cabecalho Authorization recebe a chave pura (ou pos_...), SEM prefixo "Bearer ".
    return {"Authorization": api_key.strip()}


def listar_integracoes_postiz(
    api_key: str,
    base_url: str = POSTIZ_CLOUD_API_URL,
    sessao_http: requests.Session | None = None,
) -> list[dict[str, Any]]:
    """Chama GET /integrations e devolve os canais conectados na organizacao do Postiz."""
    http = sessao_http or requests.Session()
    url = f"{base_url.rstrip('/')}/integrations"
    try:
        resp = http.get(url, headers=_cabecalhos_postiz(api_key), timeout=30)
    except requests.RequestException as exc:
        raise ErroTemporario(f"Falha de rede ao consultar canais no Postiz: {exc}") from exc

    detalhe = _erro_postiz(resp)
    if resp.status_code == 403:
        raise ErroAutenticacao(f"Acesso negado pelo Postiz (HTTP 403): {detalhe}")
    classificar_erro_http(resp, "Erro ao listar canais no Postiz", detalhe)

    try:
        bruto = resp.json()
    except ValueError as exc:
        raise ErroEnvio("Postiz devolveu resposta invalida ao listar canais.") from exc

    lista = bruto if isinstance(bruto, list) else (bruto.get("integrations") if isinstance(bruto, dict) else [])
    if not isinstance(lista, list):
        lista = []

    canais: list[dict[str, Any]] = []
    for item in lista:
        if not isinstance(item, dict):
            continue
        cid = str(item.get("id") or "").strip()
        ident = str(item.get("identifier") or "").strip().lower()
        if not cid or not ident:
            continue
        canais.append(
            {
                "id": cid,
                "nome": str(item.get("name") or item.get("profile") or cid),
                "identifier": ident,
                "perfil": str(item.get("profile") or "").strip() or None,
                "desativado": bool(item.get("disabled", False)),
            }
        )
    return canais


def escolher_integracao_rede(
    rede: str,
    canais: list[dict[str, Any]],
    cfg: dict[str, Any],
) -> dict[str, Any] | None:
    """Seleciona o canal do Postiz correspondente a `youtube`, `instagram` ou `tiktok`."""
    aceitos = IDENTIFICADORES_POR_REDE.get(rede, ())
    bloco_postiz = cfg.get("postiz") or {}
    id_forcado = str(bloco_postiz.get(f"integracao_{rede}") or "").strip()

    for c in canais:
        if c.get("desativado"):
            continue
        if id_forcado and c["id"] == id_forcado:
            return c
    if id_forcado:
        return None

    for c in canais:
        if not c.get("desativado") and c["identifier"] in aceitos:
            return c
    return None


def conectar_postiz(
    api_key: str,
    cfg: dict[str, Any],
    diretorio_contas: Path | None = None,
    sessao_http: requests.Session | None = None,
    validar_online: bool = True,
) -> dict[str, Any]:
    """Valida a API key do Postiz e a grava criptografada com DPAPI fora do repositorio."""
    chave_limpa = (api_key or "").strip()
    if _eh_placeholder(chave_limpa) or len(chave_limpa) < 6:
        raise ValueError(
            "API Key do Postiz invalida ou vazia. Copie sua chave em Postiz -> Settings -> Public API "
            "e passe em: python -m cortelive.postar conectar postiz --chave SUA_CHAVE"
        )

    base_url = obter_base_url_postiz(cfg)
    canais: list[dict[str, Any]] = []
    if validar_online:
        canais = listar_integracoes_postiz(chave_limpa, base_url=base_url, sessao_http=sessao_http)

    dados_cofre = {
        "api_key": chave_limpa,
        "base_url": base_url,
        "canais_conhecidos": canais,
    }
    arq = cofre.salvar_token("postiz", dados_cofre, diretorio=diretorio_contas)
    return {
        "rede": "postiz",
        "via": "postiz",
        "conectado": True,
        "base_url": base_url,
        "arquivo_token": str(arq),
        "criptografado_dpapi": cofre.token_esta_criptografado_em_disco("postiz", diretorio=diretorio_contas),
        "canais": canais,
    }


def obter_credencial_postiz(
    cfg: dict[str, Any],
    diretorio_contas: Path | None = None,
) -> tuple[str, str]:
    """Carrega (api_key, base_url) do cofre DPAPI para uso nas chamadas do Postiz."""
    salvo = cofre.carregar_token("postiz", diretorio=diretorio_contas)
    if not salvo or not str(salvo.get("api_key") or "").strip():
        raise ErroEnvio(
            "Postiz nao esta conectado neste computador. "
            "Rode 'python -m cortelive.postar conectar postiz --chave SUA_API_KEY' primeiro."
        )
    api_key = str(salvo["api_key"]).strip()
    base_url = obter_base_url_postiz(cfg, dados_token=salvo)
    return api_key, base_url


def status_postiz(
    cfg: dict[str, Any],
    diretorio_contas: Path | None = None,
    sessao_http: requests.Session | None = None,
    consultar_online: bool = True,
) -> dict[str, Any]:
    """Retorna o status por rede + lista completa de canais conectados no Postiz."""
    saida: dict[str, Any] = {}
    try:
        salvo = cofre.carregar_token("postiz", diretorio=diretorio_contas)
    except Exception as exc:
        msg = f"Erro ao abrir chave Postiz no cofre DPAPI: {exc}"
        for rede in REDES_SUPORTADAS:
            saida[rede] = {
                "conectado": False,
                "valido": False,
                "conta": None,
                "expira_em": None,
                "expira_iso": None,
                "renovavel": False,
                "criptografado_dpapi": False,
                "via": "postiz",
                "detalhe": msg,
            }
        saida["postiz"] = {"conectado": False, "valido": False, "criptografado_dpapi": False, "canais": [], "detalhe": msg}
        return saida

    if not salvo or not str(salvo.get("api_key") or "").strip():
        for rede in REDES_SUPORTADAS:
            saida[rede] = {
                "conectado": False,
                "valido": False,
                "conta": None,
                "expira_em": None,
                "expira_iso": None,
                "renovavel": False,
                "criptografado_dpapi": False,
                "via": "postiz",
                "detalhe": "API key do Postiz nao configurada",
            }
        saida["postiz"] = {
            "conectado": False,
            "valido": False,
            "criptografado_dpapi": False,
            "canais": [],
            "detalhe": "Nao conectado (use conectar postiz --chave ...)",
        }
        return saida

    cripto_ok = cofre.token_esta_criptografado_em_disco("postiz", diretorio=diretorio_contas)
    base_url = obter_base_url_postiz(cfg, dados_token=salvo)
    api_key = str(salvo["api_key"]).strip()

    canais: list[dict[str, Any]] = list(salvo.get("canais_conhecidos") or [])
    valido_api = True
    detalhe_postiz = "API key salva"

    if consultar_online:
        try:
            canais = listar_integracoes_postiz(api_key, base_url=base_url, sessao_http=sessao_http)
            ativos = [c for c in canais if not c.get("desativado")]
            detalhe_postiz = f"{len(ativos)} canal(is) ativo(s) no Postiz"
        except Exception as exc:
            valido_api = False
            detalhe_postiz = f"Falha ao consultar canais no Postiz: {exc}"

    for rede in REDES_SUPORTADAS:
        canal = escolher_integracao_rede(rede, canais, cfg) if valido_api else None
        if canal:
            nome_conta = f"@{canal['perfil']}" if canal.get("perfil") and not str(canal["perfil"]).startswith("@") else canal["nome"]
            saida[rede] = {
                "conectado": True,
                "valido": True,
                "conta": nome_conta,
                "expira_em": None,
                "expira_iso": None,
                "renovavel": True,
                "criptografado_dpapi": cripto_ok,
                "via": "postiz",
                "integracao_id": canal["id"],
                "identificador": canal["identifier"],
                "detalhe": f"Canal conectado no Postiz ({canal['identifier']})",
            }
        else:
            saida[rede] = {
                "conectado": False,
                "valido": False,
                "conta": None,
                "expira_em": None,
                "expira_iso": None,
                "renovavel": False,
                "criptografado_dpapi": cripto_ok,
                "via": "postiz",
                "detalhe": (
                    detalhe_postiz
                    if not valido_api
                    else f"Nenhum canal de {rede.capitalize()} conectado na sua conta Postiz"
                ),
            }

    saida["postiz"] = {
        "conectado": True,
        "valido": valido_api,
        "base_url": base_url,
        "criptografado_dpapi": cripto_ok,
        "canais": canais,
        "detalhe": detalhe_postiz,
    }
    return saida


def upload_midia_postiz(
    caminho_video: Path,
    api_key: str,
    base_url: str,
    cfg: dict[str, Any],
    progresso: CallbackProgresso | None = None,
    sessao_http: requests.Session | None = None,
    dormir: Any = time.sleep,
) -> dict[str, str]:
    """Envia o .mp4 para `POST /upload` e aguarda `GET /media/{id}/status` se `adjust=true`."""
    if not caminho_video.is_file():
        raise ErroEnvio(f"Arquivo de video nao encontrado: {caminho_video}")
    if caminho_video.stat().st_size <= 0:
        raise ErroEnvio(f"Arquivo de video esta vazio: {caminho_video}")

    http = sessao_http or requests.Session()
    bloco = cfg.get("postiz") or {}
    ajustar = bool(bloco.get("ajustar_midia", False))

    if progresso:
        progresso("enviando", 35)

    url_up = f"{base_url.rstrip('/')}/upload"
    form_data = {"adjust": "true"} if ajustar else None
    try:
        with caminho_video.open("rb") as f:
            resp = http.post(
                url_up,
                headers=_cabecalhos_postiz(api_key),
                files={"file": (caminho_video.name, f, "video/mp4")},
                data=form_data,
                timeout=300,
            )
    except requests.RequestException as exc:
        raise ErroTemporario(f"Falha de rede no upload de midia para o Postiz: {exc}") from exc

    detalhe = _erro_postiz(resp)
    if resp.status_code == 403:
        raise ErroAutenticacao(f"Acesso negado no upload do Postiz (HTTP 403): {detalhe}")
    classificar_erro_http(resp, "Erro no upload de video para o Postiz", detalhe)

    dados = resp.json() if resp.content else {}
    midia_id = str(dados.get("id") or "").strip()
    midia_path = str(dados.get("path") or "").strip()
    estado = str(dados.get("status") or "ready").lower()
    if not midia_id or not midia_path:
        raise ErroEnvio("Postiz concluiu o upload, mas nao devolveu 'id' e 'path' da midia.")

    if estado == "processing":
        poll_s = float(bloco.get("poll_intervalo_s", 2.0))
        max_polls = max(1, int(bloco.get("poll_tentativas", 30)))
        url_status = f"{base_url.rstrip('/')}/media/{midia_id}/status"
        for tentativa in range(max_polls):
            if progresso:
                progresso("processando", min(70, 50 + int(((tentativa + 1) / max_polls) * 20)))
            try:
                r_st = http.get(url_status, headers=_cabecalhos_postiz(api_key), timeout=30)
            except requests.RequestException as exc:
                raise ErroTemporario(f"Falha de rede ao consultar midia no Postiz: {exc}") from exc
            classificar_erro_http(r_st, "Erro ao consultar status da midia no Postiz", _erro_postiz(r_st))
            d_st = r_st.json() if r_st.content else {}
            if str(d_st.get("status") or "ready").lower() == "ready":
                if d_st.get("path"):
                    midia_path = str(d_st["path"])
                break
            dormir(poll_s)
        else:
            raise ErroTemporario("Tempo esgotado aguardando o Postiz ajustar o video.")

    if progresso:
        progresso("enviando", 70)
    return {"id": midia_id, "path": midia_path}


def montar_item_post_postiz(
    rede: str,
    integracao: dict[str, Any],
    midia: dict[str, str],
    titulo: str,
    descricao: str,
    privado: bool,
    cfg: dict[str, Any],
) -> dict[str, Any]:
    """Monta um elemento de `posts[]` seguindo estritamente os schemas da doc oficial do Postiz."""
    ident = integracao["identifier"]
    bloco_postiz = cfg.get("postiz") or {}

    if rede == "youtube":
        bloco_yt = cfg.get("youtube") or {}
        priv_efetivo = bool(privado or bloco_yt.get("forcar_privado", False))
        titulo_yt = _preparar_titulo_shorts(titulo)
        if len(titulo_yt) < 2:
            titulo_yt = "Corte #Shorts"
        desc_yt = descricao.strip()
        if "#shorts" not in desc_yt.lower() and "#shorts" not in titulo_yt.lower():
            desc_yt = f"{desc_yt}\n\n#Shorts".strip()
        tags_yt = [{"value": t, "label": t} for t in _extrair_tags_yt(desc_yt)]
        settings: dict[str, Any] = {
            "__type": "youtube",
            "title": titulo_yt[:100],
            "type": "private" if priv_efetivo else "public",
            "selfDeclaredMadeForKids": "no",
            "tags": tags_yt,
        }
        conteudo = desc_yt[:5000]

    elif rede == "instagram":
        # Suporta tanto contas vinculadas ao Facebook ("instagram") quanto login direto ("instagram-standalone")
        tipo_ig = "instagram-standalone" if ident == "instagram-standalone" else "instagram"
        settings = {
            "__type": tipo_ig,
            "post_type": "post",
            "is_trial_reel": bool(bloco_postiz.get("instagram_trial_reel", False)),
            "collaborators": [],
        }
        conteudo = _montar_legenda_ig(titulo, descricao)

    elif rede == "tiktok":
        bloco_tk = cfg.get("tiktok") or {}
        priv_efetivo = bool(privado or bloco_tk.get("forcar_privado", False))
        titulo_curto = re.sub(r"\s+", " ", titulo).strip()[:90]
        settings = {
            "__type": "tiktok",
            "title": titulo_curto,
            "privacy_level": "SELF_ONLY" if priv_efetivo else "PUBLIC_TO_EVERYONE",
            "duet": bool(bloco_postiz.get("tiktok_duet", True)),
            "stitch": bool(bloco_postiz.get("tiktok_stitch", True)),
            "comment": bool(bloco_postiz.get("tiktok_comment", True)),
            "autoAddMusic": "no",
            "brand_content_toggle": False,
            "brand_organic_toggle": False,
            "video_made_with_ai": False,
            "content_posting_method": str(bloco_postiz.get("tiktok_metodo") or "DIRECT_POST"),
        }
        conteudo = _montar_legenda_tiktok(titulo, descricao)

    else:
        raise ErroEnvio(f"Rede nao suportada no adaptador Postiz: {rede}")

    return {
        "integration": {"id": integracao["id"]},
        "value": [
            {
                "content": conteudo,
                "image": [{"id": midia["id"], "path": midia["path"]}],
            }
        ],
        "settings": settings,
    }


def data_utc_postiz(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def timestamp_postiz(data):
    instante = datetime.fromisoformat(data)
    if instante.tzinfo is None:
        raise ValueError("A data do Postiz precisa incluir o fuso horário.")
    return instante.timestamp()


def proximo_horario_postiz(canal, cfg):
    chave, base = obter_credencial_postiz(cfg)
    url = f"{base.rstrip('/')}/find-slot/{urllib.parse.quote(canal, safe='')}"
    try:
        resp = requests.Session().get(url, headers=_cabecalhos_postiz(chave), timeout=10)
        classificar_erro_http(resp, "Erro ao consultar horário no Postiz")
        return timestamp_postiz(resp.json()["date"])
    except (ErroEnvio, requests.RequestException, KeyError, TypeError, ValueError):
        return None


def excluir_post_postiz(post_id, cfg):
    chave, base = obter_credencial_postiz(cfg)
    url = f"{base.rstrip('/')}/posts/{urllib.parse.quote(post_id, safe='')}"
    try:
        resp = requests.Session().delete(url, headers=_cabecalhos_postiz(chave), timeout=30)
    except requests.RequestException as exc:
        raise ErroTemporario("Falha de rede ao cancelar o agendamento no Postiz.") from exc
    if resp.status_code not in (200, 204, 404):
        classificar_erro_http(resp, "Erro ao cancelar o agendamento no Postiz", _erro_postiz(resp))
        raise ErroEnvio("O Postiz não confirmou o cancelamento.")


def confirmar_post_agendado(post_id, canal, data, cfg):
    chave, base = obter_credencial_postiz(cfg)
    horario = timestamp_postiz(data)
    try:
        resp = requests.Session().get(
            f"{base.rstrip('/')}/posts", headers=_cabecalhos_postiz(chave), timeout=30,
            params={"startDate": data_utc_postiz(horario - 60), "endDate": data_utc_postiz(horario + 60)},
        )
        classificar_erro_http(resp, "Erro ao localizar agendamento no Postiz")
        return any(post["id"] == post_id and post["state"] == "QUEUE"
                   and post["integration"]["id"] == canal
                   and timestamp_postiz(post["publishDate"]) == horario
                   for post in resp.json()["posts"])
    except (requests.RequestException, KeyError, TypeError, ValueError) as exc:
        raise ErroEnvio("O Postiz não confirmou a situação do agendamento.") from exc


class ErroAgendamentoIncerto(ErroEnvio):
    """A criação pode ter sido aceita, mesmo sem confirmação utilizável."""


def enviar_rede_postiz(
    rede: str,
    caminho_video: Path,
    titulo: str,
    descricao: str,
    privado: bool,
    cfg: dict[str, Any],
    progresso: CallbackProgresso,
    diretorio_contas: Path | None = None,
    sessao_http: requests.Session | None = None,
    dormir: Any = time.sleep,
    cache_execucao: dict[str, Any] | None = None,
    agendar=None,
    editar_post_id: str | None = None,
) -> tuple[str, str]:
    """Faz upload (reaproveitando no lote se disponivel) + POST /posts no Postiz e devolve (link, post_id)."""
    api_key, base_url = obter_credencial_postiz(cfg, diretorio_contas=diretorio_contas)
    http = sessao_http or requests.Session()
    progresso("iniciando", 15)

    cache = cache_execucao if cache_execucao is not None else {}
    if "canais" not in cache:
        cache["canais"] = listar_integracoes_postiz(api_key, base_url=base_url, sessao_http=http)
    canais: list[dict[str, Any]] = cache["canais"]

    integracao = escolher_integracao_rede(rede, canais, cfg)
    if not integracao:
        raise ErroEnvio(
            f"Nenhum canal ativo de {rede.capitalize()} encontrado na sua conta do Postiz. "
            f"Conecte o canal no painel do Postiz primeiro e confira com 'python -m cortelive.postar status --via postiz'."
        )

    chave_midia = f"midia:{caminho_video.resolve()}"
    if chave_midia not in cache:
        cache[chave_midia] = upload_midia_postiz(
            caminho_video=caminho_video,
            api_key=api_key,
            base_url=base_url,
            cfg=cfg,
            progresso=progresso,
            sessao_http=http,
            dormir=dormir,
        )
    else:
        progresso("enviando", 70)
    midia: dict[str, str] = cache[chave_midia]

    post_item = montar_item_post_postiz(
        rede=rede,
        integracao=integracao,
        midia=midia,
        titulo=titulo,
        descricao=descricao,
        privado=privado,
        cfg=cfg,
    )
    if editar_post_id:
        post_item["value"][0]["id"] = editar_post_id
    progresso("agendando" if agendar else "publicando", 85)
    corpo_post = {
        "type": "schedule" if agendar else "now",
        "date": agendar() if agendar else data_utc_postiz(time.time()),
        "shortLink": False,
        "tags": [],
        "posts": [post_item],
    }

    url_posts = f"{base_url.rstrip('/')}/posts"
    try:
        resp = http.post(
            url_posts,
            headers={**_cabecalhos_postiz(api_key), "Content-Type": "application/json"},
            json=corpo_post,
            timeout=60,
        )
    except requests.RequestException as exc:
        if agendar and not editar_post_id:
            raise ErroAgendamentoIncerto("O Postiz pode ter recebido o agendamento. Confira o calendário.") from exc
        raise ErroTemporario(f"Falha de rede ao criar publicacao no Postiz: {exc}") from exc

    if agendar and not editar_post_id and (resp.status_code >= 500 or 300 <= resp.status_code < 400):
        raise ErroAgendamentoIncerto("O Postiz pode ter recebido o agendamento. Confira o calendário.")
    detalhe = _erro_postiz(resp)
    if resp.status_code == 403:
        raise ErroAutenticacao(f"Acesso negado ao criar post no Postiz (HTTP 403): {detalhe}")
    classificar_erro_http(resp, f"Erro ao publicar {rede} via Postiz", detalhe)

    try:
        dados_resp = resp.json() if resp.content else []
    except ValueError as exc:
        if agendar and not editar_post_id:
            raise ErroAgendamentoIncerto("O Postiz respondeu sem confirmar o agendamento.") from exc
        raise
    primeiro = dados_resp[0] if isinstance(dados_resp, list) and dados_resp else (dados_resp if isinstance(dados_resp, dict) else {})
    primeiro = primeiro if isinstance(primeiro, dict) else {}
    post_id = str(primeiro.get("postId") or primeiro.get("id") or "").strip()
    if editar_post_id and post_id != editar_post_id:
        raise ErroEnvio("O Postiz não confirmou a edição do mesmo post. Confira o calendário.")
    if not post_id:
        if agendar:
            raise ErroAgendamentoIncerto("O Postiz respondeu sem confirmar o agendamento.")
        raise ErroEnvio("Postiz aceitou a requisicao, mas nao devolveu 'postId'.")

    # Se o Postiz devolver releaseURL publica direta, priorizamos; caso contrario, montamos URL
    # deterministica na instancia Postiz (pois no OpenAPI oficial POST /posts retorna [{postId, integration}]).
    link_direto = str(primeiro.get("releaseURL") or primeiro.get("url") or "").strip()
    if link_direto.startswith("https://") or link_direto.startswith("http://"):
        return link_direto, post_id

    web_base = _derivar_web_url_postiz(base_url, cfg)
    return f"{web_base}/launches?postId={urllib.parse.quote(post_id)}", post_id
