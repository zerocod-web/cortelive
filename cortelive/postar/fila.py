"""Orquestrador de envio, controle de limites por rede e fila persistente com retry e backoff.

Nota de arquitetura: o modo automatico 'postar ao cortar' pertence exclusivamente ao
modulo `cortelive.autopost`; este pacote (`cortelive.postar`) nao instala gancho no corte.
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

import requests

from .config import (
    REDES_SUPORTADAS,
    carregar_config_postar,
    garantir_fora_do_repo,
    pasta_dados_local,
    resolver_provedor_via,
)
from .oauth import ErroOAuth, obter_token_valido
from .redes import ErroAutenticacao, ErroEnvio, ErroTemporario
from .redes.instagram import enviar_instagram
from .redes.postiz import POSTIZ_CLOUD_API_URL, ErroAgendamentoIncerto, enviar_rede_postiz
from .redes.tiktok import enviar_tiktok
from .redes.youtube import enviar_youtube
from .sugestao import sugerir_metadados

EmissorEvento = Callable[[dict[str, Any]], None]

JANELA_24H_S = 86400.0


def reservar_requisicao_postiz_cloud(agora=None, arquivo=None):
    """Reserva uma tentativa de criar post, inclusive retries, em janela móvel."""
    ts = time.time() if agora is None else agora
    db = Path(arquivo) if arquivo is not None else pasta_dados_local() / "postiz_cloud.sqlite3"
    db.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db, timeout=15)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("CREATE TABLE IF NOT EXISTS requisicoes (ts REAL NOT NULL)")
        conn.execute("DELETE FROM requisicoes WHERE ts <= ?", (ts - 3600,))
        quantidade, primeira = conn.execute("SELECT COUNT(*), MIN(ts) FROM requisicoes").fetchone()
        if quantidade >= 100:
            raise ErroTemporario("Limite de criação de posts do Postiz Cloud atingido.",
                                  retry_after_s=max(1, primeira + 3600 - ts))
        conn.execute("INSERT INTO requisicoes (ts) VALUES (?)", (ts,))


class SessaoPostizLimitada:
    def __init__(self, sessao):
        self.sessao = sessao

    def get(self, url, **opcoes):
        return self.sessao.get(url, **opcoes)

    def post(self, url, **opcoes):
        alvo = urlsplit(url)
        cloud = urlsplit(POSTIZ_CLOUD_API_URL)
        if alvo.hostname == cloud.hostname and alvo.path.rstrip("/") == cloud.path + "/posts":
            reservar_requisicao_postiz_cloud()
        return self.sessao.post(url, **opcoes)


class ErroLimiteRede(RuntimeError):
    """Indica que a rede atingiu o limite seguro de envios ou intervalo minimo."""

    def __init__(self, mensagem: str, aguardar_s: float):
        super().__init__(mensagem)
        self.aguardar_s = max(1.0, aguardar_s)


class ControladorLimites:
    """Registra envios concluidos por rede para respeitar cotas de 24h e intervalo minimo."""

    def __init__(self, arquivo_estado: Path | None = None):
        if arquivo_estado is not None:
            alvo = Path(arquivo_estado).resolve()
        else:
            alvo = (pasta_dados_local() / "limites_postagem.json").resolve()
        garantir_fora_do_repo(alvo)
        self.arquivo = alvo

    def _carregar(self) -> dict[str, list[float]]:
        if not self.arquivo.is_file():
            return {}
        try:
            bruto = json.loads(self.arquivo.read_text(encoding="utf-8"))
            if isinstance(bruto, dict):
                return {k: [float(t) for t in v if isinstance(t, (int, float))] for k, v in bruto.items()}
        except (OSError, ValueError):
            pass
        return {}

    def _salvar(self, dados: dict[str, list[float]]) -> None:
        self.arquivo.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.arquivo.with_suffix(".tmp")
        tmp.write_text(json.dumps(dados, indent=2), encoding="utf-8")
        tmp.replace(self.arquivo)

    def verificar_disponibilidade(self, rede: str, cfg: dict[str, Any], agora: float | None = None) -> None:
        ts_atual = time.time() if agora is None else agora
        bloco = cfg.get(rede) or {}
        limite_24h = int(bloco.get("limite_24h", 15))
        intervalo_min_s = float(bloco.get("intervalo_min_s", 0.0))

        todos = self._carregar()
        recentes = sorted(t for t in todos.get(rede, []) if ts_atual - t < JANELA_24H_S)

        if limite_24h > 0 and len(recentes) >= limite_24h:
            mais_antigo = recentes[-limite_24h]
            falta_s = max(1.0, JANELA_24H_S - (ts_atual - mais_antigo))
            horas = falta_s / 3600.0
            raise ErroLimiteRede(
                f"Limite de {limite_24h} envios em 24h atingido para {rede.capitalize()} "
                f"(libera em ~{horas:.1f}h).",
                aguardar_s=falta_s,
            )

        if intervalo_min_s > 0 and recentes:
            ultimo = recentes[-1]
            decorrido = ts_atual - ultimo
            if decorrido < intervalo_min_s:
                falta_s = intervalo_min_s - decorrido
                raise ErroLimiteRede(
                    f"Intervalo minimo entre postagens em {rede.capitalize()}: aguarde {int(round(falta_s))}s.",
                    aguardar_s=falta_s,
                )

    def registrar_envio(self, rede: str, agora: float | None = None) -> None:
        ts_atual = time.time() if agora is None else agora
        todos = self._carregar()
        recentes = [t for t in todos.get(rede, []) if ts_atual - t < JANELA_24H_S]
        recentes.append(ts_atual)
        todos[rede] = recentes
        self._salvar(todos)


def calcular_backoff(tentativa_1based: int, cfg: dict[str, Any], retry_after_s: float | None = None) -> float:
    base = float(cfg.get("backoff_inicial_s", 2.0))
    fator = float(cfg.get("backoff_fator", 2.0))
    teto = float(cfg.get("backoff_max_s", 60.0))
    calculado = min(teto, base * (fator ** max(0, tentativa_1based - 1)))
    if retry_after_s is not None:
        return min(max(teto, retry_after_s), max(calculado, retry_after_s))
    return calculado


def _link_dry_run(rede: str, caminho_video: Path, privado: bool, via: str = "direto") -> str:
    slug = caminho_video.stem[:24]
    modo = "privado" if privado else "publico"
    if via == "postiz":
        return f"https://platform.postiz.com/launches?postId=dry-run-{rede}-{slug}-{modo}"
    if rede == "youtube":
        return f"https://youtube.com/shorts/dry-run-{slug}-{modo}"
    if rede == "instagram":
        return f"https://www.instagram.com/reel/dry-run-{slug}-{modo}/"
    return f"https://www.tiktok.com/@dry-run/video/{slug}-{modo}"


def enviar_uma_rede(
    rede: str,
    caminho_video: Path,
    titulo: str,
    descricao: str,
    privado: bool,
    dry_run: bool,
    cfg: dict[str, Any],
    emitir: EmissorEvento,
    via: str = "direto",
    diretorio_contas: Path | None = None,
    limites: ControladorLimites | None = None,
    sessao_http: requests.Session | None = None,
    dormir: Callable[[float], None] = time.sleep,
    agora: float | None = None,
    cache_postiz: dict[str, Any] | None = None,
    agendar: Callable[[], str] | None = None,
) -> dict[str, Any]:
    """Executa o envio para uma unica rede com retry, backoff, auto-refresh e linhas JSON."""
    ultimo_pct = 0

    def _prog(etapa: str, pct: int, extras: dict[str, Any] | None = None) -> dict[str, Any]:
        nonlocal ultimo_pct
        ultimo_pct = max(ultimo_pct, min(100, int(pct)))
        ev: dict[str, Any] = {"rede": rede, "etapa": etapa, "pct": ultimo_pct}
        if extras:
            ev.update(extras)
        emitir(ev)
        return ev

    if rede not in REDES_SUPORTADAS:
        return _prog("erro", 0, {"erro": f"Rede desconhecida '{rede}'. Opcoes: {', '.join(REDES_SUPORTADAS)}."})

    try:
        provedor = resolver_provedor_via(via, cfg)
    except ValueError as exc:
        return _prog("erro", 0, {"erro": str(exc)})

    if not caminho_video.is_file():
        return _prog("erro", 0, {"erro": f"Arquivo de video nao encontrado: {caminho_video}"})
    if caminho_video.stat().st_size <= 0:
        return _prog("erro", 0, {"erro": f"Arquivo de video esta vazio: {caminho_video}"})

    _prog("preparando", 5)

    if dry_run:
        _prog("iniciando", 25)
        _prog("enviando", 70)
        link = _link_dry_run(rede, caminho_video, privado, via=provedor)
        extras_dry: dict[str, Any] = {
            "link": link,
            "dry_run": True,
            "titulo": titulo,
            "privado": privado,
        }
        if provedor == "postiz":
            extras_dry["via"] = "postiz"
        return _prog("concluido", 100, extras_dry)

    ctrl_limites = None if agendar and provedor == "postiz" else (limites or ControladorLimites())
    if ctrl_limites is not None:
        try:
            ctrl_limites.verificar_disponibilidade(rede, cfg, agora=agora)
        except ErroLimiteRede as exc:
            return _prog("erro", ultimo_pct, {"erro": str(exc), "espera_s": exc.aguardar_s, "limite_rede": True})

    tentativas_max = max(1, int(cfg.get("tentativas_max", 3)))
    ja_tentou_refresh_401 = False
    http_postiz = SessaoPostizLimitada(sessao_http or requests.Session()) if provedor == "postiz" else None

    for tentativa in range(1, tentativas_max + 1):
        try:
            _prog("autenticando", max(ultimo_pct, 8))

            if provedor == "postiz":
                link, post_id = enviar_rede_postiz(
                    rede=rede,
                    caminho_video=caminho_video,
                    titulo=titulo,
                    descricao=descricao,
                    privado=privado,
                    cfg=cfg,
                    progresso=lambda et, p: _prog(et, p),
                    diretorio_contas=diretorio_contas,
                    sessao_http=http_postiz,
                    dormir=dormir,
                    cache_execucao=cache_postiz,
                    **({"agendar": agendar} if agendar else {}),
                )
                if ctrl_limites is not None:
                    ctrl_limites.registrar_envio(rede, agora=agora)
                return _prog("concluido", 100, {"link": link, "via": "postiz", "post_id": post_id})

            dados_token = obter_token_valido(
                rede,
                cfg=cfg,
                diretorio_contas=diretorio_contas,
                sessao_http=sessao_http,
                forcar_refresh=False,
            )

            if rede == "youtube":
                link = enviar_youtube(
                    caminho_video=caminho_video,
                    titulo=titulo,
                    descricao=descricao,
                    privado=privado,
                    dados_token=dados_token,
                    cfg=cfg,
                    progresso=lambda et, p: _prog(et, p),
                    sessao_http=sessao_http,
                )
            elif rede == "instagram":
                link = enviar_instagram(
                    caminho_video=caminho_video,
                    titulo=titulo,
                    descricao=descricao,
                    privado=privado,
                    dados_token=dados_token,
                    cfg=cfg,
                    progresso=lambda et, p: _prog(et, p),
                    sessao_http=sessao_http,
                    dormir=dormir,
                )
            else:
                link = enviar_tiktok(
                    caminho_video=caminho_video,
                    titulo=titulo,
                    descricao=descricao,
                    privado=privado,
                    dados_token=dados_token,
                    cfg=cfg,
                    progresso=lambda et, p: _prog(et, p),
                    sessao_http=sessao_http,
                    dormir=dormir,
                )

            ctrl_limites.registrar_envio(rede, agora=agora)
            return _prog("concluido", 100, {"link": link})

        except ErroAgendamentoIncerto as exc:
            return _prog("erro", ultimo_pct, {"erro": str(exc), "confirmacao_pendente": True})
        except ErroAutenticacao as exc:
            if provedor == "direto" and not ja_tentou_refresh_401:
                ja_tentou_refresh_401 = True
                try:
                    obter_token_valido(
                        rede,
                        cfg=cfg,
                        diretorio_contas=diretorio_contas,
                        sessao_http=sessao_http,
                        forcar_refresh=True,
                    )
                    continue
                except ErroOAuth as exc_ref:
                    return _prog("erro", ultimo_pct, {"erro": str(exc_ref)})
            return _prog("erro", ultimo_pct, {"erro": str(exc)})

        except ErroTemporario as exc:
            if tentativa < tentativas_max:
                espera = calcular_backoff(tentativa, cfg, retry_after_s=exc.retry_after_s)
                _prog(
                    "aguardando_tentativa",
                    ultimo_pct,
                    {"tentativa": tentativa + 1, "espera_s": round(espera, 2)},
                )
                dormir(espera)
                continue
            return _prog(
                "erro",
                ultimo_pct,
                {"erro": f"Falha apos {tentativas_max} tentativas: {exc}",
                 "espera_s": calcular_backoff(tentativa, cfg, retry_after_s=exc.retry_after_s)},
            )

        except (ErroOAuth, ErroEnvio, ValueError) as exc:
            return _prog("erro", ultimo_pct, {"erro": str(exc)})

    return _prog("erro", ultimo_pct, {"erro": "Envio interrompido inesperadamente."})


def enviar_para_redes(
    caminho_video: Path | str,
    redes: list[str],
    titulo: str | None = None,
    descricao: str | None = None,
    privado: bool | None = None,
    dry_run: bool = False,
    via: str | None = None,
    cfg: dict[str, Any] | None = None,
    emitir: EmissorEvento | None = None,
    diretorio_contas: Path | None = None,
    limites: ControladorLimites | None = None,
    sessao_http: requests.Session | None = None,
    dormir: Callable[[float], None] = time.sleep,
) -> list[dict[str, Any]]:
    """Envia um corte para uma ou mais redes preenchendo titulo/descricao da fala se omitidos."""
    cfg_efetiva = cfg or carregar_config_postar()
    provedor = resolver_provedor_via(via, cfg_efetiva)
    arq = Path(caminho_video).resolve()
    privado_efetivo = bool(cfg_efetiva.get("privado_padrao", False)) if privado is None else bool(privado)

    titulo_limpo = (titulo or "").strip()
    desc_limpa = (descricao or "").strip()
    if not titulo_limpo or not desc_limpa:
        sug = sugerir_metadados(arq)
        if not titulo_limpo:
            titulo_limpo = sug["titulo"]
        if not desc_limpa:
            desc_limpa = sug["descricao"]

    eventos_finais: list[dict[str, Any]] = []
    emissor = emitir or (lambda _ev: None)
    cache_postiz: dict[str, Any] = {}

    redes_limpas = [r.strip().lower() for r in redes if r.strip()]
    for rede in redes_limpas:
        final = enviar_uma_rede(
            rede=rede,
            caminho_video=arq,
            titulo=titulo_limpo,
            descricao=desc_limpa,
            privado=privado_efetivo,
            dry_run=dry_run,
            via=provedor,
            cfg=cfg_efetiva,
            emitir=emissor,
            diretorio_contas=diretorio_contas,
            limites=limites,
            sessao_http=sessao_http,
            dormir=dormir,
            cache_postiz=cache_postiz,
        )
        eventos_finais.append(final)

    return eventos_finais


class FilaPostagem:
    """Fila persistente em disco com nova tentativa e backoff respeitando limites de cada rede."""

    def __init__(
        self,
        arquivo_fila: Path | None = None,
        limites: ControladorLimites | None = None,
        diretorio_contas: Path | None = None,
    ):
        if arquivo_fila is not None:
            alvo = Path(arquivo_fila).resolve()
        else:
            alvo = (pasta_dados_local() / "fila_postagem.json").resolve()
        garantir_fora_do_repo(alvo)
        self.arquivo = alvo
        self.limites = limites or ControladorLimites(alvo.parent / "limites_postagem.json")
        self.diretorio_contas = diretorio_contas

    def listar(self) -> list[dict[str, Any]]:
        if not self.arquivo.is_file():
            return []
        try:
            itens = json.loads(self.arquivo.read_text(encoding="utf-8"))
            return itens if isinstance(itens, list) else []
        except (OSError, ValueError):
            return []

    def _salvar(self, itens: list[dict[str, Any]]) -> None:
        self.arquivo.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.arquivo.with_suffix(".tmp")
        tmp.write_text(json.dumps(itens, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.arquivo)

    def limpar_concluidos(self) -> int:
        itens = self.listar()
        restantes = [i for i in itens if i.get("estado") != "concluido"]
        removidos = len(itens) - len(restantes)
        self._salvar(restantes)
        return removidos

    def enfileirar(
        self,
        caminho_video: Path | str,
        redes: list[str],
        titulo: str | None = None,
        descricao: str | None = None,
        privado: bool = False,
        dry_run: bool = False,
        via: str = "direto",
    ) -> list[dict[str, Any]]:
        if via not in ("direto", "postiz"):
            raise ValueError(f"Provedor invalido: {via}")
        arq = Path(caminho_video).resolve()
        sug = sugerir_metadados(arq) if not (titulo and descricao) else {}
        titulo_final = (titulo or sug.get("titulo") or "Corte da live").strip()
        desc_final = (descricao or sug.get("descricao") or "").strip()

        itens = self.listar()
        novos: list[dict[str, Any]] = []
        agora = time.time()
        for rede in [r.strip().lower() for r in redes if r.strip()]:
            if rede not in REDES_SUPORTADAS:
                raise ValueError(f"Rede invalida: {rede}")
            item = {
                "id": uuid.uuid4().hex[:12],
                "rede": rede,
                "via": via,
                "video": str(arq),
                "titulo": titulo_final,
                "descricao": desc_final,
                "privado": bool(privado),
                "dry_run": bool(dry_run),
                "estado": "pendente",
                "tentativas": 0,
                "proxima_tentativa_em": agora,
                "criado_em": agora,
                "link": None,
                "ultimo_erro": None,
            }
            itens.append(item)
            novos.append(item)
        self._salvar(itens)
        return novos

    def processar_pendentes(
        self,
        cfg: dict[str, Any] | None = None,
        emitir: EmissorEvento | None = None,
        sessao_http: requests.Session | None = None,
        dormir: Callable[[float], None] = time.sleep,
        agora: float | None = None,
    ) -> list[dict[str, Any]]:
        """Processa os itens cuja janela de tentativa chegou, reagendando com backoff se necessario."""
        cfg_efetiva = cfg or carregar_config_postar()
        ts_agora = time.time() if agora is None else agora
        tentativas_max = max(1, int(cfg_efetiva.get("tentativas_max", 3)))
        emissor = emitir or (lambda _ev: None)

        itens = self.listar()
        processados: list[dict[str, Any]] = []
        cache_postiz: dict[str, Any] = {}

        for item in itens:
            if item.get("estado") not in ("pendente", "aguardando_tentativa"):
                continue
            if float(item.get("proxima_tentativa_em") or 0) > ts_agora:
                continue

            rede = item["rede"]
            via_item = str(item.get("via") or cfg_efetiva.get("provedor_padrao") or "direto")
            arq = Path(item["video"])
            dry_run = bool(item.get("dry_run", False))

            if not dry_run:
                try:
                    self.limites.verificar_disponibilidade(rede, cfg_efetiva, agora=ts_agora)
                except ErroLimiteRede as exc:
                    item["estado"] = "aguardando_tentativa"
                    item["proxima_tentativa_em"] = ts_agora + exc.aguardar_s
                    item["ultimo_erro"] = str(exc)
                    emissor({"rede": rede, "etapa": "aguardando_tentativa", "pct": 0, "espera_s": round(exc.aguardar_s, 1)})
                    processados.append(dict(item))
                    continue

            # Na fila persistente fazemos 1 tentativa por rodada para nao travar outras redes
            cfg_uma_rodada = dict(cfg_efetiva)
            cfg_uma_rodada["tentativas_max"] = 1
            item["tentativas"] = int(item.get("tentativas", 0)) + 1

            final = enviar_uma_rede(
                rede=rede,
                caminho_video=arq,
                titulo=item["titulo"],
                descricao=item["descricao"],
                privado=bool(item.get("privado", False)),
                dry_run=dry_run,
                via=via_item,
                cfg=cfg_uma_rodada,
                emitir=emissor,
                diretorio_contas=self.diretorio_contas,
                limites=self.limites,
                sessao_http=sessao_http,
                dormir=dormir,
                agora=ts_agora,
                cache_postiz=cache_postiz,
            )

            if final.get("etapa") == "concluido":
                item["estado"] = "concluido"
                item["link"] = final.get("link")
                item["ultimo_erro"] = None
            else:
                erro_msg = str(final.get("erro") or "Falha no envio")
                item["ultimo_erro"] = erro_msg
                eh_transitorio = any(
                    termo in erro_msg.lower()
                    for termo in ("falha de rede", "http 429", "http 5", "tempo esgotado", "limite", "tentativas")
                )
                if eh_transitorio and item["tentativas"] < tentativas_max:
                    atraso = calcular_backoff(item["tentativas"], cfg_efetiva)
                    item["estado"] = "aguardando_tentativa"
                    item["proxima_tentativa_em"] = ts_agora + atraso
                else:
                    item["estado"] = "erro"
            processados.append(dict(item))

        self._salvar(itens)
        return processados
