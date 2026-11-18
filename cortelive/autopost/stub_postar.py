"""Substituto do `python -m cortelive.postar` enquanto o conector oficial não existe. MESMO contrato:

  python -m cortelive.autopost.stub_postar enviar --video ARQ --titulo T --descricao D
         --redes youtube,instagram,tiktok [--privado] [--dry-run]

Saída: linhas JSON {rede, etapa, pct, link|erro}. NUNCA posta: sem --dry-run, responde erro.
CORTELIVE_STUB_ATRASO_S (padrão 0.5) simula o tempo de upload por rede, para testar a fila.
"""
import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path


def _out(**k):
    print(json.dumps(k, ensure_ascii=False), flush=True)


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(prog="stub_postar")
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("enviar")
    e.add_argument("--video", required=True)
    e.add_argument("--titulo", required=True)
    e.add_argument("--descricao", default="")
    e.add_argument("--redes", required=True)
    e.add_argument("--privado", action="store_true")
    e.add_argument("--dry-run", action="store_true")
    e.add_argument("--via", choices=("postiz", "direto"), default="postiz")
    sub.add_parser("status")
    a = ap.parse_args(argv)
    if a.cmd == "status":
        for r in ("youtube", "instagram", "tiktok"):
            _out(rede=r, etapa="status", pct=0, erro="conector oficial ainda não instalado (stub)")
        return 0
    redes = [r.strip() for r in a.redes.split(",") if r.strip()]
    if not Path(a.video).exists():
        for r in redes:
            _out(rede=r, etapa="erro", pct=0, erro=f"vídeo não encontrado: {a.video}")
        return 2
    if not a.dry_run:
        for r in redes:
            _out(rede=r, etapa="erro", pct=0, erro="conector de postagem ainda não instalado (stub não posta)")
        return 2
    atraso = float(os.environ.get("CORTELIVE_STUB_ATRASO_S", "0.5"))
    sha = hashlib.sha256(Path(a.video).read_bytes()).hexdigest()[:10]
    for r in redes:
        for pct in (0, 50, 100):
            if pct:
                time.sleep(atraso / 2)
            _out(rede=r, etapa="enviando" if pct < 100 else "pronto", pct=pct,
                 **({"link": f"dryrun://{a.via}/{r}/{sha}"} if pct == 100 else {}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
