"""v1.0.13: só faixa preta simétrica (jogo 4:3 de verdade) vira janela do jogo; cena escura de um lado não."""
import numpy as np

from cortelive import render


def _cols(esq, dir_, W=480):
    ok = np.ones(W, bool)
    ok[:esq] = False
    if dir_:
        ok[W - dir_:] = False
    return ok


def test_gaules_4x3_faixas_simetricas_vira_janela():
    # gaules 1280x720 medido em 480 colunas: faixas de 60 e 62 colunas
    assert render._corrida_jogo(_cols(60, 62)) == (60, 418)


def test_ovotz_noite_escura_so_de_um_lado_nao_e_4x3():
    # ovotz 1920: imagem de 4 a 1704 px -> em 480 colunas, 1 à esquerda e 54 à direita
    assert render._corrida_jogo(_cols(1, 54)) is None


def test_stepbr12_inventario_escuro_assimetrico_nao_e_4x3():
    assert render._corrida_jogo(_cols(153, 84)) is None


def test_tela_cheia_continua_none():
    assert render._corrida_jogo(_cols(0, 0)) is None
