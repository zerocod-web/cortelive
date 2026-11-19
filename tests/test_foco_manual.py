"""Editor de foco (v1.0.16): marcação manual exata e conferência da cam automática em cada corte."""
import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from cortelive import render


def test_foco_valido_normaliza_e_recusa():
    f = render.foco_valido({"cam": [101, 51.4, 501, 297], "jogo": [3, 0, 999, 2000]}, 1920, 1080)
    assert f["cam"] == [102, 52, 502, 296] or all(v % 2 == 0 for v in f["cam"])
    assert all(v % 2 == 0 for v in f["jogo"])
    assert f["jogo"][1] + f["jogo"][3] <= 1080
    with pytest.raises(ValueError):
        render.foco_valido({"cam": [0, 0, 10, 10], "jogo": [0, 0, 500, 500]}, 1920, 1080)
    with pytest.raises(ValueError):
        render.foco_valido({"cam": [0, 0, 500, 300]}, 1920, 1080)


def test_filtro_foco_recorta_exatamente_as_caixas():
    m = dict(render.carregar("modelo", "cam-em-cima"))
    m["_foco_manual"] = {"cam": [380, 190, 960, 568], "jogo": [100, 190, 590, 700]}
    fc = render.filtro_modelo(m, 1920, 1080)
    assert "crop=960:568:380:190" in fc
    assert "crop=590:700:100:190" in fc
    assert fc.endswith("[v]")


def _imgs(n=10):
    return [np.zeros((1080, 1920, 3), np.uint8) for _ in range(n)]


def test_rever_cam_mantem_quando_ha_rosto(monkeypatch):
    from cortelive import camauto
    monkeypatch.setattr(render, "_quadros_do_trecho", lambda *a: _imgs())
    monkeypatch.setattr(camauto, "rostos", lambda img: [(1300, 100, 150, 150)])
    cam, motivo = render.rever_cam("x", 0, 60, (1238, 72, 414, 244), 1.6875)
    assert cam == (1238, 72, 414, 244) and motivo is None


def test_rever_cam_procura_de_novo_sem_rosto(monkeypatch):
    """gabepeixe 19/11/2026: a caixa veio do desenho da tela de abertura; no corte o rosto está em outro lugar."""
    from cortelive import camauto
    monkeypatch.setattr(render, "_quadros_do_trecho", lambda *a: _imgs())
    monkeypatch.setattr(camauto, "rostos", lambda img: [(900, 400, 260, 260)])
    monkeypatch.setattr(camauto, "detectar_imgs", lambda imgs, prop: (0, 0, 1920, 1080))
    cam, motivo = render.rever_cam("x", 0, 60, (1238, 72, 414, 244), 1.6875)
    assert cam == (0, 0, 1920, 1080) and "achei de novo" in motivo


def test_rever_cam_sem_nada_fica_sem_cam(monkeypatch):
    from cortelive import camauto
    monkeypatch.setattr(render, "_quadros_do_trecho", lambda *a: _imgs())
    monkeypatch.setattr(camauto, "rostos", lambda img: [])
    monkeypatch.setattr(camauto, "detectar_imgs", lambda imgs, prop: None)
    cam, motivo = render.rever_cam("x", 0, 60, (1238, 72, 414, 244), 1.6875)
    assert cam is None and motivo


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="sem ffmpeg")
def test_render_com_foco_e_ass_pronto(tmp_path):
    video = tmp_path / "fonte.mkv"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=30:duration=3",
                    "-f", "lavfi", "-i", "sine=duration=3", "-shortest", "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "aac", str(video)], check=True)
    ass = tmp_path / "antigo.ass"
    ass.write_text("[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n\n[V4+ Styles]\n"
                   "Format: Name, Fontname, Fontsize, PrimaryColour, BackColour, Bold, Italic, BorderStyle, Outline, "
                   "Shadow, Alignment, MarginL, MarginR, MarginV\nStyle: Default,Arial,60,&H00FFFFFF,&H00000000,0,0,1,"
                   "2,0,5,10,10,10\n\n[Events]\nFormat: Layer, Start, End, Style, Text\n"
                   "Dialogue: 0,0:00:00.00,0:00:02.00,Default,TESTE\n", encoding="utf-8")
    r = render.render_corte(str(video), 0.5, 2.5, [], "cam-em-cima", "opus", None, str(tmp_path / "out"),
                            "corte_001_000000", foco={"cam": [380, 190, 960, 568], "jogo": [100, 190, 590, 700]},
                            ass_pronto=str(ass))
    assert r["foco_manual"] and r["legenda_ok"]
    w, h = render.tamanho(r["mp4"])
    assert (w, h) == (1080, 1920)


def test_painel_rotas_foco_existem():
    from painel import motor_painel
    api = motor_painel.ApiPainel(Path(__file__).resolve().parents[1])
    st, d = api.responder("GET", "/api/foco/estado?caminho=nao_existe.mp4", {})
    assert st == 404 and d["ok"] is False          # rota existe; o corte é que não
    st, d = api.responder("POST", "/api/foco/refazer", {"caminho": "nao_existe.mp4", "foco": {}})
    assert st == 404


def test_memoria_automatica_so_vale_na_mesma_sessao(tmp_path, monkeypatch):
    """Kick VOD gabepeixe 19/11/2026: a cam automática de uma rodada anterior era usada sem conferir."""
    from cortelive import camauto
    monkeypatch.setattr(camauto, "ARQ", tmp_path / "cams.json")
    camauto.salvar("01a1f40d", (1238, 72, 414, 244), "automática", "sessao A")
    assert camauto.da_memoria("01a1f40d", "sessao A")["cam"] == [1238, 72, 414, 244]
    assert camauto.da_memoria("01a1f40d", "sessao B") is None
    assert camauto.da_memoria("01a1f40d") is None
    camauto.salvar("gaules", (0, 0, 400, 300), "marcada")
    assert camauto.da_memoria("gaules", "qualquer")["cam"] == [0, 0, 400, 300]
