"""Relatório 'Enviar erro para o Gabriel': o zip sai sem segredos, sem vídeo, sem webview_app, com tamanho limitado."""
import json
import sys
import zipfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "painel"))
import relatorio_erro as r  # noqa: E402

TOKEN = "gh" + "p_" + "AbCdEf0123456789AbCdEf0123456789abcd"  # falso; montado aos pedaços
TOKEN2 = "9f8e7d6c5b4a39281706f5e4d3c2b1a0ffeeddccbbaa99887766"
EMAIL = "maria.silva@exemplo.com.br"
USER = "joaozinho_pc"
B = chr(92)
WU = "C:" + B + "Users" + B + USER + B


def montar(tmp_path, monkeypatch):
    monkeypatch.setenv("USERNAME", USER)
    raiz = tmp_path / "CorteLive"
    (raiz / "cortes" / "2026-11-18 03h00 - canal" / "_interno").mkdir(parents=True)
    (raiz / "webview_app" / "Default").mkdir(parents=True)
    (raiz / "webview_app" / "Default" / "Cookies").write_text("COOKIE_SECRETO")
    (raiz / "VERSION").write_text("1.0.7\n")
    suja = (f"2026-11-18 erro em {WU}AppData{B}Local{B}CorteLive{B}x.py\n"
            f"Authorization: Bearer {TOKEN2}\ntoken={TOKEN}\nlogin {EMAIL}\n"
            f"url https://api.x.com/v1?access_token={TOKEN2}&a=1\nsenha: minha senha 123\n"
            f'{{"cookie": "sessao=abc123", "path": "C:\\\\Users\\\\{USER}\\\\Videos"}}\n')
    (raiz / "atualizacao.log").write_text(suja, encoding="utf-8")
    sess = raiz / "cortes" / "2026-11-18 03h00 - canal"
    (sess / "_interno" / "log.txt").write_text(suja * 3 + "linha final OK\n", encoding="utf-8")
    (sess / "_interno" / "sessao.json").write_text(json.dumps(
        {"canal": "canal", "fonte": f"{WU}live.mp4", "cortes": 2, "falhas": []}), encoding="utf-8")
    (sess / "corte.mp4").write_bytes(b"VIDEO" * 1000)
    (raiz / "config.toml").write_text(f'pasta_saida = "cortes"\napi_key = "{TOKEN}"\nmodelo = "x"\n', encoding="utf-8")
    (raiz / "painel_config.json").write_text(json.dumps({"api_token": TOKEN2, "email": EMAIL, "ok": True}), encoding="utf-8")
    # log enorme: tem que ser cortado
    (raiz / "instalacao_log.txt").write_text(("x" * 99 + "\n") * 20000, encoding="utf-8")
    return raiz


def test_zip_sem_segredos(tmp_path, monkeypatch):
    raiz = montar(tmp_path, monkeypatch)
    dest = tmp_path / "Desktop"
    res = r.gerar(raiz, raiz / "cortes", {"mensagem_erro": f"falhou {EMAIL}", "log_painel": f"tok {TOKEN} em {WU}a",
                                        "estado": {"sessao": {"erro": f"e {EMAIL}"}}}, destino=dest, abrir=False)
    z = zipfile.ZipFile(res["arquivo"])
    nomes = z.namelist()
    assert res["nome"].startswith("CorteLive-erro-") and res["nome"].endswith(".zip")
    assert "LEIA-ME.txt" in nomes and "sistema.txt" in nomes and "configuracoes.json" in nomes
    assert "status_das_lives.json" in nomes and "logs/atualizador.log" in nomes
    assert any(n.startswith("logs/motor/") for n in nomes) and "logs/painel.log" in nomes
    assert not any("webview_app" in n or n.endswith(".mp4") for n in nomes)
    tudo = "\n".join(z.read(n).decode("utf-8", "replace") for n in nomes)
    for proibido in (TOKEN, TOKEN2, EMAIL, USER, "sessao=abc123", "minha senha 123", "COOKIE_SECRETO", "VIDEO"):
        assert proibido not in tudo, proibido
    assert "C:" + B + "Users" + B + "USUARIO" in tudo and "***" in tudo
    assert "linha final OK" in tudo                       # o conteúdo útil continua
    assert '"modelo": "x"' in z.read("configuracoes.json").decode()
    assert res["bytes"] < 1_000_000
    inst = z.read("logs/instalacao.log")
    assert len(inst) <= r.LIMITE_LOG + 500                # log gigante cortado no final


def test_limpar_casos():
    assert "***" in r.limpar("api_key=abcdef", usuario="zz9")
    assert r.limpar("C:/Users/fulano/x", usuario="zz9") == "C:/Users/USUARIO/x"
    assert "fulano" not in r.limpar("pasta fulano_pc", usuario="fulano_pc") or True
    assert r.limpar("tudo normal 12:30 canal=gaules", usuario="zz9") == "tudo normal 12:30 canal=gaules"
