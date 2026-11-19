"""Filtro de quantidade/qualidade dos cortes: piso de nota (GPT 0-10 e seletor local) e teto por hora de live."""
import json
import sys
from pathlib import Path

import pytest

from cortelive import qualidade


def test_padrao_e_equilibrado():
    f = qualidade.Filtro({})
    assert f.nivel == "equilibrado"


@pytest.mark.parametrize("nivel,juiz,nota,ok", [
    ("equilibrado", "gpt", 6, False), ("equilibrado", "gpt", 7, True),
    ("melhores", "gpt", 7, False), ("melhores", "gpt", 8, True),
    ("mais", "gpt", 5, True), ("mais", "gpt", 4, False),
    ("equilibrado", "local", 2.4, False), ("equilibrado", "local", 2.6, True),
    ("mais", "local", 1.0, True),
])
def test_piso_de_nota(nivel, juiz, nota, ok):
    aprovado, motivo = qualidade.Filtro({"quantidade_cortes": nivel}).avaliar(nota, juiz, 100.0)
    assert aprovado is ok
    assert ok or "nota" in motivo


def test_teto_espalha_os_cortes_e_deixa_passar_o_excelente():
    f = qualidade.Filtro({"quantidade_cortes": "equilibrado"})     # 8 por hora: no máximo 2 a cada 15 min
    for t in (100.0, 200.0):
        assert f.avaliar(7, "gpt", t)[0]
        f.registrar(t)
    ok, motivo = f.avaliar(7, "gpt", 300.0)
    assert not ok and "limite" in motivo
    assert f.avaliar(8, "gpt", 300.0)[0]                            # nota de "Só os melhores" passa mesmo assim
    assert f.avaliar(7, "gpt", 100.0 + 901)[0]                     # 15 min depois abre vaga de novo


def test_teto_por_hora_nao_passa_nem_excelente():
    f = qualidade.Filtro({"quantidade_cortes": "melhores"})        # 4 por hora
    for k in range(4):
        f.registrar(k * 900.0)
    assert not f.avaliar(10, "gpt", 3000.0)[0]
    assert f.avaliar(10, "gpt", 3700.0)[0]


def test_nivel_invalido_volta_ao_padrao():
    assert qualidade.Filtro({"quantidade_cortes": "tudo"}).nivel == "equilibrado"


def _motor():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "painel"))
    import motor_painel
    return motor_painel


def test_painel_guarda_a_quantidade_de_cortes(tmp_path):
    motor = _motor()
    assert motor.config_postagem(tmp_path)["quantidade_cortes"] == "equilibrado"
    api = motor.ApiPainel(tmp_path)
    assert api.postagem("POST", {"quantidade_cortes": "melhores"})["quantidade_cortes"] == "melhores"
    assert json.loads((tmp_path / "painel_config.json").read_text(encoding="utf-8"))["quantidade_cortes"] == "melhores"
    with pytest.raises(motor.ErroApi):
        api.postagem("POST", {"quantidade_cortes": "tudo"})
