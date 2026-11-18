"""Autopost: quando um corte fica pronto, gera título/legenda/hashtags por rede e posta (ou deixa na fila
de aprovação). Liga com `postar_sozinho = true` no config.toml (padrão: desligado).

Tudo roda numa thread própria (uma fila só para todas as lives do processo): o render do próximo corte
nunca espera o upload. `disparar()` só põe na fila e volta na hora.
"""
from __future__ import annotations

import json
import math
import queue
import threading
import time
from pathlib import Path

from .. import log, pastas
from . import agenda, envio, metadados
from .fila import Fila, hash_arquivo, log_corte

REDES = ("youtube", "instagram", "tiktok")
_CANAIS_PADRAO = object()

_q: "queue.Queue[tuple]" = queue.Queue()
_th: threading.Thread | None = None
_lk = threading.Lock()
_ocupado = threading.Event()


def _opcoes(cfg) -> dict:
    redes = [r for r in (cfg.get("postar_redes") or REDES) if r in REDES]
    return {"redes": redes or list(REDES), "privado": bool(cfg.get("postar_privado", False)),
            "dry_run": bool(cfg.get("postar_dry_run", False)), "via": str(cfg.get("postar_via") or "postiz"),
            "revisar": bool(cfg.get("revisar_antes", True))}


def pasta_fila(cfg) -> Path:
    return Path(cfg.get("pasta_saida", "cortes"))


def nota_corte(info):
    nota = info.get("pontuacao", info.get("nota_juiz"))
    return nota if type(nota) in (int, float) and math.isfinite(nota) else 0


def criar_item(video, cfg, canal=None, info=None, palavras=None,
               canais_postiz=_CANAIS_PADRAO) -> tuple[dict, bool]:
    """Gera os metadados do corte e põe na fila em disco. -> (item, novo?)"""
    video = Path(video).resolve()
    if info is None:
        js = pastas.meta_do_video(video)
        info = json.loads(js.read_text(encoding="utf-8")) if js else {}
    canal = canal or info.get("canal") or pastas.canal_da_pasta(pastas.pasta_sessao_do_video(video).name)
    sha = hash_arquivo(video)
    fila = Fila(pasta_fila(cfg))
    existente = fila.pegar(sha[:12])
    if existente:
        return existente, False
    t0 = time.time()
    meta = metadados.gerar(info.get("transcricao", ""), canal, cfg, info.get("motivos", ()), palavras,
                           info.get("momento_na_live"),
                           frase_sugerida=info.get("titulo_sugerido", ""))
    op = _opcoes(cfg)
    item = {"id": sha[:12], "sha256": sha, "video": str(video), "canal": canal,
            "corte": video.stem, "momento_hms": info.get("momento_hms"), "duracao": info.get("duracao"),
            "pontuacao": nota_corte(info),
            "estado": "aguardando_aprovacao" if op["revisar"] else "na_fila",
            "metadados": meta, "opcoes": op, "gerado_em_s": round(time.time() - t0, 2),
            "redes": {r: {"estado": "pendente"} for r in op["redes"]}}
    if canais_postiz is not _CANAIS_PADRAO:
        item["canais_postiz"] = canais_postiz
    item, novo = fila.criar(item)
    if novo:
        log_corte(video, {"etapa": "metadados", "fonte": meta["fonte"], "obs": meta.get("obs", ""),
                          "estado": item["estado"], "youtube": meta["youtube"]["titulo"]})
    return item, novo


def processar(id_, cfg, dry_run=None, ao_progresso=None) -> dict:
    """Posta um item nas redes pendentes. Pula rede já postada com o mesmo arquivo (anti-duplicado)."""
    fila = Fila(pasta_fila(cfg))
    it = fila.pegar(id_)
    if not it:
        raise KeyError(f"item {id_} não está na fila")
    op = {**_opcoes(cfg), **it.get("opcoes", {})}
    if dry_run is not None:
        op["dry_run"] = bool(dry_run)
    if it["estado"] == "rejeitado":
        return it
    fila.atualizar(it["id"], estado="postando")
    for rede in list(it["redes"]):
        st = it["redes"][rede]
        if st.get("estado") in ("postado", "duplicado"):
            continue
        ja = fila.ja_postado(it["sha256"], rede, op["dry_run"])
        if ja:
            fila.atualizar(it["id"], redes={rede: {"estado": "duplicado", "link": ja["link"]}})
            log_corte(it["video"], {"etapa": "duplicado", "rede": rede, "link": ja["link"]})
            continue
        fila.atualizar(it["id"], redes={rede: {"estado": "enviando"}})

        def prog(d, rede=rede):
            log_corte(it["video"], {"etapa": "progresso", **d})
            if ao_progresso:
                ao_progresso({"id": it["id"], **d})
        r = envio.enviar(it["video"], rede, it["metadados"], op["privado"], op["dry_run"], prog, via=op["via"])
        if r["estado"] == "postado":
            fila.registrar_postado(it["sha256"], rede, r["link"], it["video"], op["dry_run"])
            fila.atualizar(it["id"], redes={rede: {"estado": "postado", "link": r["link"],
                                                    "dry_run": op["dry_run"], "via": op["via"],
                                                    "quando": time.time()}})
        else:
            fila.atualizar(it["id"], redes={rede: {"estado": "erro", "erro": r["erro"]}})
        log_corte(it["video"], {"etapa": r["estado"], "rede": rede, "link": r.get("link"),
                                "erro": r.get("erro"), "conector": r["modulo"], "dry_run": op["dry_run"],
                                "via": op["via"]})
    it = fila.pegar(it["id"])
    est = [s["estado"] for s in it["redes"].values()]
    ok = sum(e in ("postado", "duplicado") for e in est)
    final = "postado" if ok == len(est) else ("parcial" if ok else "erro")
    return fila.atualizar(it["id"], estado=final)


# ---------------------------------------------------------------- gancho da sessão
def _trabalhador():
    while True:
        video, cfg, canal, info, palavras, reg = _q.get()
        _ocupado.set()
        try:
            log.usar(reg)
            t0 = time.time()
            item, novo = criar_item(video, cfg, canal, info, palavras)
            if not novo:
                log.info(f"Autopost: {Path(video).name} já estava na fila ({item['estado']}).")
            elif item["estado"] == "aguardando_aprovacao":
                log.info(f"Autopost: {Path(video).name} aguardando aprovação no painel — "
                         f"\"{item['metadados']['youtube']['titulo']}\"")
            else:
                agenda.processar_pendentes(pasta_fila(cfg), cfg)
                log.info(f"Autopost: fila atualizada em {time.time() - t0:.0f} s.")
        except Exception as e:
            log.aviso(f"Autopost do {Path(video).name} falhou: {str(e)[:200]}")
            try:
                log_corte(video, {"etapa": "falha", "erro": str(e)[:500]})
            except Exception:
                pass
        finally:
            _q.task_done()
            if _q.unfinished_tasks == 0:
                _ocupado.clear()


def disparar(video, cfg, canal=None, info=None, palavras=None) -> float:
    """Chamado no fim de cada corte. Só enfileira e volta (ms). -> segundos gastos aqui."""
    global _th
    t0 = time.perf_counter()
    with _lk:
        if _th is None or not _th.is_alive():
            _th = threading.Thread(target=_trabalhador, daemon=True, name="autopost")
            _th.start()
    _q.put((str(video), dict(cfg), canal, dict(info) if info else None,
            list(palavras) if palavras else None, log.atual()))
    return time.perf_counter() - t0


def aguardar(timeout=300.0) -> bool:
    """No fim da sessão: espera os posts em andamento (até `timeout`). -> True se esvaziou."""
    t0 = time.time()
    while _q.unfinished_tasks and time.time() - t0 < timeout:
        time.sleep(0.2)
    return _q.unfinished_tasks == 0
