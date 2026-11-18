"""Agenda persistida de publicação automática, compartilhada pela sessão e pelo painel."""
from __future__ import annotations

import time
import json

from .. import pastas
from ..postar.fila import ControladorLimites, ErroLimiteRede
from . import envio
from .fila import Fila

PADROES_AGENDA = {
    "max_posts_dia": 48, "intervalo_min_posts": 30, "expirar_cortes_horas": 48, "rodizio": False,
    "canais_postiz": [],
}
FAIXAS_AGENDA = {"max_posts_dia": (1, 500), "intervalo_min_posts": (0, 240),
                 "expirar_cortes_horas": (1, None)}


def opcao_valida(chave, valor):
    if chave == "rodizio":
        return isinstance(valor, bool)
    if chave == "canais_postiz":
        return valor == [] or canais_validos(valor)
    minimo, maximo = FAIXAS_AGENDA[chave]
    return type(valor) is int and valor >= minimo and (maximo is None or valor <= maximo)


def opcoes_agenda(cfg):
    opcoes = {k: cfg[k] if k in cfg and opcao_valida(k, cfg[k]) else v for k, v in PADROES_AGENDA.items()}
    opcoes["canais_postiz"] = opcoes["canais_postiz"].copy()
    return opcoes


def canais_validos(canais):
    return isinstance(canais, list) and bool(canais) and all(
        isinstance(canal, str) and 0 < len(canal) <= 256 and canal.strip() == canal
        for canal in canais
    ) and len(set(canais)) == len(canais)


def _config_postiz(cfg):
    from ..postar.config import REDES_SUPORTADAS, carregar_config_postar

    postagem = carregar_config_postar()
    for rede in REDES_SUPORTADAS:
        postagem[rede] = {**postagem.get(rede, {}), "limite_24h": cfg["max_posts_dia"],
                          "intervalo_min_s": cfg["intervalo_min_posts"] * 60}
    return postagem


def _consultar_canais(cfg):
    from ..postar.redes.postiz import listar_integracoes_postiz, obter_credencial_postiz

    chave, base = obter_credencial_postiz(cfg)
    return listar_integracoes_postiz(chave, base_url=base)


def _destinos(fila, item, rede, canais, cfg):
    from ..postar.redes.postiz import IDENTIFICADORES_POR_REDE

    selecao = item.get("canais_postiz", cfg["canais_postiz"] or None)
    ids = sorted({canal["id"] for canal in canais
                  if canal["identifier"] in IDENTIFICADORES_POR_REDE.get(rede, ())
                  and not canal.get("desativado") and (selecao is None or canal["id"] in selecao)})
    if ids and cfg["rodizio"]:
        return [min(ids, key=lambda c: (fila.ultimo_por_canal(rede, c), c))]
    return ids


def _em_espera(estado, cfg, agora):
    limites = estado.get("limites_espera")
    if limites is not None and limites != [cfg["max_posts_dia"], cfg["intervalo_min_posts"]]:
        return False
    return estado.get("proxima_tentativa", 0) > agora


def _processar_rede(fila, item, rede, estado, config_postiz, cache, cfg, agora, emitir, limites):
    if estado.get("estado") in ("postado", "duplicado", "ignorado") or _em_espera(estado, cfg, agora):
        return estado
    atual = {**estado}
    destinos = atual.get("destinos")
    if destinos is None:
        destinos = _destinos(fila, item, rede, cache["canais"], cfg)
        if not destinos:
            return {"estado": "ignorado"}
        # O rodízio só fixa a conta quando há uma vaga para começar a enviar.
        try:
            limites.verificar_disponibilidade(rede, config_postiz, agora=agora)
        except ErroLimiteRede as erro:
            return {**atual, "estado": "pendente", "erro": str(erro),
                    "proxima_tentativa": agora + erro.aguardar_s,
                    "limites_espera": [cfg["max_posts_dia"], cfg["intervalo_min_posts"]]}
        atual["destinos"] = destinos
    concluidos = dict(atual.get("canais", {}))
    atual.update(estado="pendente", canais=concluidos, proxima_tentativa=0, limites_espera=None)
    for canal in destinos:
        if canal in concluidos:
            continue
        alvo = f"{rede}:{canal}"
        anterior = fila.ja_postado(item["sha256"], alvo) or fila.ja_postado(item["sha256"], rede)
        if anterior:
            concluidos[canal] = {"estado": "duplicado", "link": anterior["link"]}
            continue
        fila.atualizar(item["id"], redes={rede: atual})
        resultado = envio.enviar_postiz(
            item["video"], rede, canal, item["metadados"], item.get("opcoes", {}).get("privado", False),
            config_postiz, cache, lambda e: emitir({**e, "id": item["id"], "canal_postiz": canal}),
        )
        if resultado.get("etapa") == "concluido":
            fila.registrar_postado(item["sha256"], alvo, resultado["link"], item["video"])
            concluidos[canal] = {"estado": "postado", "link": resultado["link"]}
            fila.atualizar(item["id"], redes={rede: atual})
        elif resultado.get("espera_s"):
            atual.update(proxima_tentativa=time.time() + resultado["espera_s"], erro=resultado.get("erro"))
            if resultado.get("limite_rede"):
                atual["limites_espera"] = [cfg["max_posts_dia"], cfg["intervalo_min_posts"]]
            return atual
        else:
            concluidos[canal] = {"estado": "erro", "erro": resultado.get("erro")}
            fila.atualizar(item["id"], redes={rede: atual})
    sucessos = [canal for canal in concluidos.values() if canal.get("link")]
    if sucessos:
        atual["link"] = sucessos[0]["link"]
    atual.update(estado="postado" if len(sucessos) == len(destinos) else "erro",
                 erro=None if len(sucessos) == len(destinos) else "Um ou mais canais falharam.")
    return atual


def processar_pendentes(pasta, cfg, emitir=None, agora=None):
    """Processa o corte mais bem avaliado primeiro e preserva os demais quando não há vaga."""
    from . import nota_corte, processar

    now = time.time() if agora is None else agora
    cfg = {**cfg, **opcoes_agenda(cfg)}
    emitir = emitir or (lambda _: None)
    fila = Fila(pasta)
    resultados = []
    with fila.reservar_processamento() as livre:
        if not livre:
            return resultados
        def prioridade(item):
            if "pontuacao" not in item:
                meta = pastas.meta_do_video(item["video"])
                try:
                    item["pontuacao"] = nota_corte(json.loads(meta.read_text(encoding="utf-8"))) if meta else 0
                except (OSError, ValueError):
                    item["pontuacao"] = 0
            return -item["pontuacao"], item.get("criado", 0), item["id"]

        itens = sorted(fila.itens(), key=prioridade)
        cache = {}
        config_postiz = None
        limites = None
        for item in itens:
            estado = item["estado"]
            if estado not in ("na_fila", "postando", "aguardando_aprovacao"):
                continue
            ident = item["id"]
            if now - item.get("criado", now) >= cfg["expirar_cortes_horas"] * 3600:
                resultados.append(fila.atualizar(ident, estado="expirado"))
                continue
            if estado == "aguardando_aprovacao" or not (
                item.get("aprovado") or cfg.get("postar_sozinho") and not cfg.get("revisar_antes", True)
            ):
                continue
            op = item.get("opcoes", {})
            if op.get("dry_run") or op.get("via") == "direto":
                resultados.append(processar(ident, cfg, ao_progresso=emitir))
                continue
            if all(s.get("estado") in ("postado", "duplicado", "ignorado") or
                   _em_espera(s, cfg, now) for s in item["redes"].values()):
                continue
            if config_postiz is None:
                config_postiz = _config_postiz(cfg)
                limites = ControladorLimites()
                from ..postar.redes import ErroEnvio
                try:
                    cache["canais"] = _consultar_canais(config_postiz)
                except ErroEnvio:
                    fila.atualizar(ident, estado="na_fila", erro="Não foi possível consultar os canais do Postiz.")
                    break
            fila.atualizar(ident, estado="postando")
            redes = {rede: _processar_rede(fila, item, rede, s, config_postiz, cache, cfg, now, emitir, limites)
                     for rede, s in item["redes"].items()}
            estados = {s["estado"] for s in redes.values()}
            if "pendente" in estados:
                final = "na_fila"
            elif "erro" in estados:
                final = "parcial" if any(s.get("link") for s in redes.values()) else "erro"
            elif estados == {"ignorado"}:
                final = "erro"
            else:
                final = "postado"
            mensagem = "Nenhum canal ativo corresponde à seleção." if estados == {"ignorado"} else None
            resultados.append(fila.atualizar(ident, estado=final, redes=redes, erro=mensagem))
    return resultados
