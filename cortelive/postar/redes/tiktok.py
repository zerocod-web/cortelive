"""Envio oficial para o TikTok via Content Posting API v2 (init + FILE_UPLOAD + status/fetch)."""
from __future__ import annotations

from pathlib import Path
import time
from typing import Any

import requests

from . import CallbackProgresso, ErroEnvio, ErroTemporario, classificar_erro_http

TIKTOK_INIT_URL = "https://open.tiktokapis.com/v2/post/publish/video/init/"
TIKTOK_STATUS_URL = "https://open.tiktokapis.com/v2/post/publish/status/fetch/"

MIN_CHUNK_TIKTOK = 5 * 1024 * 1024
PADRAO_CHUNK_TIKTOK = 10 * 1024 * 1024
MAX_CHUNK_UNICO_TIKTOK = 64 * 1024 * 1024


def calcular_chunks_tiktok(tamanho_bytes: int) -> tuple[int, int]:
    """Calcula (chunk_size, total_chunk_count) seguindo a regra oficial do TikTok Content Posting API."""
    if tamanho_bytes <= 0:
        raise ValueError("Tamanho de video invalido.")
    if tamanho_bytes <= MAX_CHUNK_UNICO_TIKTOK:
        return tamanho_bytes, 1
    chunk_size = PADRAO_CHUNK_TIKTOK
    total_chunks = tamanho_bytes // chunk_size
    # Na especificacao do TikTok, eventual sobra entra no ultimo chunk (ate 128 MB)
    return chunk_size, max(1, total_chunks)


def enviar_tiktok(
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
    """Publica um video vertical no TikTok usando a Content Posting API oficial."""
    if not caminho_video.is_file():
        raise ErroEnvio(f"Arquivo de video nao encontrado: {caminho_video}")
    tamanho = caminho_video.stat().st_size
    if tamanho <= 0:
        raise ErroEnvio(f"Arquivo de video esta vazio: {caminho_video}")

    bloco = cfg.get("tiktok") or {}
    privado_efetivo = bool(privado or bloco.get("forcar_privado", False))
    nivel_privacidade = "SELF_ONLY" if privado_efetivo else "PUBLIC_TO_EVERYONE"
    legenda = _montar_legenda_tiktok(titulo, descricao)
    chunk_size, total_chunks = calcular_chunks_tiktok(tamanho)

    token = dados_token["access_token"]
    http = sessao_http or requests.Session()
    progresso("iniciando", 10)

    corpo_init = {
        "post_info": {
            "title": legenda,
            "privacy_level": nivel_privacidade,
            "disable_duet": False,
            "disable_comment": False,
            "disable_stitch": False,
        },
        "source_info": {
            "source": "FILE_UPLOAD",
            "video_size": tamanho,
            "chunk_size": chunk_size,
            "total_chunk_count": total_chunks,
        },
    }

    try:
        resp_init = http.post(
            TIKTOK_INIT_URL,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=UTF-8",
            },
            json=corpo_init,
            timeout=30,
        )
    except requests.RequestException as exc:
        raise ErroTemporario(f"Falha de rede ao iniciar envio no TikTok: {exc}") from exc

    detalhe_init = _erro_tiktok(resp_init)
    classificar_erro_http(resp_init, "Erro ao inicializar postagem no TikTok", detalhe_init)

    payload_init = resp_init.json()
    cod_erro = ((payload_init.get("error") or {}).get("code") or "ok").lower()
    if cod_erro != "ok":
        msg = (payload_init.get("error") or {}).get("message") or cod_erro
        if "rate_limit" in cod_erro or "spam_risk" in cod_erro:
            raise ErroTemporario(f"Limite do TikTok atingido ({msg}).")
        if "unaudited" in cod_erro or "privacy" in cod_erro:
            raise ErroEnvio(
                f"TikTok recusou a privacidade escolhida ({msg}). "
                "Apps ainda nao auditados pelo TikTok exigem postagem privada (--privado)."
            )
        raise ErroEnvio(f"TikTok recusou iniciar o envio ({cod_erro}: {msg}).")

    dados_init = payload_init.get("data") or {}
    publish_id = str(dados_init.get("publish_id") or "")
    upload_url = str(dados_init.get("upload_url") or "")
    if not publish_id or not upload_url:
        raise ErroEnvio("TikTok nao devolveu publish_id ou upload_url na inicializacao.")

    with caminho_video.open("rb") as f:
        for indice in range(total_chunks):
            inicio = indice * chunk_size
            qtd = (tamanho - inicio) if indice == total_chunks - 1 else chunk_size
            pedaco = f.read(qtd)
            fim = inicio + len(pedaco) - 1
            pct = 20 + int(((fim + 1) / tamanho) * 55)
            progresso("enviando", min(75, pct))
            try:
                resp_put = http.put(
                    upload_url,
                    headers={
                        "Content-Type": "video/mp4",
                        "Content-Length": str(len(pedaco)),
                        "Content-Range": f"bytes {inicio}-{fim}/{tamanho}",
                    },
                    data=pedaco,
                    timeout=180,
                )
            except requests.RequestException as exc:
                raise ErroTemporario(f"Falha de rede no upload do arquivo para o TikTok: {exc}") from exc

            classificar_erro_http(resp_put, "Erro ao enviar bytes do video ao TikTok", _erro_tiktok(resp_put))

    # Consulta status de publicacao assincrona
    poll_s = float(bloco.get("poll_intervalo_s", 3.0))
    max_polls = max(1, int(bloco.get("poll_tentativas", 40)))
    post_id_publico = ""
    for tentativa in range(max_polls):
        pct_proc = min(95, 80 + int(((tentativa + 1) / max_polls) * 15))
        progresso("processando", pct_proc)
        try:
            resp_st = http.post(
                TIKTOK_STATUS_URL,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json; charset=UTF-8",
                },
                json={"publish_id": publish_id},
                timeout=30,
            )
        except requests.RequestException as exc:
            raise ErroTemporario(f"Falha de rede ao consultar status no TikTok: {exc}") from exc

        classificar_erro_http(resp_st, "Erro ao consultar status no TikTok", _erro_tiktok(resp_st))
        corpo_st = resp_st.json()
        dados_st = corpo_st.get("data") or {}
        estado = str(dados_st.get("status") or "").upper()
        ids_pub = dados_st.get("publicaly_available_post_id") or dados_st.get("publicly_available_post_id") or []
        if ids_pub:
            post_id_publico = str(ids_pub[0])

        if estado in ("PUBLISH_COMPLETE", "SEND_TO_USER_INBOX"):
            break
        if estado == "FAILED":
            motivo = dados_st.get("fail_reason") or "motivo nao informado"
            raise ErroEnvio(f"Publicacao falhou no processamento do TikTok ({motivo}).")
        dormir(poll_s)
    else:
        raise ErroTemporario("Tempo esgotado aguardando o TikTok finalizar o processamento do video.")

    conta_crua = str(dados_token.get("conta") or "").strip()
    usuario = conta_crua.lstrip("@") if conta_crua.startswith("@") else ""
    if usuario and post_id_publico:
        return f"https://www.tiktok.com/@{usuario}/video/{post_id_publico}"
    if post_id_publico:
        return f"https://www.tiktok.com/video/{post_id_publico}"
    if usuario:
        return f"https://www.tiktok.com/@{usuario}"
    return f"https://www.tiktok.com/tiktokstudio/content?publish_id={publish_id}"


def _montar_legenda_tiktok(titulo: str, descricao: str) -> str:
    t = titulo.strip()
    # Pega hashtags da descricao para enriquecer o title do TikTok sem estourar 2200 chars
    tags = " ".join(p for p in descricao.split() if p.startswith("#"))
    texto = f"{t} {tags}".strip() if tags and tags.lower() not in t.lower() else (t or descricao.strip() or "Corte da live")
    return texto[:2200]


def _erro_tiktok(resp: requests.Response) -> str:
    try:
        dados = resp.json()
        err = dados.get("error") or {}
        if isinstance(err, dict):
            cod = err.get("code") or ""
            msg = err.get("message") or ""
            if cod and cod != "ok":
                return f"{cod}: {msg}".strip(": ")
    except ValueError:
        pass
    return resp.text[:200].strip()
