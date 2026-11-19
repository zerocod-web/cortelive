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


def test_nao_quero_anota_o_corte_rejeitado(tmp_path, monkeypatch):
    """v1.0.17: "Não quero" e "Excluir os filtrados" guardam live, trecho e nota em rejeitados.jsonl."""
    api = ApiPainel(tmp_path)
    sessao = tmp_path / "cortes" / "2026-11-19 19h10 - coringa"
    (sessao / "_interno").mkdir(parents=True)
    video = sessao / "corte.mp4"
    video.write_bytes(b"x")
    (sessao / "_interno" / "corte.json").write_text(json.dumps({
        "arquivo": video.name, "nota_juiz": 4, "juiz": "chatgpt", "inicio_na_live": 658.8,
        "fim_na_live": 738.5, "inicio_hms": "00:10:58", "titulo_sugerido": "Traje novo"}), encoding="utf-8")
    (sessao / "_interno" / "sessao.json").write_text("{}")
    from painel import lixeira
    monkeypatch.setattr(lixeira, "reciclar", lambda p: p.unlink())
    api._rota("POST", "/api/cortes/excluir", {"caminho": str(video), "rejeitado": True, "origem": "nao_quero"})
    linhas = (tmp_path / "cortes" / "rejeitados.jsonl").read_text(encoding="utf-8").splitlines()
    d = json.loads(linhas[0])
    assert len(linhas) == 1 and not video.exists()
    assert (d["nota_juiz"], d["inicio_na_live"], d["fim_na_live"], d["origem"], d["canal"]) == (4, 658.8, 738.5, "nao_quero", "coringa")
