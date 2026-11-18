"""Envio oficial para o YouTube Shorts via YouTube Data API v3 (videos.insert resumable)."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import requests

from . import CallbackProgresso, ErroEnvio, ErroTemporario, classificar_erro_http

YOUTUBE_UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"


def _preparar_titulo_shorts(titulo: str) -> str:
    limpo = re.sub(r"\s+", " ", titulo).strip() or "Corte da live"
    if "#shorts" not in limpo.lower():
        if len(limpo) <= 91:
            limpo = f"{limpo} #Shorts"
    return limpo[:100]


def _extrair_tags(descricao: str) -> list[str]:
    achadas = re.findall(r"#(\w{2,30})", descricao)
    vistas: list[str] = []
    for t in achadas:
        if t.lower() not in {x.lower() for x in vistas}:
            vistas.append(t)
    if "shorts" not in {x.lower() for x in vistas}:
        vistas.insert(0, "Shorts")
    return vistas[:15]


def enviar_youtube(
    caminho_video: Path,
    titulo: str,
    descricao: str,
    privado: bool,
    dados_token: dict[str, Any],
    cfg: dict[str, Any],
    progresso: CallbackProgresso,
    sessao_http: requests.Session | None = None,
) -> str:
    """Faz upload resumable de um video vertical como YouTube Short e devolve o link."""
    if not caminho_video.is_file():
        raise ErroEnvio(f"Arquivo de video nao encontrado: {caminho_video}")
    tamanho = caminho_video.stat().st_size
    if tamanho <= 0:
        raise ErroEnvio(f"Arquivo de video esta vazio: {caminho_video}")

    bloco = cfg.get("youtube") or {}
    privado_efetivo = bool(privado or bloco.get("forcar_privado", False))
    privacidade = "private" if privado_efetivo else "public"
    titulo_final = _preparar_titulo_shorts(titulo)
    desc_final = descricao.strip()
    if "#shorts" not in desc_final.lower() and "#shorts" not in titulo_final.lower():
        desc_final = f"{desc_final}\n\n#Shorts".strip()

    corpo_meta = {
        "snippet": {
            "title": titulo_final,
            "description": desc_final[:5000],
            "tags": _extrair_tags(desc_final),
            "categoryId": str(bloco.get("categoria_id") or "20"),
        },
        "status": {
            "privacyStatus": privacidade,
            "selfDeclaredMadeForKids": False,
        },
    }

    http = sessao_http or requests.Session()
    token = dados_token["access_token"]
    progresso("iniciando", 10)

    try:
        resp_init = http.post(
            YOUTUBE_UPLOAD_URL,
            params={"uploadType": "resumable", "part": "snippet,status"},
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=UTF-8",
                "X-Upload-Content-Length": str(tamanho),
                "X-Upload-Content-Type": "video/mp4",
            },
            json=corpo_meta,
            timeout=30,
        )
    except requests.RequestException as exc:
        raise ErroTemporario(f"Falha de rede ao iniciar upload no YouTube: {exc}") from exc

    detalhe = _mensagem_erro_google(resp_init)
    classificar_erro_http(resp_init, "Erro ao iniciar upload no YouTube", detalhe)

    upload_url = resp_init.headers.get("Location")
    if not upload_url:
        raise ErroEnvio("YouTube nao devolveu a URL de upload resumable (cabecalho Location ausente).")

    chunk_bytes = max(256 * 1024, int(cfg.get("chunk_mb", 8)) * 1024 * 1024)
    enviado = 0
    resp_final: requests.Response | None = None

    with caminho_video.open("rb") as f:
        while enviado < tamanho:
            pedaco = f.read(chunk_bytes)
            if not pedaco:
                break
            fim = enviado + len(pedaco) - 1
            pct = 15 + int(((fim + 1) / tamanho) * 75)
            progresso("enviando", min(90, pct))
            try:
                resp_chunk = http.put(
                    upload_url,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Length": str(len(pedaco)),
                        "Content-Type": "video/mp4",
                        "Content-Range": f"bytes {enviado}-{fim}/{tamanho}",
                    },
                    data=pedaco,
                    timeout=180,
                )
            except requests.RequestException as exc:
                raise ErroTemporario(f"Falha de rede durante envio do video ao YouTube: {exc}") from exc

            detalhe_chunk = _mensagem_erro_google(resp_chunk)
            classificar_erro_http(resp_chunk, "Erro ao enviar bytes do video ao YouTube", detalhe_chunk)
            enviado = fim + 1
            if resp_chunk.status_code in (200, 201):
                resp_final = resp_chunk
                break

    if resp_final is None:
        raise ErroEnvio("Upload no YouTube terminou sem resposta final do servidor.")

    progresso("processando", 95)
    dados = resp_final.json() if resp_final.content else {}
    video_id = dados.get("id")
    if not video_id:
        raise ErroEnvio("YouTube concluiu o upload, mas nao retornou o ID do video.")

    return f"https://youtube.com/shorts/{video_id}"


def _mensagem_erro_google(resp: requests.Response) -> str:
    try:
        dados = resp.json()
        err = dados.get("error")
        if isinstance(err, dict):
            return str(err.get("message") or "")
        if isinstance(err, str):
            return err
    except ValueError:
        pass
    return resp.text[:200].strip()
