import subprocess
from pathlib import Path

import pytest

from cortelive import gpt


def rodar_codex(monkeypatch, atual):
    """Executa _consultar com o Codex simulado e devolve os argumentos passados ao 'codex exec'."""
    vistos = []

    def run(args, **kw):
        if args[1:3] == ["login", "status"]:
            return subprocess.CompletedProcess(args, 0, "Logged in using ChatGPT", "")
        vistos.append(args)
        Path(args[args.index("--output-last-message") + 1]).write_text("null", encoding="utf-8")
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(gpt, "localizar_codex", lambda: "codex.exe")
    monkeypatch.setattr(gpt.subprocess, "run", run)
    gpt._consultar(atual, "instrução", "contexto")
    return vistos[0]


@pytest.mark.parametrize("atual,esperado", [
    ({"modo": "chatgpt"}, "gpt-6-luna"),                         # conexão antiga, sem modelo salvo: Luna
    ({"modo": "chatgpt", "modelo": "gpt-6.2-sol"}, "gpt-6.2-sol"),
    ({"modo": "chatgpt", "modelo": "inventado"}, "gpt-6-luna"),
])
def test_modelo_do_codex(monkeypatch, atual, esperado):
    args = rodar_codex(monkeypatch, atual)
    assert args[args.index("-m") + 1] == esperado
    assert 'model_reasoning_effort="low"' in args


def test_conectar_salva_modelo_e_recusa_desconhecido(monkeypatch):
    salvos = []
    monkeypatch.setattr(gpt, "localizar_codex", lambda: "codex.exe")
    monkeypatch.setattr(gpt, "sessao", lambda cli, env: "conectado")
    monkeypatch.setattr(gpt, "salvar", salvos.append)
    r = gpt.conectar("conectar", {"modo": "chatgpt", "modelo": "gpt-6.1-terra"}, {})
    assert r["modelo"] == "gpt-6.1-terra" and salvos == [{"modo": "chatgpt", "modelo": "gpt-6.1-terra"}]
    assert gpt.conectar("conectar", {"modo": "chatgpt"}, {})["modelo"] == "gpt-6-luna"
    with pytest.raises(gpt.FalhaGPT):
        gpt.conectar("conectar", {"modo": "chatgpt", "modelo": "gpt-x"}, {})
    monkeypatch.setattr(gpt, "ler", lambda: {"modo": "chatgpt", "modelo": "gpt-6.2-sol"})
    assert gpt.conectar("status", {}, {})["modelo"] == "gpt-6.2-sol"


def test_ajuste_de_nota_so_na_conexao_chatgpt(monkeypatch):
    monkeypatch.setitem(gpt.MODELOS_CODEX["gpt-6-luna"], "ajuste_nota", 1)
    assert gpt.ajuste_nota({"modo": "chatgpt"}) == 1
    assert gpt.ajuste_nota({"modo": "chatgpt", "modelo": "gpt-6.2-sol"}) == 0
    assert gpt.ajuste_nota({"modo": "api", "chave": "x"}) == 0
    fs = [{"p": [{"w": f"frase{i}.", "s": i * 5.0, "e": i * 5.0 + 4.5}], "s": i * 5.0, "e": i * 5.0 + 4.5,
           "cont": False} for i in range(12)]
    pedido = {"fs": fs, "disponivel": 200.0, "abre0": True, "min": 10.0, "alvo": 30.0, "max": 60.0,
              "modo": "chatgpt", "ajuste_nota": 1}
    juiz = gpt.Juiz(print)
    resposta = {"primeiro": 2, "ultimo": 8, "momento": 5}
    assert juiz._interpretar({**resposta, "nota": 7}, pedido, 0.0, True)["nota"] == 8
    assert juiz._interpretar({**resposta, "nota": 10}, pedido, 0.0, True)["nota"] == 10   # teto 10


def test_padrao_sem_ajuste_calibrado():
    """Calibrado nas 3 lives em cache (19/11/2026): Luna já dá tantas notas 7+ quanto o Sol; ajuste 0."""
    assert gpt.MODELO_CODEX_PADRAO == "gpt-6-luna"
    assert all(m["ajuste_nota"] == 0 for m in gpt.MODELOS_CODEX.values())
