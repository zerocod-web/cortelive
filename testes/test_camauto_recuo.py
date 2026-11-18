import numpy as np
from cortelive import camauto, render


def _quadros(n=8):
    rng = np.random.default_rng(1)
    base = rng.integers(0, 255, (1080, 1920, 3), dtype=np.uint8)
    out = []
    for _ in range(n):
        q = base.copy()
        q[0:300, 0:465] = rng.integers(0, 255, (300, 465, 3), dtype=np.uint8)   # cam: muda a cada quadro
        out.append(q)
    return out


def test_icone_parado_nao_e_rosto():
    imgs = _quadros()
    assert camauto._rosto_parado(imgs, (1480, 823, 54, 54))        # fundo do jogo parado
    assert not camauto._rosto_parado(imgs, (120, 70, 120, 120))    # área da cam muda


def test_rosto_estavel_ignora_icone_e_acha_pessoa_que_se_mexe():
    imgs = _quadros()
    pessoa = [(181, 151, 104, 104), (145, 49, 159, 159), (138, 89, 156, 156), (89, 102, 138, 138),
              (117, 60, 97, 97), (172, 89, 96, 96)]
    achados = [[(1480, 823, 54, 54)] + ([pessoa[i]] if i < len(pessoa) else []) for i in range(8)]
    r = camauto._rosto_estavel(achados, 1920, 1080, imgs)
    assert r and r[0] < 400 and r[1] < 300       # a pessoa, não o ícone em (1507, 850)
    assert camauto._rosto_estavel(achados, 1920, 1080) [0] > 1400   # sem o filtro era o ícone (o bug)


def test_cam_em_cima_sem_cam_nunca_sai_cam_em_cima():
    m, motivo = render._modelo_efetivo("cam-em-cima", None, 1920, 1080)
    assert m["tipo"] == "fundo-desfocado" and "jogo-fundo-desfocado" in motivo
    m, motivo = render._modelo_efetivo("cam-em-cima", None, 1920, 1080, ["jogo-tela-cheia-crop", "jogo-fundo-desfocado"])
    assert m["tipo"] != "cam-em-cima" and "tela-cheia" in motivo
    m, motivo = render._modelo_efetivo("cam-em-cima", (0, 0, 465, 300), 1920, 1080)
    assert m["tipo"] == "cam-em-cima" and motivo is False
