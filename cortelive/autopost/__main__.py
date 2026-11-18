"""CLI do autopost (o painel chama esta; saída sempre em JSON, uma linha por objeto).

  python -m cortelive.autopost fila [--estado aguardando_aprovacao]
  python -m cortelive.autopost gerar --video corte_001.mp4 [--enfileirar]
  python -m cortelive.autopost editar ID [--titulo T] [--descricao D] [--tiktok L] [--instagram L] [--tags a,b]
  python -m cortelive.autopost aprovar ID [--dry-run]       (posta agora; progresso em linhas JSON)
  python -m cortelive.autopost rejeitar ID
  python -m cortelive.autopost tentar ID [--dry-run]        (posta de novo as redes que deram erro)

--pasta PASTA: pasta dos cortes (padrão: pasta_saida do config.toml).
"""
import argparse
import json
import sys
from pathlib import Path


def _p(obj):
    print(json.dumps(obj, ensure_ascii=False), flush=True)


def _cfg(a):
    from ..cli import carregar_config
    cfg = carregar_config()
    if a.pasta:
        cfg["pasta_saida"] = a.pasta
    return cfg


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(prog="python -m cortelive.autopost")
    ap.add_argument("--pasta", help="pasta dos cortes (padrão: pasta_saida do config)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fila")
    f.add_argument("--estado")
    g = sub.add_parser("gerar")
    g.add_argument("--video", required=True)
    g.add_argument("--canal")
    g.add_argument("--enfileirar", action="store_true", help="além de gerar, põe na fila")
    e = sub.add_parser("editar")
    e.add_argument("id")
    e.add_argument("--titulo")
    e.add_argument("--descricao")
    e.add_argument("--tiktok")
    e.add_argument("--instagram")
    e.add_argument("--tags", help="tags do YouTube separadas por vírgula")
    for nome in ("aprovar", "tentar"):
        x = sub.add_parser(nome)
        x.add_argument("id")
        x.add_argument("--dry-run", action="store_true")
        x.add_argument("--adiar", action="store_true", help="apenas libera para o próximo ciclo da agenda")
    r = sub.add_parser("rejeitar")
    r.add_argument("id")
    a = ap.parse_args(argv)

    from . import agenda, criar_item, metadados, processar
    from .fila import Fila
    cfg = _cfg(a)
    fila = Fila(Path(cfg["pasta_saida"]))
    try:
        if a.cmd == "fila":
            _p({"ok": True, "itens": fila.itens(a.estado)})
        elif a.cmd == "gerar":
            v = Path(a.video)
            if not v.exists():
                raise FileNotFoundError(f"vídeo não encontrado: {v}")
            if a.enfileirar:
                item, novo = criar_item(v, cfg, a.canal)
                _p({"ok": True, "novo": novo, "item": item})
            else:
                js = v.with_suffix(".json")
                info = json.loads(js.read_text(encoding="utf-8")) if js.exists() else {}
                canal = a.canal or v.resolve().parent.name.split("_", 2)[-1]
                _p({"ok": True, "video": str(v.resolve()), "canal": canal,
                    "metadados": metadados.gerar(info.get("transcricao", ""), canal, cfg, info.get("motivos", ()),
                                                 None, info.get("momento_na_live"),
                                                 frase_sugerida=info.get("titulo_sugerido", ""))})
        elif a.cmd == "editar":
            it = fila.pegar(a.id)
            if not it:
                raise KeyError(f"item {a.id} não está na fila")
            if it["estado"] not in ("aguardando_aprovacao", "erro", "parcial", "na_fila"):
                raise ValueError(f"item {it['id']} está '{it['estado']}', não dá para editar")
            meta = metadados.aplicar_edicao(it["metadados"], a.titulo, a.descricao, a.tiktok, a.instagram,
                                            a.tags.split(",") if a.tags is not None else None)
            _p({"ok": True, "item": fila.atualizar(it["id"], metadados=meta)})
        elif a.cmd in ("aprovar", "tentar"):
            it = fila.pegar(a.id)
            if not it:
                raise KeyError(f"item {a.id} não está na fila")
            if a.cmd == "aprovar" and it["estado"] != "aguardando_aprovacao":
                raise ValueError(f"item {it['id']} está '{it['estado']}', não aguardando aprovação")
            if a.cmd == "tentar" and it["estado"] not in ("erro", "parcial", "na_fila"):
                raise ValueError(f"item {it['id']} está '{it['estado']}', não pode ser reenviado")
            final = fila.atualizar(it["id"], estado="na_fila", aprovado=True)
            if not a.adiar:
                if a.dry_run or it.get("opcoes", {}).get("dry_run"):
                    final = processar(it["id"], cfg, dry_run=True, ao_progresso=_p)
                else:
                    agenda.processar_pendentes(Path(cfg["pasta_saida"]), cfg, emitir=_p)
                    final = fila.pegar(it["id"])
            _p({"ok": final["estado"] in ("na_fila", "postado", "parcial", "agendado"), "item": final})
        elif a.cmd == "rejeitar":
            with fila.reservar_processamento() as livre:
                if not livre:
                    raise ValueError("Aguarde o processamento da fila antes de rejeitar o corte.")
                it = fila.pegar(a.id)
                if not it:
                    raise KeyError(f"item {a.id} não está na fila")
                if it["estado"] in ("postando", "cancelando", "cancelado", "confirmacao_pendente") or any(
                    canal.get("estado") in agenda.HORARIOS_OCUPADOS
                    for rede in it["redes"].values() for canal in rede.get("canais", {}).values()
                ):
                    raise ValueError("O envio já começou. Use o cancelamento de agendamento no Postiz.")
                _p({"ok": True, "item": fila.atualizar(it["id"], estado="rejeitado")})
    except Exception as ex:
        _p({"ok": False, "erro": str(ex)})
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
