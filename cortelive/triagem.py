"""Cortes repetidos (v1.0.17): dois cortes da mesma live que dividem mais da metade do tempo viram um só,
o de nota maior, antes do render."""
from __future__ import annotations

LIMITE = 0.5


def sobreposicao(a_ini, a_fim, b_ini, b_fim) -> float:
    """Fração do corte MAIS CURTO que está dentro do outro (0 a 1)."""
    comum = min(a_fim, b_fim) - max(a_ini, b_ini)
    menor = min(a_fim - a_ini, b_fim - b_ini)
    return max(0.0, comum) / menor if menor > 0 else 0.0


def valor(corte: dict) -> float:
    """Nota usada para decidir: a do GPT (0-10) quando há; senão a pontuação local (mesma escala de 0-10)."""
    sinais = corte.get("sinais") if isinstance(corte.get("sinais"), dict) else {}
    for v in (sinais.get("nota_gpt"), corte.get("nota_juiz")):
        if type(v) is int:
            return float(v)
    return float(corte.get("nota") or corte.get("pontuacao") or 0.0)


def _tempos(corte: dict):
    if "ini" in corte:
        return corte["ini"], corte["fim"]
    return corte.get("inicio_na_live"), corte.get("fim_na_live")


def repetido(novo: dict, outros) -> dict | None:
    """O corte de 'outros' que mais se sobrepõe ao novo, se passar do LIMITE; senão None."""
    a, b = _tempos(novo)
    melhor, frac_max = None, LIMITE
    for o in outros:
        oa, ob = _tempos(o)
        if not isinstance(oa, (int, float)) or not isinstance(ob, (int, float)):
            continue
        f = sobreposicao(a, b, oa, ob)
        if f > frac_max:
            melhor, frac_max = o, f
    return melhor
