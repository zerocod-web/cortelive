"""Foco da live conferido no começo: proposta, resposta do painel, memória por canal e rotas do painel."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from cortelive import foco_live

FOCO = {"cam": [880, 40, 360, 216], "jogo": [100, 200, 700, 470]}


@pytest.fixture(autouse=True)
def memoria(tmp_path, monkeypatch):
    monkeypatch.setattr(foco_live, "ARQ_CANAIS", tmp_path / "focos.json")


@pytest.fixture
def segmento(tmp_path):
    # pedaço .ts como os da captura: o ffprobe lista o tamanho duas vezes (programa e stream)
    seg = tmp_path / "r1_000000.ts"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=30:duration=2",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-f", "mpegts", str(seg)], check=True)
    return seg


def _sessao(raiz, nome="2026-11-19 20h00 - canalx"):
    dint = raiz / "cortes" / nome / "_interno"
    dint.mkdir(parents=True)
    return dint


def test_live_nova_pede_conferencia_e_segue_no_automatico(tmp_path, segmento):
    dint = _sessao(tmp_path)
    d = foco_live.propor(dint, "CanalX", segmento, cam=(900, 30, 300, 180))
    assert d["estado"] == "pendente" and d["tamanho"] == [1280, 720] and d["proposta"]["cam"] == [900, 30, 300, 180]
    assert (dint / foco_live.QUADRO).stat().st_size > 1000
    assert foco_live.foco_ativo(dint) is None                      # sem resposta: automático
    assert [p["canal"] for p in foco_live.pendentes(tmp_path / "cortes")] == ["CanalX"]


def test_confirmar_vale_para_a_live_e_para_a_proxima_do_canal(tmp_path, segmento):
    dint = _sessao(tmp_path)
    foco_live.propor(dint, "CanalX", segmento)
    foco_live.responder(dint, FOCO, lembrar_canal=True)
    assert foco_live.foco_ativo(dint) == FOCO
    assert foco_live.pendentes(tmp_path / "cortes") == []
    dint2 = _sessao(tmp_path, "2026-11-20 20h00 - canalx")
    d2 = foco_live.propor(dint2, "canalx", segmento)              # próxima live: já vem com o foco salvo
    assert d2["estado"] == "salvo" and d2["origem"] == "salvo do canal" and d2["proposta"] == FOCO
    assert foco_live.foco_ativo(dint2) == FOCO
    assert foco_live.esquecer("CanalX") is True
    assert foco_live.canais_salvos() == []
    dint3 = _sessao(tmp_path, "2026-11-21 20h00 - canalx")
    assert foco_live.propor(dint3, "canalx", segmento)["estado"] == "pendente"


def test_sem_lembrar_e_automatico(tmp_path, segmento):
    dint = _sessao(tmp_path)
    foco_live.propor(dint, "CanalX", segmento)
    foco_live.responder(dint, FOCO, lembrar_canal=False)
    assert foco_live.canais_salvos() == []
    foco_live.responder(dint, None)
    assert foco_live.foco_ativo(dint) is None


def test_foco_salvo_de_outro_tamanho_de_tela_nao_vale(tmp_path, segmento):
    foco_live.lembrar("canalx", FOCO, [1920, 1080])
    dint = _sessao(tmp_path)
    assert foco_live.propor(dint, "canalx", segmento)["estado"] == "pendente"


def test_rotas_do_painel(tmp_path, segmento):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "painel"))
    import motor_painel
    api = motor_painel.ApiPainel(tmp_path)
    dint = _sessao(tmp_path)
    foco_live.propor(dint, "CanalX", segmento)
    st, r = api.responder("GET", "/api/foco-live/pendente", {})
    assert st == 200 and r["pendentes"][0]["sessao"] == "2026-11-19 20h00 - canalx" and r["proporcoes"]["cam"] > 1
    st, jpg = api.responder("GET", "/api/foco-live/quadro?sessao=2026-11-19%2020h00%20-%20canalx", {})
    assert st == 200 and jpg[:2] == b"\xff\xd8"
    st, r = api.responder("POST", "/api/foco-live/confirmar",
                          {"sessao": "2026-11-19 20h00 - canalx", "foco": FOCO, "lembrar": True})
    assert st == 200 and "próximas lives" in r["mensagem"]
    assert json.loads((dint / foco_live.ARQ).read_text(encoding="utf-8"))["estado"] == "confirmado"
    st, r = api.responder("GET", "/api/foco-live/canais", {})
    assert [c["canal"] for c in r["canais"]] == ["canalx"]
    st, r = api.responder("POST", "/api/foco-live/esquecer", {"canal": "canalx"})
    assert r["esquecido"] is True
    st, r = api.responder("POST", "/api/foco-live/confirmar", {"sessao": "../fora", "foco": None})
    assert st == 404
