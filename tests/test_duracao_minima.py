import tomllib
from pathlib import Path
import pytest
from cortelive import momento


def _cfg(modo):
    cfg = tomllib.loads((Path(momento.__file__).parent / "config.toml").read_text(encoding="utf-8-sig"))
    cfg["duracao_minima_modo"] = modo
    momento.ajustar_cortes_longos(cfg)
    return cfg


def _fala(ini, fim, passo=0.5, frase=8):
    out, t, k = [], ini, 0
    while t < fim:
        k += 1
        out.append({"w": "palavra" + ("." if k % frase == 0 else ""), "s": t, "e": t + passo * 0.8})
        t += passo
    return out


def _planeja(modo, ws, t, disp=400.0):
    sel = momento.Seletor(_cfg(modo)) if hasattr(momento, "Seletor") else None
    return sel


@pytest.mark.parametrize("modo,esperado", [("livre", 0), ("30", 30), ("45", 45), ("61", 61), (None, 0)])
def test_modo_piso(modo, esperado):
    assert momento.modo_piso({"duracao_minima_modo": modo} if modo else {}) == esperado


def test_compat_cortes_longos_antigo():
    assert momento.modo_piso({"cortes_longos": True}) == 61
    assert momento.modo_piso({"cortes_longos": True, "duracao_minima_modo": "30"}) == 30


def test_livre_nao_muda_o_cfg():
    cfg = {"duracao_min": 30, "duracao_max": 60, "antes_max": 18, "depois_do_momento": 14, "depois_min": 7,
           "duracao_minima_modo": "livre"}
    assert momento.ajustar_cortes_longos(dict(cfg)) == cfg


def test_degrau_30_tem_teto_abaixo_do_45():
    c = momento.cfg_degrau(_cfg("30"), 30)
    assert c["piso_s"] == 30 and c["duracao_max"] < 45
    c61 = momento.cfg_degrau(_cfg("30"), 61)
    assert c61["duracao_max"] == 90


def test_janela_do_degrau_30_respeita_piso_e_teto():
    ws = _fala(0, 40)
    j = momento.escolher_janela(15.0, ws, 100.0, momento.cfg_degrau(_cfg("30"), 30), min_ini=0.0, max_fim=100.0)
    assert not j.get("recusado")
    assert 30 <= j["fim"] - j["ini"] <= 44.5


def test_divide_em_duas_partes_quando_o_contexto_passa_de_90_s():
    ws = _fala(0, 170, passo=1.0, frase=8)
    j = momento.dividir_em_partes(60.0, ws, 200.0, _cfg("30"), min_ini=0.0, max_fim=200.0)
    assert j and len(j["partes"]) == 2
    p1, p2 = j["partes"]
    for p in (p1, p2):
        assert 30 <= p["fim"] - p["ini"] <= 90
    assert p1["fim"] <= p2["ini"]
    # cada parte termina/começa em fronteira de frase: a palavra antes do fim da parte 1 fecha frase
    ult = max((w for w in ws if w["e"] <= p1["fim"]), key=lambda w: w["e"])
    assert ult["w"].endswith(".")


def test_nao_divide_se_cada_parte_ficaria_abaixo_do_minimo():
    ws = _fala(0, 100, passo=1.0, frase=8)
    j = momento.dividir_em_partes(50.0, ws, 100.0, _cfg("61"), min_ini=0.0, max_fim=100.0)
    assert j is None
