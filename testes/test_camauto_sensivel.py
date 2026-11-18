import numpy as np
from cortelive import camauto


def test_passada_sensivel_so_roda_sem_rosto_fixo(monkeypatch):
    img = np.zeros((1080, 1920, 3), np.uint8)
    img[:] = (120, 160, 220)                      # tom de pele
    imgs = [img + i * 6 for i in range(6)]
    chamadas = []
    monkeypatch.setattr(camauto, "_quadro", lambda a: a)
    monkeypatch.setattr(camauto, "rostos", lambda i: [])
    monkeypatch.setattr(camauto, "rostos_sensivel",
                        lambda i: chamadas.append(1) or [(200, 150, 100, 100)])
    monkeypatch.setattr(camauto, "_overlay", lambda *a: ((0, 40, 480, 280), [True] * 4, None))
    camauto.detectar(imgs)
    assert len(chamadas) == 6 and camauto.ULTIMO.get("rosto_sensivel")


def test_pele_e_borda():
    icone = np.zeros((60, 60, 3), np.uint8)
    icone[:] = (200, 60, 120)                      # roxo (ícone de habilidade): sem tom de pele
    rosto = np.zeros((60, 60, 3), np.uint8)
    rosto[:] = (120, 160, 220)                     # tom de pele (BGR)
    assert camauto._pele(icone) < camauto.PELE_MIN <= camauto._pele(rosto)
    assert camauto._na_borda((0, 40, 400, 250), 1920, 1080)
    assert not camauto._na_borda((608, 582, 190, 112), 1920, 1080)


def test_solo_cam_rosto_grande_e_central():
    cands = [(640.0, 300.0, 156.0, 208.0, 8)]
    assert camauto._solo_cam(cands, 1280, 720, 8) == (0, 0, 1280, 720)
    assert camauto._solo_cam([(1100.0, 100.0, 60.0, 80.0, 8)], 1280, 720, 8) is None      # cam pequena de canto
    assert camauto._solo_cam([(1250.0, 300.0, 156.0, 208.0, 8)], 1280, 720, 8) is None    # grande mas no canto
    assert camauto._solo_cam([(640.0, 300.0, 156.0, 208.0, 3)], 1280, 720, 8) is None     # poucos quadros


def test_candidatos_tolera_rosto_grande_que_se_mexe():
    lista = [[(441 + i * 20, 72 + i * 20, 219, 264)] for i in range(8)]
    c = camauto._candidatos(lista, 1280, 720)
    assert c and c[0][4] >= 5


def test_yunet_acha_rosto_e_caixa_estimada():
    assert camauto.MODELO_YUNET.exists()
    img = np.zeros((720, 1280, 3), np.uint8)
    assert camauto.rostos(img) == [] or len(camauto.rostos(img)) == 0
    x, y, w, h = camauto.caixa_estimada(1100.0, 150.0, 60.0, 80.0, 1280, 720)
    assert 0 <= x and x + w <= 1280 and 0 <= y and y + h <= 720 and w > 60 and h > 80


def test_grupo_sem_pele_nem_olhos_e_descartado_mas_neon_passa(monkeypatch):
    icone = np.zeros((720, 1280, 3), np.uint8)
    icone[:] = (200, 60, 120)
    imgs = [icone + i * 6 for i in range(6)]
    achados = [[(600, 600, 50, 50)] for _ in range(6)]
    assert all(not l for l in camauto._filtrar(achados, imgs))
    monkeypatch.setattr(camauto, "_olhos", lambda p: True)       # rosto sob neon: sem pele, mas com olhos
    assert all(l for l in camauto._filtrar(achados, imgs))
