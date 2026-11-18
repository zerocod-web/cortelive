"""Adaptadores oficiais de upload para YouTube, Instagram e TikTok."""
from __future__ import annotations

from email.utils import parsedate_to_datetime
import time
from typing import Callable

import requests

CallbackProgresso = Callable[[str, int], None]


class ErroEnvio(RuntimeError):
    """Erro permanente de validacao ou negocio na API oficial."""


class ErroAutenticacao(ErroEnvio):
    """HTTP 401/403 por token invalido ou revogado (permite tentar refresh 1x)."""


class ErroTemporario(ErroEnvio):
    """Erro transitorio (rede, timeout, 429 rate-limit ou 5xx) elegivel a retry com backoff."""

    def __init__(self, mensagem: str, retry_after_s: float | None = None):
        super().__init__(mensagem)
        self.retry_after_s = retry_after_s


def extrair_retry_after(resp: requests.Response) -> float | None:
    bruto = resp.headers.get("Retry-After")
    if not bruto:
        return None
    try:
        return max(0.0, float(bruto))
    except ValueError:
        try:
            dt = parsedate_to_datetime(bruto)
            return max(0.0, dt.timestamp() - time.time())
        except Exception:
            return None


def classificar_erro_http(resp: requests.Response, contexto: str, detalhe_api: str = "") -> None:
    """Lanca a excecao adequada se resp nao for 2xx/308."""
    if resp.status_code in (200, 201, 202, 204, 308):
        return
    msg_base = f"{contexto} (HTTP {resp.status_code})"
    if detalhe_api:
        msg_base = f"{msg_base}: {detalhe_api}"
    if resp.status_code == 401:
        raise ErroAutenticacao(msg_base)
    if resp.status_code == 429 or resp.status_code >= 500:
        raise ErroTemporario(msg_base, retry_after_s=extrair_retry_after(resp))
    raise ErroEnvio(msg_base)
