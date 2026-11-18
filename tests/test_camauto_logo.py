import numpy as np
from cortelive import camauto


def test_logo_so_com_olho_ocasional_nao_e_camera(monkeypatch):
    """Grupo sem tom de pele e com 'olhos' em só 1 de 8 quadros (letras de um logo) é rejeitado."""
    h, w = 400, 600
    imgs = [np.full((h, w, 3), 40, np.uint8) for _ in range(8)]
    for k, im in enumerate(imgs):
        im[100:160, 100:200] = 30 + k * 12          # muda um pouco: não é "parado"
    face = (100, 100, 100, 60)
    achados = [[face] for _ in imgs]
    chamadas = {"n": 0}

    def olhos(p):
        chamadas["n"] += 1
        return chamadas["n"] == 1
    monkeypatch.setattr(camauto, "_olhos", olhos)
    monkeypatch.setattr(camauto, "_pele", lambda p: 0.0)
    assert camauto._filtrar(achados, imgs, minimo=0.2) == [[] for _ in imgs]


def test_rosto_sob_neon_com_olhos_na_maioria_passa(monkeypatch):
    h, w = 400, 600
    imgs = [np.full((h, w, 3), 40, np.uint8) for _ in range(8)]
    for k, im in enumerate(imgs):
        im[100:160, 100:200] = 30 + k * 12
    face = (100, 100, 100, 60)
    monkeypatch.setattr(camauto, "_olhos", lambda p: True)
    monkeypatch.setattr(camauto, "_pele", lambda p: 0.0)
    assert all(len(l) == 1 for l in camauto._filtrar([[face] for _ in imgs], imgs, minimo=0.2))
