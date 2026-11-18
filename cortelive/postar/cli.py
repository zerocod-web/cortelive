"""CLI oficial do modulo de postagem: `python -m cortelive.postar`."""
from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from pathlib import Path
from typing import Any

from . import cofre
from .config import (
    CHAVES_COFRE_VALIDAS,
    PROVEDORES_VIA,
    REDES_SUPORTADAS,
    carregar_config_postar,
    resolver_provedor_via,
)
from .fila import FilaPostagem, enviar_para_redes
from .oauth import ErroOAuth, conectar_rede, status_contas
from .redes import ErroEnvio
from .redes.postiz import conectar_postiz, status_postiz
from .sugestao import sugerir_metadados


def _configurar_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _imprimir_json(dados: Any, compacto: bool = False) -> None:
    if compacto:
        print(json.dumps(dados, ensure_ascii=False), flush=True)
    else:
        print(json.dumps(dados, ensure_ascii=False, indent=2), flush=True)


def _ler_chave_postiz_interativa(args: argparse.Namespace) -> str:
    chave = (args.chave or os.environ.get("CORTELIVE_POSTIZ_API_KEY") or "").strip()
    if chave:
        return chave
    if not sys.stdin.isatty():
        return sys.stdin.readline().strip()
    return getpass.getpass("Cole sua API Key do Postiz (nao aparece na tela): ").strip()


def _cmd_conectar(args: argparse.Namespace) -> int:
    try:
        cfg = carregar_config_postar(args.config)
        if args.rede == "postiz":
            chave = _ler_chave_postiz_interativa(args)
            res = conectar_postiz(
                api_key=chave,
                cfg=cfg,
                validar_online=not args.sem_validar,
            )
            _imprimir_json(res, compacto=args.json)
            return 0

        res = conectar_rede(
            rede=args.rede,
            cfg=cfg,
            abrir_navegador=not args.sem_abrir,
            porta_override=args.porta,
            ao_url_gerada=lambda url: print(
                json.dumps({"rede": args.rede, "etapa": "aguardando_login", "url": url}, ensure_ascii=False),
                file=sys.stderr if args.json else sys.stdout,
                flush=True,
            ),
        )
        _imprimir_json(res, compacto=args.json)
        return 0
    except (ErroOAuth, ErroEnvio, ValueError, OSError) as exc:
        erro = {"rede": args.rede, "conectado": False, "erro": str(exc)}
        _imprimir_json(erro, compacto=args.json)
        return 1


def _cmd_desconectar(args: argparse.Namespace) -> int:
    alvos = list(CHAVES_COFRE_VALIDAS) if args.rede == "todas" else [args.rede]
    removidas = {r: cofre.remover_token(r) for r in alvos}
    _imprimir_json({"removidas": removidas})
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    cfg = carregar_config_postar(args.config)
    provedor = resolver_provedor_via(args.via, cfg)
    if provedor == "postiz":
        st: dict[str, Any] = status_postiz(cfg=cfg, consultar_online=not args.sem_refresh)
    else:
        st = status_contas(cfg=cfg, renovar_se_expirado=not args.sem_refresh)

    if args.texto:
        for rede in REDES_SUPORTADAS:
            info = st.get(rede) or {}
            estado = "CONECTADA (valido)" if info.get("valido") else (
                "CONECTADA (expirado/invalido)" if info.get("conectado") else "nao conectada"
            )
            conta = f" [{info['conta']}]" if info.get("conta") else ""
            print(f"{rede:10s}: {estado}{conta} — {info.get('detalhe', '')}", flush=True)
        if "postiz" in st:
            pz = st["postiz"]
            print(f"postiz    : {pz.get('detalhe', '')} ({len(pz.get('canais') or [])} canais listados)", flush=True)
        return 0

    _imprimir_json(st, compacto=args.json)
    return 0


def _cmd_sugerir(args: argparse.Namespace) -> int:
    arq = Path(args.video)
    if not arq.is_file():
        _imprimir_json({"erro": f"Arquivo nao encontrado: {arq}"}, compacto=True)
        return 1
    dados = sugerir_metadados(arq)
    _imprimir_json(dados, compacto=args.json)
    return 0


def _cmd_enviar(args: argparse.Namespace) -> int:
    cfg = carregar_config_postar(args.config)
    provedor = resolver_provedor_via(args.via, cfg)
    redes = [r.strip().lower() for r in args.redes.split(",") if r.strip()]
    if not redes:
        print(
            json.dumps({"rede": "", "etapa": "erro", "pct": 0, "erro": "Nenhuma rede informada em --redes."}, ensure_ascii=False),
            flush=True,
        )
        return 1

    privado = True if args.privado else (False if args.publico else None)

    if args.enfileirar:
        fila = FilaPostagem()
        priv_bool = bool(cfg.get("privado_padrao", False)) if privado is None else privado
        itens = fila.enfileirar(
            caminho_video=args.video,
            redes=redes,
            titulo=args.titulo,
            descricao=args.descricao,
            privado=priv_bool,
            dry_run=args.dry_run,
            via=provedor,
        )
        for item in itens:
            print(
                json.dumps(
                    {"rede": item["rede"], "etapa": "enfileirado", "pct": 0, "id_fila": item["id"], "via": provedor},
                    ensure_ascii=False,
                ),
                flush=True,
            )
        return 0

    def _emitir_linha(ev: dict[str, Any]) -> None:
        print(json.dumps(ev, ensure_ascii=False), flush=True)

    finais = enviar_para_redes(
        caminho_video=args.video,
        redes=redes,
        titulo=args.titulo,
        descricao=args.descricao,
        privado=privado,
        dry_run=args.dry_run,
        via=provedor,
        cfg=cfg,
        emitir=_emitir_linha,
    )
    teve_erro = any(f.get("etapa") == "erro" or "erro" in f for f in finais)
    return 1 if teve_erro else 0


def _cmd_fila(args: argparse.Namespace) -> int:
    cfg = carregar_config_postar(args.config)
    fila = FilaPostagem()
    if args.acao == "listar":
        _imprimir_json(fila.listar(), compacto=args.json)
        return 0
    if args.acao == "limpar":
        n = fila.limpar_concluidos()
        _imprimir_json({"removidos": n}, compacto=True)
        return 0

    def _emitir_linha(ev: dict[str, Any]) -> None:
        print(json.dumps(ev, ensure_ascii=False), flush=True)

    proc = fila.processar_pendentes(cfg=cfg, emitir=_emitir_linha)
    return 1 if any(i.get("estado") == "erro" for i in proc) else 0


def construir_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m cortelive.postar",
        description="Postagem oficial de cortes no YouTube Shorts, Instagram Reels e TikTok (direto ou via Postiz).",
    )
    p.add_argument("--config", help="Caminho opcional de um arquivo .toml de configuracao.")
    sub = p.add_subparsers(dest="comando", required=True)

    # conectar
    p_con = sub.add_parser("conectar", help="Conecta uma rede via OAuth 2.0 no navegador ou salva a API key do Postiz.")
    p_con.add_argument("rede", choices=CHAVES_COFRE_VALIDAS, help="Rede social (youtube, instagram, tiktok) ou postiz.")
    p_con.add_argument("--porta", type=int, default=None, help="Porta loopback local opcional (OAuth direto).")
    p_con.add_argument("--sem-abrir", action="store_true", help="Apenas exibe a URL sem abrir o navegador.")
    p_con.add_argument("--chave", "--api-key", dest="chave", default="", help="API key do Postiz (salva com DPAPI).")
    p_con.add_argument("--sem-validar", action="store_true", help="Salva a chave do Postiz sem consultar a rede agora.")
    p_con.add_argument("--json", action="store_true", help="Saida JSON compacta numa linha.")
    p_con.set_defaults(func=_cmd_conectar)

    # desconectar
    p_desc = sub.add_parser("desconectar", help="Remove o token ou API key salvo localmente no cofre DPAPI.")
    p_desc.add_argument("rede", choices=(*CHAVES_COFRE_VALIDAS, "todas"))
    p_desc.set_defaults(func=_cmd_desconectar)

    # status
    p_st = sub.add_parser("status", help="Mostra quais contas/canais estao conectados e se o token vale.")
    p_st.add_argument("--via", choices=PROVEDORES_VIA, default=None, help="Provedor: 'direto' (padrao) ou 'postiz'.")
    p_st.add_argument("--json", action="store_true", help="Saida JSON compacta numa linha.")
    p_st.add_argument("--texto", action="store_true", help="Saida resumida em texto simples.")
    p_st.add_argument("--sem-refresh", action="store_true", help="Nao consulta/renova online agora.")
    p_st.set_defaults(func=_cmd_status)

    # enviar
    p_env = sub.add_parser("enviar", help="Envia um video vertical emitindo linhas JSON de progresso.")
    p_env.add_argument("--video", required=True, help="Caminho do arquivo .mp4 do corte.")
    p_env.add_argument("--titulo", default="", help="Titulo do corte (se vazio, sugere pela transcricao).")
    p_env.add_argument("--descricao", default="", help="Descricao/hashtags (se vazia, sugere pela transcricao).")
    p_env.add_argument(
        "--redes",
        default="youtube,instagram,tiktok",
        help="Lista separada por virgula: youtube,instagram,tiktok.",
    )
    p_env.add_argument("--via", choices=PROVEDORES_VIA, default=None, help="Provedor: 'direto' (padrao) ou 'postiz'.")
    grp_priv = p_env.add_mutually_exclusive_group()
    grp_priv.add_argument("--privado", action="store_true", help="Publica como privado (quando suportado pela rede).")
    grp_priv.add_argument("--publico", action="store_true", help="Forca publicacao publica.")
    p_env.add_argument("--dry-run", action="store_true", help="Simula todo o fluxo e JSON sem enviar nada de verdade.")
    p_env.add_argument("--enfileirar", action="store_true", help="Apenas adiciona na fila persistente para envio posterior.")
    p_env.set_defaults(func=_cmd_enviar)

    # sugerir
    p_sug = sub.add_parser("sugerir", help="Sugere titulo, descricao e hashtags locais a partir da fala do corte.")
    p_sug.add_argument("--video", required=True, help="Caminho do arquivo .mp4 do corte.")
    p_sug.add_argument("--json", action="store_true", help="Saida JSON compacta numa linha.")
    p_sug.set_defaults(func=_cmd_sugerir)

    # fila
    p_fila = sub.add_parser("fila", help="Gerencia a fila persistente de envios com retry/backoff.")
    p_fila.add_argument("acao", nargs="?", default="processar", choices=("listar", "processar", "limpar"))
    p_fila.add_argument("--json", action="store_true", help="Saida JSON compacta.")
    p_fila.set_defaults(func=_cmd_fila)

    return p


def main(argv: list[str] | None = None) -> int:
    _configurar_utf8()
    parser = construir_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))
