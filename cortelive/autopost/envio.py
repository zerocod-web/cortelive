"""Chama o conector de postagem (CLI `python -m cortelive.postar enviar ...`) e lê as linhas JSON.

Uma chamada por rede, porque cada rede tem o seu título/legenda. Se `cortelive.postar` ainda não
existe, usa o substituto `cortelive.autopost.stub_postar` (mesmo contrato, nunca posta de verdade).
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]      # pasta que contém o pacote cortelive


def modulo_postar() -> str:
    forcado = os.environ.get("CORTELIVE_POSTAR_MODULO")     # testes: força o stub
    if forcado:
        return forcado
    try:
        # só usa o conector oficial quando ele já roda com `python -m` (tem __main__)
        if importlib.util.find_spec("cortelive.postar.__main__") is not None:
            return "cortelive.postar"
    except (ImportError, ValueError):
        pass
    return "cortelive.autopost.stub_postar"


def textos_da_rede(meta: dict, rede: str) -> tuple[str, str]:
    """(--titulo, --descricao) para cada rede."""
    if rede == "youtube":
        return meta["youtube"]["titulo"], meta["youtube"]["descricao"]
    if rede == "tiktok":
        return meta["tiktok"]["titulo"], meta["tiktok"]["legenda"]
    if rede == "instagram":
        return meta["instagram"]["titulo"], meta["instagram"]["legenda"]
    raise ValueError(f"rede desconhecida: {rede}")


def enviar_postiz(video, rede, canal, meta, privado, cfg, cache, emitir, agendar):
    from ..postar.fila import enviar_uma_rede

    titulo, descricao = textos_da_rede(meta, rede)
    opcoes = {**cfg, "tentativas_max": 1,
              "postiz": {**cfg.get("postiz", {}), f"integracao_{rede}": canal}}
    return enviar_uma_rede(
        rede, Path(video), titulo, descricao, privado, False, opcoes, emitir,
        via="postiz", cache_postiz=cache, agendar=agendar,
    )


def enviar(video, rede: str, meta: dict, privado=False, dry_run=False, ao_progresso=None,
           timeout=1800, via: str | None = "postiz") -> dict:
    """-> {estado: postado|erro, link?, erro?, linhas: [...]}
    via: 'postiz' (padrão) ou 'direto' — repassado como --via para a CLI de postar."""
    titulo, descricao = textos_da_rede(meta, rede)
    mod = modulo_postar()
    cmd = [sys.executable, "-m", mod, "enviar", "--video", str(video), "--titulo", titulo,
           "--descricao", descricao, "--redes", rede]
    if via:
        cmd += ["--via", str(via)]
    if privado:
        cmd.append("--privado")
    if dry_run:
        cmd.append("--dry-run")
    linhas, link, erro = [], None, None
    try:
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}   # acentos certos no Windows
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=str(RAIZ), env=env,
                             text=True, encoding="utf-8", errors="replace")
        for ln in p.stdout:
            ln = ln.strip()
            if not ln:
                continue
            try:
                d = json.loads(ln)
            except json.JSONDecodeError:
                continue
            if d.get("rede") not in (None, rede):
                continue
            linhas.append(d)
            if ao_progresso:
                ao_progresso(d)
            link = d.get("link") or link
            erro = d.get("erro") or erro
        p.wait(timeout=timeout)
        err_txt = (p.stderr.read() or "").strip()
        if p.returncode != 0 and not erro:
            erro = f"conector saiu com código {p.returncode}: {err_txt[-300:]}"
    except Exception as e:
        erro = f"falha ao chamar o conector: {e}"
    if link and not erro:
        return {"estado": "postado", "link": link, "modulo": mod, "linhas": linhas}
    return {"estado": "erro", "erro": erro or "conector não devolveu link", "modulo": mod, "linhas": linhas}
