import os
from urllib.error import HTTPError

import pytest

from cortelive import gpt, momento


def escolher(monkeypatch, avaliar):
    seletor = momento.Seletor({})
    monkeypatch.setattr(seletor, "planejar", lambda *a: ({"fim": 18}, "", False))
    decisoes = []
    notas = {s: (5.0 if 8 <= s <= 12 else 0, [], {}) for s in range(40)}
    seletor.passo(notas, [{"w": "gol!", "s": 9, "e": 10}], 40, 39, 39, 4,
                  [], lambda d: decisoes.append(d) or True, final=True, juiz=avaliar)
    return decisoes


@pytest.mark.parametrize("valor,aceito", [(10, True), (0, False)])
def test_gpt_muda_escolha(monkeypatch, valor, aceito):
    monkeypatch.setattr(gpt, "ler", lambda: {"modo": "chatgpt"})
    monkeypatch.setattr(gpt, "pontuar", lambda conf, fala: valor if fala == "gol!" else None)
    decisoes = escolher(monkeypatch, gpt.Juiz(print).avaliar)
    assert bool(decisoes) is aceito
    if aceito:
        assert decisoes[0]["nota"] > 5
        assert decisoes[0]["sinais"] == {"nota_gpt": 10, "juiz": "chatgpt"}


@pytest.mark.parametrize("resposta", [429, 503, "vazia"])
def test_falha_retomada_local(monkeypatch, resposta):
    monkeypatch.setattr(gpt, "ler", lambda: {"modo": "api", "chave": "simulada"})
    chamadas = []
    def indisponivel(req, timeout):
        chamadas.append(1)
        if isinstance(resposta, int):
            raise HTTPError(req.full_url, resposta, "simulado", {}, None)
        from io import BytesIO
        return BytesIO(b'{"output":[]}')
    monkeypatch.setattr(gpt, "urlopen", indisponivel)
    avisos = []
    juiz = gpt.Juiz(avisos.append)
    for _ in range(2):
        decisoes = escolher(monkeypatch, juiz.avaliar)
        assert decisoes[0]["nota"] == 5 and decisoes[0]["sinais"] == {}
    assert len(chamadas) == len(avisos) == 1


@pytest.mark.skipif(os.name != "nt", reason="DPAPI")
def test_api_chave_e_ranking(monkeypatch, tmp_path):
    from io import BytesIO
    import json
    chave = "sk-" + "teste-local-sem-valor"
    monkeypatch.setattr(gpt, "arquivo", lambda: tmp_path / "credencial.bin")
    chamadas = []
    def simular(req, timeout):
        assert req.get_header("Authorization") == "Bearer " + chave
        chamadas.append(req.full_url)
        if req.data is None:
            return BytesIO(b'{"id":"teste"}')
        assert json.loads(req.data)["input"] == "gol!"
        return BytesIO(b'{"output":[{"content":[{"type":"output_text","text":"9"}]}]}')
    monkeypatch.setattr(gpt, "urlopen", simular)
    status = gpt.conectar("conectar", {"modo": "api", "chave": chave}, {})
    assert status == {"modo": "api", "estado": "salva"}
    assert chave.encode() not in gpt.arquivo().read_bytes()
    assert gpt.conectar("status", {}, {}) == status
    decisao, = escolher(monkeypatch, gpt.Juiz(print).avaliar)
    assert decisao["sinais"]["nota_gpt"] == 9
    assert len(chamadas) == 2
