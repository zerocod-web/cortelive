"""Envio oficial para Instagram Reels via Instagram Graph API (Resumable Upload + Publish).

Como a Graph API resolve arquivos locais sem precisar de servidor publico:
  1. Cria um container de midia em graph.facebook.com/{versao}/{ig_user_id}/media com
     media_type=REELS e upload_type=resumable -> devolve {id, uri} em rupload.facebook.com.
  2. Envia os bytes do .mp4 local via POST para rupload.facebook.com/ig-api-upload/...
     com cabecalhos Authorization: OAuth <token>, offset e file_size.
  3. Consulta GET /{container_id}?fields=status_code,status ate status_code == FINISHED.
  4. Publica com POST /{ig_user_id}/media_publish (creation_id=container_id) e consulta permalink.
Tambem aceita URL HTTPS direta caso seja fornecida em configuracao avancada (video_url).
"""
from __future__ import annotations

from pathlib import Path
import time
from typing import Any

import requests

from . import CallbackProgresso, ErroEnvio, ErroTemporario, classificar_erro_http


def enviar_instagram(
    caminho_video: Path,
    titulo: str,
    descricao: str,
    privado: bool,
    dados_token: dict[str, Any],
    cfg: dict[str, Any],
    progresso: CallbackProgresso,
    sessao_http: requests.Session | None = None,
    dormir: Any = time.sleep,
) -> str:
    """Envia um Reel para conta Instagram Profissional usando o protocolo oficial Resumable."""
    if not caminho_video.is_file():
        raise ErroEnvio(f"Arquivo de video nao encontrado: {caminho_video}")
    tamanho = caminho_video.stat().st_size
    if tamanho <= 0:
        raise ErroEnvio(f"Arquivo de video esta vazio: {caminho_video}")

    bloco = cfg.get("instagram") or {}
    versao = str(dados_token.get("versao_api") or bloco.get("versao_api") or "v21.0")
    ig_user_id = str(dados_token.get("ig_user_id") or bloco.get("ig_user_id") or "").strip()
    if not ig_user_id:
        raise ErroEnvio(
            "Conta Instagram Profissional sem ig_user_id salvo. "
            "Reconecte com 'python -m cortelive.postar conectar instagram'."
        )

    token = dados_token["access_token"]
    http = sessao_http or requests.Session()
    legenda = _montar_legenda(titulo, descricao)
    # Na Graph API nao existe Reel 100% privado; quando --privado e pedido, ocultamos do feed principal
    compartilhar_feed = "false" if privado else "true"

    progresso("iniciando", 10)
    base_graph = f"https://graph.facebook.com/{versao}"
    video_url_externa = str(bloco.get("video_url_override") or "").strip()

    params_container: dict[str, str] = {
        "media_type": "REELS",
        "caption": legenda,
        "share_to_feed": compartilhar_feed,
    }
    if video_url_externa.startswith("https://"):
        params_container["video_url"] = video_url_externa
    else:
        params_container["upload_type"] = "resumable"

    try:
        resp_init = http.post(
            f"{base_graph}/{ig_user_id}/media",
            headers={"Authorization": f"Bearer {token}"},
            data=params_container,
            timeout=30,
        )
    except requests.RequestException as exc:
        raise ErroTemporario(f"Falha de rede ao criar container de Reel no Instagram: {exc}") from exc

    classificar_erro_http(resp_init, "Erro ao criar container no Instagram", _erro_meta(resp_init))
    dados_init = resp_init.json()
    container_id = str(dados_init.get("id") or "")
    if not container_id:
        raise ErroEnvio("Instagram Graph API nao devolveu o ID do container de midia.")

    if not video_url_externa.startswith("https://"):
        upload_uri = str(
            dados_init.get("uri")
            or f"https://rupload.facebook.com/ig-api-upload/{versao}/{container_id}"
        )
        chunk_bytes = max(1024 * 1024, int(cfg.get("chunk_mb", 8)) * 1024 * 1024)
        offset = 0
        with caminho_video.open("rb") as f:
            while offset < tamanho:
                pedaco = f.read(chunk_bytes)
                if not pedaco:
                    break
                pct = 20 + int(((offset + len(pedaco)) / tamanho) * 45)
                progresso("enviando", min(65, pct))
                try:
                    resp_up = http.post(
                        upload_uri,
                        headers={
                            "Authorization": f"OAuth {token}",
                            "offset": str(offset),
                            "file_size": str(tamanho),
                            "Content-Type": "video/mp4",
                        },
                        data=pedaco,
                        timeout=180,
                    )
                except requests.RequestException as exc:
                    raise ErroTemporario(f"Falha de rede no upload resumable do Instagram: {exc}") from exc

                classificar_erro_http(resp_up, "Erro no upload binario para rupload.facebook.com", _erro_meta(resp_up))
                offset += len(pedaco)

    # Aguarda processamento assincrono do container na Meta
    poll_s = float(bloco.get("poll_intervalo_s", 3.0))
    max_polls = max(1, int(bloco.get("poll_tentativas", 40)))
    status_code = "IN_PROGRESS"
    for tentativa in range(max_polls):
        pct_proc = min(88, 70 + int(((tentativa + 1) / max_polls) * 18))
        progresso("processando", pct_proc)
        try:
            resp_st = http.get(
                f"{base_graph}/{container_id}",
                params={"fields": "status_code,status"},
                headers={"Authorization": f"Bearer {token}"},
                timeout=30,
            )
        except requests.RequestException as exc:
            raise ErroTemporario(f"Falha de rede ao consultar processamento no Instagram: {exc}") from exc

        classificar_erro_http(resp_st, "Erro ao consultar container no Instagram", _erro_meta(resp_st))
        info_st = resp_st.json()
        status_code = str(info_st.get("status_code") or "FINISHED").upper()
        if status_code in ("FINISHED", "PUBLISHED"):
            break
        if status_code in ("ERROR", "EXPIRED"):
            motivo = info_st.get("status") or status_code
            raise ErroEnvio(f"Instagram recusou o processamento do Reel ({motivo}).")
        dormir(poll_s)
    else:
        raise ErroTemporario("Tempo esgotado aguardando o Instagram processar o video.")

    progresso("publicando", 92)
    try:
        resp_pub = http.post(
            f"{base_graph}/{ig_user_id}/media_publish",
            headers={"Authorization": f"Bearer {token}"},
            data={"creation_id": container_id},
            timeout=45,
        )
    except requests.RequestException as exc:
        raise ErroTemporario(f"Falha de rede ao publicar Reel no Instagram: {exc}") from exc

    classificar_erro_http(resp_pub, "Erro ao publicar Reel no Instagram", _erro_meta(resp_pub))
    media_id = str(resp_pub.json().get("id") or "")
    if not media_id:
        raise ErroEnvio("Instagram nao retornou o ID da publicacao final.")

    # Busca o permalink oficial do Reel
    try:
        resp_link = http.get(
            f"{base_graph}/{media_id}",
            params={"fields": "permalink,shortcode"},
            headers={"Authorization": f"Bearer {token}"},
            timeout=20,
        )
        if resp_link.ok:
            dados_link = resp_link.json()
            if dados_link.get("permalink"):
                return str(dados_link["permalink"])
            if dados_link.get("shortcode"):
                return f"https://www.instagram.com/reel/{dados_link['shortcode']}/"
    except requests.RequestException:
        pass

    return f"https://www.instagram.com/reel/{media_id}/"


def _montar_legenda(titulo: str, descricao: str) -> str:
    t = titulo.strip()
    d = descricao.strip()
    if t and d and not d.lower().startswith(t.lower()):
        combinado = f"{t}\n\n{d}"
    else:
        combinado = d or t or "Corte da live"
    return combinado[:2200]


def _erro_meta(resp: requests.Response) -> str:
    try:
        dados = resp.json()
        err = dados.get("error") or {}
        if isinstance(err, dict):
            msg = err.get("error_user_msg") or err.get("message") or ""
            return str(msg)
    except ValueError:
        pass
    return resp.text[:200].strip()
