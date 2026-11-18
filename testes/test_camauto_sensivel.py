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
