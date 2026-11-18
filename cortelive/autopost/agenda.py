"""Agenda persistida de publicação automática, compartilhada pela sessão e pelo painel."""
from __future__ import annotations

import time
import json
from math import ceil
from pathlib import Path

from .. import pastas
from ..postar.config import carregar_config_postar
from ..postar.fila import JANELA_24H_S
from ..postar.redes import ErroEnvio
from ..postar.redes.postiz import (
    data_utc_postiz, editar_post_postiz, excluir_post_postiz, proximo_horario_postiz, timestamp_postiz,
)
from . import envio, metadados
from .fila import Fila

PADROES_AGENDA = {
    "max_posts_dia": 48, "intervalo_min_posts": 30, "expirar_cortes_horas": 48, "rodizio": False,
    "canais_postiz": [], "usar_horarios_postiz": False,
}
FAIXAS_AGENDA = {"max_posts_dia": (1, 500), "intervalo_min_posts": (0, 240),
                 "expirar_cortes_horas": (1, None)}


def opcao_valida(chave, valor):
    if chave in ("rodizio", "usar_horarios_postiz"):
        return isinstance(valor, bool)
    if chave == "canais_postiz":
        return valor == [] or canais_validos(valor)
    minimo, maximo = FAIXAS_AGENDA[chave]
    return type(valor) is int and valor >= minimo and (maximo is None or valor <= maximo)


def opcoes_agenda(cfg):
    cfg = {**cfg}
    for atual, antigo in (("max_posts_dia", "postar_max_dia"), ("intervalo_min_posts", "postar_intervalo_min"),
                          ("canais_postiz", "postar_canais"), ("rodizio", "postar_rodizio")):
        if antigo in cfg:
            cfg.setdefault(atual, cfg[antigo])
    opcoes = {k: cfg[k] if k in cfg and opcao_valida(k, cfg[k]) else v for k, v in PADROES_AGENDA.items()}
    opcoes["canais_postiz"] = opcoes["canais_postiz"].copy()
    return opcoes


def canais_validos(canais):
    return isinstance(canais, list) and bool(canais) and all(
        isinstance(canal, str) and 0 < len(canal) <= 256 and canal.strip() == canal
        for canal in canais
    ) and len(set(canais)) == len(canais)


def _consultar_canais(cfg):
    from ..postar.redes.postiz import listar_integracoes_postiz, obter_credencial_postiz

    chave, base = obter_credencial_postiz(cfg)
    return listar_integracoes_postiz(chave, base_url=base)


def _destinos(item, rede, canais, cfg, historico):
    from ..postar.redes.postiz import IDENTIFICADORES_POR_REDE

    selecao = item.get("canais_postiz", cfg["canais_postiz"] or None)
    ids = sorted({canal["id"] for canal in canais
                  if canal["identifier"] in IDENTIFICADORES_POR_REDE.get(rede, ())
                  and not canal.get("desativado") and (selecao is None or canal["id"] in selecao)})
    if ids and cfg["rodizio"]:
        def ordem(canal):
            datas = historico.get((rede, canal), ())
            ultimo = max(datas, default=0)
            return ultimo, datas.count(ultimo), canal
        return [min(ids, key=ordem)]
    return ids


def _em_espera(estado, agora):
    # Versões anteriores esperavam localmente para cumprir estes limites.
    return not estado.get("limites_espera") and estado.get("proxima_tentativa", 0) > agora


ENCERRADOS_REDE = ("agendado", "postado", "duplicado", "ignorado", "confirmacao_pendente")
HORARIOS_OCUPADOS = ("agendado", "confirmacao_pendente")


def _historico(fila, itens):
    historico = fila.historico_por_canal()
    for item in itens:
        for rede, estado in item.get("redes", {}).items():
            for canal, dados in estado.get("canais", {}).items():
                if dados.get("estado") in HORARIOS_OCUPADOS:
                    historico.setdefault((rede, canal), []).append(timestamp_postiz(dados["agendado_para"]))
    return historico


def _calcular_horario(datas, cfg, agora, vaga=None):
    # Deixa um minuto de folga depois do upload para o Postiz registrar o horário.
    hora = max(agora + 60, vaga or 0)
    if datas:
        hora = max(hora, max(datas) + cfg["intervalo_min_posts"] * 60)
        limite = cfg["max_posts_dia"]
        if len(datas) >= limite:
            hora = max(hora, sorted(datas)[-limite] + JANELA_24H_S)
    return data_utc_postiz(ceil(hora))


def _primeiro_agendamento(redes):
    return min((canal["agendado_para"] for rede in redes.values()
                for canal in rede.get("canais", {}).values() if canal.get("estado") == "agendado"),
               default=None)


def agendamentos_futuros(item):
    agora = time.time()
    return [(rede, canal, dados) for rede, estado in item["redes"].items()
            for canal, dados in estado.get("canais", {}).items()
            if dados.get("estado") == "agendado" and timestamp_postiz(dados["agendado_para"]) > agora]


def cancelar_agendamentos(fila, item, alvos):
    cfg = carregar_config_postar()
    redes = item["redes"]
    ident = item["id"]
    fila.atualizar(ident, estado="cancelando", erro=None)
    for rede, canal, dados in alvos:
        if timestamp_postiz(dados["agendado_para"]) <= time.time():
            continue
        excluir_post_postiz(dados["post_id"], cfg)
        redes[rede]["canais"][canal] = {"estado": "cancelado", "post_id": dados["post_id"]}
        if all(d["estado"] == "cancelado" for d in redes[rede]["canais"].values()):
            redes[rede].update(estado="cancelado", link=None)
        fila.atualizar(ident, redes=redes, agendado_para=_primeiro_agendamento(redes))
    primeiro = _primeiro_agendamento(redes)
    incerto = any(d["estado"] == "confirmacao_pendente" for r in redes.values() for d in r.get("canais", {}).values())
    estado = "confirmacao_pendente" if incerto else ("parcial" if primeiro else "cancelado")
    erro = "Confira o agendamento no calendário do Postiz." if incerto else None
    return fila.atualizar(ident, estado=estado, agendado_para=primeiro, erro=erro)


def editar_agendamentos(fila, item, alvos, edicao):
    cfg = carregar_config_postar()
    meta = metadados.aplicar_edicao(item["metadados"], **edicao)
    for rede, canal, dados in alvos:
        titulo, descricao = envio.textos_da_rede(meta, rede)
        if rede == "youtube":
            if not edicao.keys() & {"titulo", "descricao", "tags"}:
                continue
            titulo = titulo if "titulo" in edicao else None
            descricao = descricao if "descricao" in edicao else None
        elif rede not in edicao:
            continue
        tags = meta["youtube"]["tags"] if rede == "youtube" and "tags" in edicao else None
        editar_post_postiz(rede, canal, dados, titulo, descricao, tags, cfg)
    return fila.atualizar(item["id"], metadados=meta)


def _processar_rede(fila, item, rede, estado, config_postiz, cache, cfg, agora, emitir, historico):
    if estado.get("estado") in ENCERRADOS_REDE:
        return estado
    atual = {**estado}
    destinos = atual.get("destinos")
    if destinos is None:
        destinos = _destinos(item, rede, cache["canais"], cfg, historico)
        if not destinos:
            return {"estado": "ignorado"}
        atual["destinos"] = destinos
    concluidos = dict(atual.get("canais", {}))
    adiados = cache.setdefault("canais_adiados", set())
    if _em_espera(estado, agora):
        adiados.update((rede, canal) for canal in destinos if canal not in concluidos)
        return estado
    atual.update(estado="pendente", canais=concluidos, proxima_tentativa=0, limites_espera=None)
    for canal in destinos:
        if canal in concluidos:
            continue
        alvo = f"{rede}:{canal}"
        anterior = fila.ja_postado(item["sha256"], alvo) or fila.ja_postado(item["sha256"], rede)
        if anterior:
            concluidos[canal] = {"estado": "duplicado", "link": anterior["link"]}
            continue
        if (rede, canal) in adiados:
            return atual
        datas = historico.setdefault((rede, canal), [])

        def agendar():
            vaga = proximo_horario_postiz(canal, config_postiz) if cfg["usar_horarios_postiz"] else None
            data = _calcular_horario(datas, cfg, max(agora, time.time()), vaga)
            # Uma interrupção depois deste ponto pode significar que o Postiz aceitou o POST.
            concluidos[canal] = {"estado": "confirmacao_pendente", "agendado_para": data}
            fila.atualizar(item["id"], redes={rede: atual})
            return data

        resultado = envio.enviar_postiz(
            item["video"], rede, canal, item["metadados"], item.get("opcoes", {}).get("privado", False),
            config_postiz, cache, lambda e: emitir({**e, "id": item["id"], "canal_postiz": canal}), agendar,
        )
        if resultado.get("etapa") == "concluido":
            dados = concluidos[canal]
            dados.update(estado="agendado", link=resultado["link"], post_id=resultado["post_id"],
                         midia=cache.get(f"midia:{Path(item['video']).resolve()}"))
            datas.append(timestamp_postiz(dados["agendado_para"]))
        elif resultado.get("confirmacao_pendente"):
            dados = concluidos[canal]
            dados["erro"] = resultado["erro"]
            datas.append(timestamp_postiz(dados["agendado_para"]))
        elif resultado.get("espera_s"):
            concluidos.pop(canal, None)
            adiados.add((rede, canal))
            atual.update(proxima_tentativa=time.time() + resultado["espera_s"], erro=resultado.get("erro"))
            return atual
        else:
            concluidos[canal] = {"estado": "erro", "erro": resultado.get("erro")}
        primeiro = _primeiro_agendamento({**item["redes"], rede: atual})
        fila.atualizar(item["id"], redes={rede: atual}, agendado_para=primeiro)
    sucessos = [canal for canal in concluidos.values() if canal.get("link")]
    if sucessos:
        atual["link"] = sucessos[0]["link"]
    estados = {canal["estado"] for canal in concluidos.values()}
    if "confirmacao_pendente" in estados:
        atual.update(estado="confirmacao_pendente", erro="Confira o agendamento no calendário do Postiz.")
    elif "erro" in estados:
        atual.update(estado="erro", erro="Um ou mais canais falharam.")
    else:
        atual.update(estado="agendado" if "agendado" in estados else "postado", erro=None)
    return atual


def processar_pendentes(pasta, cfg, emitir=None, agora=None):
    """Transfere os agendamentos para o Postiz em ordem de prioridade."""
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
        historico = _historico(fila, itens)
        cache = {}
        config_postiz = None
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
            if config_postiz is None:
                config_postiz = carregar_config_postar()
                try:
                    cache["canais"] = _consultar_canais(config_postiz)
                except ErroEnvio:
                    fila.atualizar(ident, estado="na_fila", erro="Não foi possível consultar os canais do Postiz.")
                    break
            fila.atualizar(ident, estado="postando")
            redes = {rede: _processar_rede(fila, item, rede, s, config_postiz, cache, cfg, now, emitir, historico)
                     for rede, s in item["redes"].items()}
            estados = {s["estado"] for s in redes.values()}
            primeiro = _primeiro_agendamento(redes)
            if "pendente" in estados:
                final = "na_fila"
            elif "confirmacao_pendente" in estados:
                final = "confirmacao_pendente"
            elif "erro" in estados:
                final = "parcial" if any(s.get("link") for s in redes.values()) else "erro"
            elif estados == {"ignorado"}:
                final = "erro"
            else:
                final = "agendado" if primeiro else "postado"
            mensagem = None
            if estados == {"ignorado"}:
                mensagem = "Nenhum canal ativo corresponde à seleção."
            elif final == "confirmacao_pendente":
                mensagem = "Confira o agendamento no calendário do Postiz."
            resultados.append(fila.atualizar(ident, estado=final, redes=redes, erro=mensagem,
                                             agendado_para=primeiro))
    return resultados
