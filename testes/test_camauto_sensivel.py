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
    img = np.zeros((720, 1280, 3), np.uint8)
    img[:] = (120, 160, 220)
    imgs = [img + i * 6 for i in range(8)]
    achados = [[(500 + i * 5, 140, 190, 190)] for i in range(8)]
    assert camauto._solo_cam(achados, 1280, 720, imgs) == (0, 0, 1280, 720)
    canto = [[(1100, 100, 60, 60)] for _ in range(8)]          # cam pequena de canto: não é só cam
    assert camauto._solo_cam(canto, 1280, 720, imgs) is None


def test_grupo_sem_pele_nem_olhos_e_descartado_mas_neon_passa(monkeypatch):
    icone = np.zeros((720, 1280, 3), np.uint8)
    icone[:] = (200, 60, 120)
    imgs = [icone + i * 6 for i in range(6)]
    achados = [[(600, 600, 50, 50)] for _ in range(6)]
    assert all(not l for l in camauto._filtrar(achados, imgs))
    monkeypatch.setattr(camauto, "_olhos", lambda p: True)       # rosto sob neon: sem pele, mas com olhos
    assert all(l for l in camauto._filtrar(achados, imgs))
