"""v1.0.13: 'só cam' (tela inteira como câmera) não pode disparar com cam de canto grande nem com rosto de cena do jogo.

Números reais do brino (GTA 6, 1280x720, cam recortada sem fundo no canto inferior direito), log de 18/11/2026 09:29:
candidatos [[742, 262, 150, 197, 6], [1023, 549, 106, 134, 8]] -> a 1.0.12 devolveu (0, 0, 1280, 720)."""
import numpy as np

from cortelive import camauto

W, H = 1280, 720


def _jogo(n=8):
    """Quadros de jogo: a tela inteira muda entre um quadro e o outro."""
    rng = np.random.default_rng(1)
    return [rng.integers(0, 255, (H, W, 3), dtype=np.uint8) for _ in range(n)]


def _quarto(n=8):
    """Quadros de 'só cam': o quarto parado, só o miolo (a pessoa) muda."""
    base = np.full((H, W, 3), 90, np.uint8)
    out = []
    for k in range(n):
        im = base.copy()
        im[200:520, 500:780] = 40 + 20 * k
        out.append(im)
    return out


def test_brino_cena_do_jogo_e_cam_de_canto_nao_viram_tela_inteira():
    cands = [(742, 262, 150, 197, 6), (1023, 549, 106, 134, 8)]
    assert camauto._solo_cam(cands, W, H, 8, _jogo()) is None


def test_brino_so_a_cam_de_canto_grande_nao_vira_tela_inteira():
    # rosto grande (21% da altura) com o centro a 79% da largura e 73% da altura: é cam de canto
    assert camauto._solo_cam([(1008, 524, 120, 156, 8)], W, H, 8, _jogo()) is None


def test_rosto_grande_no_meio_de_jogo_em_movimento_nao_e_so_cam():
    # cena de cinema do jogo: rosto grande no meio, mas a tela toda muda
    assert camauto._solo_cam([(640, 330, 160, 200, 8)], W, H, 8, _jogo()) is None


def test_just_chatting_continua_tela_inteira():
    assert camauto._solo_cam([(640, 360, 160, 200, 8)], W, H, 8, _quarto()) == (0, 0, W, H)


def test_memoria_automatica_de_detector_antigo_e_refeita(tmp_path, monkeypatch):
    monkeypatch.setattr(camauto, "ARQ", tmp_path / "cams.json")
    (tmp_path / "cams.json").write_text(
        '{"brino": {"cam": [0, 0, 1280, 720], "origem": "automática"},'
        ' "fulano": {"cam": [1, 2, 300, 200], "origem": "marcada"}}', encoding="utf-8")
    assert camauto.da_memoria("brino") is None          # detecção de versão antiga: procura de novo
    assert camauto.da_memoria("fulano")["cam"] == [1, 2, 300, 200]   # marcada à mão vale sempre
    camauto.salvar("brino", (10, 20, 300, 200), "automática")
    assert camauto.da_memoria("brino")["cam"] == [10, 20, 300, 200]  # a nova detecção vale
