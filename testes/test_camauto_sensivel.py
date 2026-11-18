import numpy as np
from cortelive import camauto


def test_passada_sensivel_so_roda_sem_rosto_fixo(monkeypatch):
    img = np.zeros((1080, 1920, 3), np.uint8)
    imgs = [img + i for i in range(6)]
    chamadas = []
    monkeypatch.setattr(camauto, "_quadro", lambda a: a)
    monkeypatch.setattr(camauto, "rostos", lambda i: [])
    monkeypatch.setattr(camauto, "rostos_sensivel",
                        lambda i: chamadas.append(1) or [(200, 150, 100, 100)])
    monkeypatch.setattr(camauto, "_overlay", lambda *a: ((0, 40, 480, 280), [True] * 4, None))
    camauto.detectar(imgs)
    assert len(chamadas) == 6 and camauto.ULTIMO.get("rosto_sensivel")
