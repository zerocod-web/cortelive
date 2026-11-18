"""Regra dos 61 s ('Só cortes de mais de 1 minuto'): piso, teto, descarte sem esticar e opção desligada intacta."""
import tomllib
from pathlib import Path

from cortelive import momento


def _cfg(**extra):
    cfg = tomllib.loads((Path(momento.__file__).parent / "config.toml").read_text(encoding="utf-8-sig"))
    cfg.update(extra)
    return cfg


def _fala(inicio, fim, passo=0.5):
    """Palavras seguidas; a cada ~6 s uma frase fecha com ponto e pausa de 0,7 s."""
    pal, t, n = [], inicio, 0
    while t + passo <= fim:
        n += 1
        fecha = n % 12 == 0
        pal.append({"w": "fim." if fecha else "Palavra", "s": t, "e": t + 0.4})
        t += passo + (0.7 if fecha else 0.0)
    return pal


def test_desligado_nada_muda():
    cfg = _cfg()
    antes = dict(cfg)
    assert momento.ajustar_cortes_longos(cfg) == antes
    assert momento.duracao_minima(cfg) == cfg["duracao_piso_frac"] * cfg["duracao_min"]
    assert momento.duracao_maxima(cfg) == cfg["duracao_max"]


def test_ligado_piso_61_e_teto_90_se_o_atual_for_menor():
    cfg = momento.ajustar_cortes_longos(_cfg(cortes_longos=True))
    assert momento.duracao_minima(cfg) == 61.0
    assert cfg["duracao_min"] >= 61.0
    assert cfg["duracao_max"] == 90.0          # atual era 60 (< 61)


def test_ligado_teto_atual_mantido_se_ja_for_maior():
    cfg = momento.ajustar_cortes_longos(_cfg(cortes_longos=True, duracao_max=120))
    assert cfg["duracao_max"] == 120


def test_corte_tem_pelo_menos_61s_e_termina_em_frase():
    cfg = momento.ajustar_cortes_longos(_cfg(cortes_longos=True))
    pal = _fala(0.0, 200.0)
    j = momento.escolher_janela(80.0, pal, 200.0, cfg)
    assert not j.get("recusado")
    assert 61.0 <= j["fim"] - j["ini"] <= cfg["duracao_max"] + 1e-6
    ultima = max((w for w in pal if w["e"] <= j["fim"]), key=lambda w: w["e"])
    assert ultima["w"].endswith(".")           # fecha em frase completa


def test_trecho_curto_e_descartado_nunca_esticado():
    cfg = momento.ajustar_cortes_longos(_cfg(cortes_longos=True))
    pal = _fala(60.0, 100.0)                  # só 40 s de fala disponível
    j = momento.escolher_janela(80.0, pal, 100.0, cfg)
    assert j.get("recusado") is True


def test_desligado_mantem_comportamento_antigo_curto_nao_recusa():
    cfg = _cfg()
    pal = _fala(60.0, 100.0)
    j = momento.escolher_janela(80.0, pal, 100.0, cfg)
    assert not j.get("recusado")
    assert j["fim"] - j["ini"] < 61.0


def test_live_adicionada_depois_le_a_chave_de_novo(tmp_path, monkeypatch):
    """A chave é lida a cada live nova (não só quando o motor abre): ligar com a sessão rodando vale para a próxima live."""
    import json
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "painel"))
    import motor_painel
    monkeypatch.chdir(tmp_path)
    assert motor_painel.config_postagem(tmp_path)["cortes_longos"] is False
    (tmp_path / "painel_config.json").write_text(json.dumps({"cortes_longos": True}), encoding="utf-8")
    assert motor_painel.config_postagem(Path.cwd())["cortes_longos"] is True
