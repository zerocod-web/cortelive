"""Aprender com o "Não quero" (v1.0.17): ajusta a nota do GPT de um corte novo pelo que o Gabriel rejeitou antes.

Regra simples e explicável:
- Exemplos: os cortes rejeitados (rejeitados.jsonl, gravado pelo painel) e os cortes mantidos na pasta
  (com nota do GPT e mais de MANTIDO_APOS_H horas, para dar tempo de ele olhar).
- Escopo: o canal, quando ele tem pelo menos MIN_REJEITADOS rejeições; senão, todos os canais; com menos que
  isso no total, não ajusta nada.
- "Parecido" = nota do GPT a até 1 ponto da nota do corte novo. Com menos de MIN_PARECIDOS parecidos, não ajusta.
- Ajuste = (taxa de rejeição geral do escopo - taxa dos parecidos) x 4, arredondado e limitado a ±MAX_AJUSTE.
  Ex.: o canal rejeita 40% no geral e 90% dos cortes nota 4-6 -> nota 5 vira 3 (-2).
- Exclusão em lote pelo filtro de nota conta meio exemplo (ela já é uma escolha por nota, não pelo corte).
Usa sempre a nota ORIGINAL do GPT dos exemplos, para o ajuste não se realimentar.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

MIN_REJEITADOS = 15
MIN_PARECIDOS = 6
MAX_AJUSTE = 2
MANTIDO_APOS_H = 2.0
ARQUIVO = "rejeitados.jsonl"
PESO = {"nao_quero": 1.0, "filtro_nota": 0.5}


def _nota(v):
    return v if type(v) is int and 0 <= v <= 10 else None


@dataclass
class Base:
    exemplos: list = field(default_factory=list)      # (canal, nota_original, peso, rejeitado: bool)
    n_rejeitados: int = 0

    @classmethod
    def carregar(cls, pasta_cortes, agora=None) -> "Base":
        pasta = Path(pasta_cortes)
        agora = time.time() if agora is None else agora
        b = cls()
        rejeitados_arq = set()
        try:
            linhas = (pasta / ARQUIVO).read_text(encoding="utf-8").splitlines()
        except OSError:
            linhas = []
        for linha in linhas:
            try:
                d = json.loads(linha)
            except ValueError:
                continue
            n = _nota(d.get("nota_gpt_original", d.get("nota_juiz")))
            if n is None or d.get("juiz") not in ("api", "chatgpt"):
                continue
            b.exemplos.append(((d.get("canal") or "").lower(), n, PESO.get(d.get("origem"), 1.0), True))
            b.n_rejeitados += 1
            rejeitados_arq.add(d.get("arquivo"))
        if pasta.is_dir():
            for meta in pasta.glob("*/_interno/*.json"):
                if meta.name == "sessao.json":
                    continue
                try:
                    if agora - meta.stat().st_mtime < MANTIDO_APOS_H * 3600:
                        continue
                    d = json.loads(meta.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if not isinstance(d, dict) or d.get("juiz") not in ("api", "chatgpt") or d.get("arquivo") in rejeitados_arq:
                    continue
                if not (meta.parent.parent / str(d.get("arquivo"))).is_file():
                    continue
                sinais = d.get("sinais") if isinstance(d.get("sinais"), dict) else {}
                n = _nota(sinais.get("nota_gpt_original", d.get("nota_juiz")))
                if n is None:
                    continue
                from .pastas import canal_da_pasta
                canal = (canal_da_pasta(meta.parent.parent.name) or "").lower()
                b.exemplos.append((canal, n, 1.0, False))
        return b

    def ajuste(self, canal, nota) -> tuple[int, str | None]:
        """(delta, explicação) para a nota do GPT de um corte novo deste canal."""
        if _nota(nota) is None:
            return 0, None
        canal = (canal or "").lower()
        do_canal = [e for e in self.exemplos if e[0] == canal]
        if sum(1 for e in do_canal if e[3]) >= MIN_REJEITADOS:
            escopo, onde = do_canal, f"no canal {canal}"
        elif self.n_rejeitados >= MIN_REJEITADOS:
            escopo, onde = self.exemplos, "em todos os canais"
        else:
            return 0, None
        parecidos = [e for e in escopo if abs(e[1] - nota) <= 1]
        if len(parecidos) < MIN_PARECIDOS:
            return 0, None

        def taxa(xs):
            total = sum(e[2] for e in xs)
            return sum(e[2] for e in xs if e[3]) / total if total else 0.0

        geral, local = taxa(escopo), taxa(parecidos)
        delta = max(-MAX_AJUSTE, min(MAX_AJUSTE, round((geral - local) * 4)))
        if delta == 0:
            return 0, None
        return delta, (f"cortes com nota {max(0, nota - 1)}-{min(10, nota + 1)} {onde}: {local:.0%} rejeitados "
                       f"(média {geral:.0%}, {len(parecidos)} exemplos)")

    def aplicar(self, canal, sinais: dict) -> dict:
        """Devolve os sinais com a nota do GPT ajustada (e a original guardada), ou os mesmos sinais."""
        n = _nota((sinais or {}).get("nota_gpt"))
        if n is None or "nota_gpt_original" in sinais:
            return sinais
        delta, porque = self.ajuste(canal, n)
        if not delta:
            return sinais
        return {**sinais, "nota_gpt": max(0, min(10, n + delta)), "nota_gpt_original": n,
                "ajuste_aprendizado": {"delta": delta, "motivo": porque}}


def status(pasta_cortes) -> dict:
    b = Base.carregar(pasta_cortes)
    return {"rejeitados": b.n_rejeitados, "minimo": MIN_REJEITADOS, "ativo": b.n_rejeitados >= MIN_REJEITADOS,
            "max_ajuste": MAX_AJUSTE}


def zerar(pasta_cortes) -> str | None:
    """Guarda o arquivo atual com outro nome (dá para voltar) e recomeça do zero. Devolve o nome guardado."""
    arq = Path(pasta_cortes) / ARQUIVO
    if not arq.is_file():
        return None
    guardado = arq.with_name(f"rejeitados.zerado-{time.strftime('%Y%m%d-%H%M%S')}.jsonl")
    arq.replace(guardado)
    return guardado.name
