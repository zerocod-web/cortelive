import pytest
from cortelive import momento

def test_titulo_limpeza_conectores_iniciais():
    tit = momento.titulo_heuristico("E ver um negócio aqui pra testar agora", [])
    assert not tit.lower().startswith("e ")
    assert tit.lower().startswith("ver um negócio")

    tit2 = momento.titulo_heuristico("De casa comigo pra caralho porra", [])
    assert not tit2.lower().startswith("de ")

    tit3 = momento.titulo_heuristico("Que tá vendo a live também", [])
    assert not tit3.lower().startswith("que ")


def test_titulo_sem_clitico_cortado():
    ws = "O Brian quer que algo seja resolvido Encontre-se".split()
    limpo = momento._limpa_titulo(ws)
    assert not limpo[-1].endswith("-se")
    assert limpo[-1] == "resolvido"


def test_escolher_janela_gancho_imediato_sem_silencio_inicial():
    # Streamer em silêncio até t=20.0, fala em t=20.0 a 28.0, pico em t=23.0
    palavras = [
        {"s": 20.0, "e": 20.6, "w": "Nossa,"},
        {"s": 20.7, "e": 21.2, "w": "olha"},
        {"s": 21.3, "e": 21.8, "w": "isso"},
        {"s": 21.9, "e": 22.4, "w": "aqui!"},
        {"s": 22.6, "e": 23.5, "w": "Caralho!"},
        {"s": 24.0, "e": 25.5, "w": "Que"},
        {"s": 25.6, "e": 26.5, "w": "loucura"},
        {"s": 26.6, "e": 27.5, "w": "demais."},
    ]
    cfg = {
        "duracao_min": 30.0, "duracao_max": 60.0,
        "antes_max": 18.0, "antes_min": 6.0,
        "depois_do_momento": 14.0, "depois_min": 7.0,
        "buraco_max_s": 2.8, "cauda_max_s": 2.0,
        "cortes_longos": False
    }
    j = momento.escolher_janela(23.0, palavras, 100.0, cfg)
    # O início DEVE estar logo antes da primeira palavra (20.0s), nunca em 11s ou 5s com silêncio
    assert j["ini"] >= 19.5
    assert j["ini"] <= 20.0
    # O silêncio antes da fala é menor que 0.6s (gancho imediato)
    primeira = next(w for w in palavras if w["s"] >= j["ini"])
    assert primeira["s"] - j["ini"] <= 0.5


def test_escolher_janela_sem_cauda_morta():
    # Fala termina em 14.5s com ponto final. Sem fala depois.
    palavras = [
        {"s": 2.0, "e": 3.0, "w": "Caralho,"},
        {"s": 3.2, "e": 4.5, "w": "não"},
        {"s": 4.6, "e": 5.5, "w": "acredito"},
        {"s": 5.6, "e": 6.5, "w": "nisso!"},
        {"s": 7.0, "e": 8.0, "w": "O"},
        {"s": 8.1, "e": 9.5, "w": "carro"},
        {"s": 9.6, "e": 10.5, "w": "explodiu."},
        {"s": 11.0, "e": 12.0, "w": "Que"},
        {"s": 12.1, "e": 13.5, "w": "cena"},
        {"s": 13.6, "e": 14.5, "w": "linda."},
    ]
    cfg = {
        "duracao_min": 30.0, "duracao_max": 60.0,
        "antes_max": 18.0, "antes_min": 4.0,
        "depois_do_momento": 10.0, "depois_min": 5.0,
        "buraco_max_s": 2.8, "cauda_max_s": 2.0,
        "cortes_longos": False
    }
    j = momento.escolher_janela(6.0, palavras, 100.0, cfg)
    # Fim não deve ser empurrado artificialmente com 10s de silêncio morto
    ultima = [w for w in palavras if w["e"] <= j["fim"]][-1]
    assert j["fim"] - ultima["e"] <= 1.0


def test_dampening_volume_sem_fala():
    sin = momento.Sinais({"palavras_quentes": ["caralho"], "palavras_apoio": []})
    # Simula volume alto (vz=2.5) mas fala=0 e chat=0
    sin.db = {10: -10.0}
    # notas(ini, fim, palavras)
    res = sin.notas(10, 11, [])
    nota, mot, sinais = res[10]
    # Com peso_vol=0.55 quando fala=0 e chat=0, nota deve ser < limiar 2.2
    assert nota < 2.2
