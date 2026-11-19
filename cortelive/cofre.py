"""Cofre local com DPAPI do Windows (usado pela conexão GPT, arquivo contas/conexao-gpt.bin)."""
from __future__ import annotations

import base64
import ctypes
import sys
from ctypes import wintypes
from typing import Any

CABECALHO_DPAPI = b"CLDPAPI1:"
CABECALHO_FALLBACK = b"CLB64FB1:"
ENTROPIA_DPAPI = b"CorteLive.Contas.OAuth.v1"
CRYPTPROTECT_UI_FORBIDDEN = 0x01


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_char)),
    ]


def dpapi_disponivel() -> bool:
    return sys.platform == "win32" and hasattr(ctypes, "windll")


def _criar_blob(dados: bytes) -> tuple[_DataBlob, Any]:
    # A DPAPI exige um ponteiro válido mesmo com comprimento zero.
    buf = ctypes.create_string_buffer(dados, max(1, len(dados)))
    blob = _DataBlob(len(dados), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    return blob, buf


def criptografar_bytes(dados: bytes) -> bytes:
    """Protege bytes com Windows DPAPI (vinculado ao usuario atual do Windows)."""
    if not dpapi_disponivel():
        # Fallback apenas para SO nao-Windows (ex.: CI Linux)
        return CABECALHO_FALLBACK + base64.b64encode(dados)

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32

    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        wintypes.LPCWSTR,
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p

    blob_in, _ref_in = _criar_blob(dados)
    blob_ent, _ref_ent = _criar_blob(ENTROPIA_DPAPI)
    blob_out = _DataBlob()

    ok = crypt32.CryptProtectData(
        ctypes.byref(blob_in),
        "CorteLive OAuth Token",
        ctypes.byref(blob_ent),
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(blob_out),
    )
    if not ok:
        erro = ctypes.GetLastError()
        raise OSError(f"Falha ao proteger token com DPAPI do Windows (codigo {erro}).")

    try:
        cifrado = ctypes.string_at(blob_out.pbData, blob_out.cbData)
        return CABECALHO_DPAPI + cifrado
    finally:
        if blob_out.pbData:
            kernel32.LocalFree(ctypes.cast(blob_out.pbData, ctypes.c_void_p))


def descriptografar_bytes(pacote: bytes) -> bytes:
    """Reverte a protecao DPAPI do Windows."""
    if pacote.startswith(CABECALHO_DPAPI):
        if not dpapi_disponivel():
            raise OSError("Token protegido com DPAPI do Windows nao pode ser aberto fora do Windows.")
        cifrado = pacote[len(CABECALHO_DPAPI):]
        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32

        crypt32.CryptUnprotectData.argtypes = [
            ctypes.POINTER(_DataBlob),
            ctypes.POINTER(wintypes.LPWSTR),
            ctypes.POINTER(_DataBlob),
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(_DataBlob),
        ]
        crypt32.CryptUnprotectData.restype = wintypes.BOOL
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p

        blob_in, _ref_in = _criar_blob(cifrado)
        blob_ent, _ref_ent = _criar_blob(ENTROPIA_DPAPI)
        blob_out = _DataBlob()
        descr = wintypes.LPWSTR()

        ok = crypt32.CryptUnprotectData(
            ctypes.byref(blob_in),
            ctypes.byref(descr),
            ctypes.byref(blob_ent),
            None,
            None,
            CRYPTPROTECT_UI_FORBIDDEN,
            ctypes.byref(blob_out),
        )
        if not ok:
            erro = ctypes.GetLastError()
            raise OSError(
                f"Nao foi possivel ler o token salvo (DPAPI codigo {erro}). "
                "Reconecte a conta neste usuario do Windows."
            )
        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            if descr:
                kernel32.LocalFree(ctypes.cast(descr, ctypes.c_void_p))
            if blob_out.pbData:
                kernel32.LocalFree(ctypes.cast(blob_out.pbData, ctypes.c_void_p))

    if pacote.startswith(CABECALHO_FALLBACK):
        return base64.b64decode(pacote[len(CABECALHO_FALLBACK):])

    raise ValueError("Formato de arquivo de conta desconhecido ou corrompido.")
