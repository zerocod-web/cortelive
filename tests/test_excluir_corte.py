import json
from pathlib import Path

import pytest

from painel.motor_painel import ApiPainel, ErroApi


def test_exclusao_isola_o_video_e_preserva_fonte(tmp_path, monkeypatch):
    api = ApiPainel(tmp_path)
    sessao = tmp_path / "cortes" / "sessao"
    interno = sessao / "_interno"
    interno.mkdir(parents=True)
    video = sessao / "corte.mp4"
    fonte = sessao / "fonte.mp4"
    outro = sessao / "outro.mp4"
    for caminho in (video, fonte, outro):
        caminho.write_bytes(caminho.name.encode())
    meta = interno / "corte.json"
    meta.write_text(json.dumps({"arquivo": video.name, "duracao": 20}))
    (interno / "sessao.json").write_text(json.dumps({"fonte": str(fonte)}))
    from painel import lixeira
    reciclados = []
    monkeypatch.setattr(lixeira, "reciclar", lambda p: reciclados.append(p))
    for invalido in (fonte, outro, tmp_path / "fora.mp4", interno / "video.mp4"):
        with pytest.raises(ErroApi):
            api._rota("POST", "/api/cortes/excluir", {"caminho": str(invalido)})
    api._rota("POST", "/api/cortes/excluir", {"caminho": str(video)})
    assert reciclados == [video]
    assert all(p.is_file() for p in (fonte, outro, meta))
