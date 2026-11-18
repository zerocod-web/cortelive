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
    assert motor_painel.config_postagem(tmp_path)["duracao_minima_modo"] == "61"      # padrão: mínimo 1 minuto
    (tmp_path / "painel_config.json").write_text(json.dumps({"duracao_minima_modo": "livre"}), encoding="utf-8")
    assert motor_painel.config_postagem(Path.cwd())["cortes_longos"] is False
    (tmp_path / "painel_config.json").write_text(json.dumps({"duracao_minima_modo": "45"}), encoding="utf-8")
    assert motor_painel.config_postagem(Path.cwd())["duracao_minima_modo"] == "45"


# --- v1.0.11: enquadramento "Câmera em cima" (jogo cheio sem cam duplicada, sem barra preta, faixa a 60%) ---
def test_janela_jogo_cheia_preenche_painel_e_exclui_cam():
    from cortelive import render
    for cam in ((1420, 46, 480, 284), (14, 46, 476, 282), (1498, 40, 396, 234)):   # cantos direito e esquerdo
        x, y, w, h, cobrir = render.janela_jogo_cheia(1920, 1080, 1080, 1280, cam)
        assert (y, h) == (0, 1080) and not cobrir
        assert w * 1280 >= 1080 * 1080 - 1280 * 2          # escalada para 1080 de largura, preenche >= 1280 de altura
        assert x + w <= cam[0] - 16 or x >= cam[0] + cam[2] + 16      # fora da caixa da cam
        assert 0 <= x and x + w <= 1920


def test_janela_jogo_cheia_cam_no_meio_cobre_em_vez_de_perder_o_centro():
    from cortelive import render
    x, y, w, h, cobrir = render.janela_jogo_cheia(1920, 1080, 1080, 1280, (700, 40, 520, 300))
    assert cobrir is True


def test_faixa_60_por_cento():
    from cortelive import render
    assert render.FAIXA_ALT == round(220 * 0.6)
    m = {"largura": 1080, "altura": 1920, "altura_cam": 640, "jogo": {"x": 0.0, "y": 0.0, "w": 0.4745, "h": 1.0}}
    g = render.geometria_faixa(m, 1920, 1080)
    assert g["blur"] <= 2 and g["h"] <= 140
