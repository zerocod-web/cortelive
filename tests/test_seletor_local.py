"""Seletor local (sem GPT) no modo do painel 'mínimo 1 minuto': contexto antes do pico e momento que espera a fala
de depois em vez de ser descartado."""
import tomllib
from pathlib import Path

from cortelive import momento


def _cfg(modo="61"):
    cfg = tomllib.loads((Path(momento.__file__).parent / "config.toml").read_text(encoding="utf-8-sig"))
    cfg["duracao_minima_modo"] = modo
    return momento.ajustar_cortes_longos(cfg)


def _gameplay(inicio, fim, pausa_cada=8, pausa=3.5):
    """Fala de quem joga: frases de ~4 s fechando com ponto e, a cada 'pausa_cada' frases, um silêncio de jogo."""
    pal, t, n = [], inicio, 0
    while t < fim:
        n += 1
        for k in range(8):
            pal.append({"w": "fim." if k == 7 else ("Palavra" if k == 0 else "palavra"), "s": t, "e": t + 0.4})
            t += 0.5
        t += pausa if n % pausa_cada == 0 else 0.7
    return pal


def test_pausa_de_jogo_nao_tira_o_contexto_de_antes_do_pico():
    # silêncios de 3,5 s a cada ~30 s de fala (jogando): o início pode passar deles para dar contexto
    cfg = _cfg()
    pal = _gameplay(0.0, 300.0, pausa_cada=2)
    sel = momento.Seletor(cfg)
    j, porque, _ = sel.planejar(150.0, 3.0, {}, pal, 300.0, [])
    assert j is not None, porque
    assert j["fim"] - j["ini"] >= 61
    assert j["ini"] <= 150.0 - 15.0


def _notas(pico, ate):
    return {s: ((4.0 if pico - 1 <= s <= pico + 1 else 0.0), ["volume alto"], {"volume": 2.0}) for s in range(ate)}


def test_momento_espera_a_fala_de_depois_em_vez_de_ser_descartado():
    cfg = _cfg()
    # 12 s de silêncio de verdade logo antes do pico: o corte só pode começar perto dele
    pal = _gameplay(0.0, 180.0) + _gameplay(195.0, 400.0)
    sel = momento.Seletor(cfg)
    decisoes = []
    pico = 200
    # ~44 s depois do pico (quando a sessão avalia hoje): 61 s ainda não cabem
    disp = pico + cfg["depois_do_momento"] + 4
    vis = [w for w in pal if w["e"] <= disp]
    limite = disp - cfg["depois_do_momento"] - 2
    sel.passo(_notas(pico, int(disp)), vis, disp, limite, int(disp) - 1, cfg["limiar_nota"], [],
              lambda d: decisoes.append(d) or True)
    assert not decisoes
    disp = pico + 75
    vis = [w for w in pal if w["e"] <= disp]
    limite = disp - cfg["depois_do_momento"] - 2
    sel.passo(_notas(pico, int(disp)), vis, disp, limite, int(disp) - 1, cfg["limiar_nota"], [],
              lambda d: decisoes.append(d) or True)
    assert len(decisoes) == 1
    j = decisoes[0]["janela"]
    assert j["fim"] - j["ini"] >= 61 and j["ini"] <= pico


def test_livre_continua_decidindo_na_hora():
    cfg = _cfg("livre")
    pal = _gameplay(0.0, 400.0)
    sel = momento.Seletor(cfg)
    decisoes = []
    pico = 200
    disp = pico + cfg["depois_do_momento"] + 4
    vis = [w for w in pal if w["e"] <= disp]
    sel.passo(_notas(pico, int(disp)), vis, disp, disp - cfg["depois_do_momento"] - 2, int(disp) - 1,
              cfg["limiar_nota"], [], lambda d: decisoes.append(d) or True)
    assert len(decisoes) == 1
