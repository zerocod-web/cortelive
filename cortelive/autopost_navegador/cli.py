"""CLI: `python -m cortelive.autopost_navegador conectar|status|enviar` (mesmo contrato JSON de cortelive.postar)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import REDES, pasta_perfil
from .base import ErroPostagem, ParouAntesDePublicar, SemSessao, Sessao, abrir_para_login


def _modulo(rede: str):
    if rede == "youtube":
        from . import youtube as m
    elif rede == "instagram":
        from . import instagram as m
    else:
        from . import tiktok as m
    return m


def _p(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False), flush=True)


def checar(rede: str) -> dict:
    """Abre o perfil sem janela e vê se a rede está logada."""
    try:
        with Sessao(rede, emitir=lambda ev: None, visivel=False) as s:
            ok = _modulo(rede).logado(s.page)
            return {"rede": rede, "conectado": ok, "perfil": str(pasta_perfil(rede))}
    except ErroPostagem as e:
        return {"rede": rede, "conectado": False, "erro": str(e)}


def _cmd_conectar(a) -> int:
    rede = a.rede
    _p({"rede": rede, "etapa": "aguardando_login",
        "aviso": "entre na sua conta na janela que abriu e depois FECHE a janela"})
    abrir_para_login(rede, _modulo(rede).URL_LOGIN)
    res = checar(rede)
    _p(res)
    return 0 if res.get("conectado") else 1


def _cmd_status(a) -> int:
    alvos = REDES if a.rede == "todas" else (a.rede,)
    res = {r: checar(r) for r in alvos}
    if a.json:
        _p(res)
    else:
        for r, info in res.items():
            print(f"{r:10s}: {'CONECTADA' if info['conectado'] else 'não conectada'}"
                  f"{' — ' + info['erro'] if info.get('erro') else ''}", flush=True)
    return 0


def enviar_rede(rede: str, video: str, titulo: str, descricao: str, privado: bool, ate_antes: bool,
                visivel: bool = True) -> dict:
    """Posta em uma rede. Devolve o evento final (concluido/parado_antes_de_publicar/erro)."""
    try:
        with Sessao(rede, emitir=_p, visivel=visivel) as s:
            try:
                link = _modulo(rede).enviar(s, video, titulo, descricao, privado, ate_antes)
                ev = {"rede": rede, "etapa": "concluido", "pct": 100, "link": link,
                      "log": str(s.pasta_log)}
            except ParouAntesDePublicar as p:
                info = p.args[0] if p.args else {}
                ev = {"rede": rede, "etapa": "parado_antes_de_publicar", "pct": 95, **info,
                      "log": str(s.pasta_log)}
            except SemSessao as e:
                ev = {"rede": rede, "etapa": "erro", "pct": 0, "erro": str(e), "precisa": "conectar",
                      "log": str(s.pasta_log)}
            except Exception as e:     # noqa: BLE001 - tudo vira linha de erro legível
                s.print("erro")
                ev = {"rede": rede, "etapa": "erro", "pct": 0,
                      "erro": str(e).splitlines()[0][:300] if str(e) else type(e).__name__,
                      "log": str(s.pasta_log)}
    except ErroPostagem as e:
        ev = {"rede": rede, "etapa": "erro", "pct": 0, "erro": str(e)}
    _p(ev)
    return ev


def _reserva_ligada(a) -> bool:
    """Reserva opcional, DESLIGADA por padrão: o caminho principal é a API oficial."""
    import os
    return bool(getattr(a, "ligar_reserva", False)) or os.environ.get("CORTELIVE_NAVEGADOR") == "1"


def _cmd_enviar(a) -> int:
    redes = [r.strip().lower() for r in a.redes.split(",") if r.strip()]
    ruins = [r for r in redes if r not in REDES]
    if not redes or ruins:
        _p({"rede": ",".join(ruins), "etapa": "erro", "pct": 0, "erro": "rede inválida em --redes"})
        return 1
    video = Path(a.video)
    if not video.is_file():
        _p({"rede": ",".join(redes), "etapa": "erro", "pct": 0, "erro": f"vídeo não encontrado: {video}"})
        return 1
    if not a.dry_run and not _reserva_ligada(a):
        for rede in redes:
            _p({"rede": rede, "etapa": "erro", "pct": 0, "precisa": "ligar_reserva",
                "erro": "a postagem pelo navegador é só reserva e está DESLIGADA; use o caminho oficial "
                        "(ou ligue com --ligar-reserva / CORTELIVE_NAVEGADOR=1)"})
        return 1
    finais = []
    for rede in redes:
        if a.dry_run:
            for etapa, pct in (("abrindo", 2), ("enviando", 50), ("pronto_para_publicar", 90)):
                _p({"rede": rede, "etapa": etapa, "pct": pct, "dry_run": True})
            ev = {"rede": rede, "etapa": "concluido", "pct": 100, "dry_run": True,
                  "link": f"https://exemplo.invalid/{rede}/dry-run"}
            _p(ev)
        else:
            ev = enviar_rede(rede, str(video.resolve()), a.titulo, a.descricao, a.privado,
                             a.ate_antes_de_publicar, visivel=not a.sem_janela)
        finais.append(ev)
    return 1 if any(f["etapa"] == "erro" for f in finais) else 0


def construir_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m cortelive.autopost_navegador",
                                description="Posta cortes pelo site oficial de cada rede, com a sessão "
                                            "logada do próprio usuário.")
    sub = p.add_subparsers(dest="comando", required=True)

    c = sub.add_parser("conectar", help="Abre a janela da rede para o usuário entrar na conta uma vez.")
    c.add_argument("rede", choices=REDES)
    c.set_defaults(func=_cmd_conectar)

    st = sub.add_parser("status", help="Mostra quais redes estão logadas.")
    st.add_argument("rede", nargs="?", default="todas", choices=(*REDES, "todas"))
    st.add_argument("--json", action="store_true")
    st.set_defaults(func=_cmd_status)

    e = sub.add_parser("enviar", help="Envia e publica; linhas JSON {rede, etapa, pct, link|erro}.")
    e.add_argument("--video", required=True)
    e.add_argument("--titulo", default="")
    e.add_argument("--descricao", default="")
    e.add_argument("--redes", default="youtube,instagram,tiktok")
    g = e.add_mutually_exclusive_group()
    g.add_argument("--privado", action="store_true", help="YouTube privado, TikTok 'Só você'.")
    g.add_argument("--publico", action="store_true", help="(padrão) público.")
    e.add_argument("--dry-run", action="store_true", help="Não abre o navegador; só simula as linhas JSON.")
    e.add_argument("--ate-antes-de-publicar", action="store_true",
                   help="Faz tudo e PARA antes do clique final (teste).")
    e.add_argument("--sem-janela", action="store_true", help="Roda sem mostrar a janela (headless).")
    e.add_argument("--ligar-reserva", action="store_true",
                   help="Liga esta reserva (desligada por padrão; também CORTELIVE_NAVEGADOR=1).")
    e.add_argument("--via", default="navegador", help="Ignorado (compatível com cortelive.postar).")
    e.set_defaults(func=_cmd_enviar)
    return p


def main(argv=None) -> int:
    for st in (sys.stdout, sys.stderr):
        try:
            st.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    a = construir_parser().parse_args(argv)
    return int(a.func(a))
