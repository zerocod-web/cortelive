from io import StringIO
import json
from queue import Queue
from threading import Event
import types

from cortelive import multi
from painel import motor_painel
from painel.motor_painel import acompanhar_multiplas, escutar_stdin
from tests.test_parar_live import _SessaoSimulada


def _hex(t):
    return t.encode("utf-8").hex()


def test_stdin_adicionar_live_preserva_maiusculas_e_ignora_lixo():
    parar, comandos = Event(), Queue()
    url = "https://www.twitch.tv/CanalMaiusculo?x=Ã"
    escutar_stdin(parar, StringIO(f"adicionar-live {_hex(url)}\nadicionar-live zz\nadicionar-live\n"), comandos)
    assert comandos.get_nowait() == ("adicionar", url)
    assert comandos.empty()


def test_segunda_e_terceira_live_entram_com_sessao_rodando(monkeypatch):
    sessoes = {}

    def criar(fonte, arquivo, canal, a):
        s = _SessaoSimulada(canal)
        sessoes[canal] = s
        return s

    parar, acordar, fechar, comandos = Event(), Event(), Event(), Queue()
    emitidos = []
    monkeypatch.setattr(motor_painel, "status_servicos", lambda m: {"modo": m, "transcricao": {}, "render": {}})
    fase = {"n": 0}

    def emitir(d):
        emitidos.append(json.loads(json.dumps(d)))
        lives = d.get("lives") or []
        if fase["n"] == 0 and len(lives) == 1:
            fase["n"] = 1
            comandos.put(("adicionar", "https://twitch.tv/segunda"))
            comandos.put(("adicionar", "https://twitch.tv/terceira"))
            comandos.put(("adicionar", "https://twitch.tv/segunda"))  # repetida: ignorada
            acordar.set()
        elif len(lives) == 3 and fase["n"] == 1:
            fase["n"] = 2
            parar.set(); acordar.set()

    monkeypatch.setattr(motor_painel, "emitir", emitir)
    ns = types.SimpleNamespace(minutos=None, detalhado=False, duracao_min=30, duracao_max=60,
                               modelo="cam-em-cima", legenda="opus", saida=None, sem_chat=True)
    unica_cel = [True]
    sup = multi.Supervisor(["https://twitch.tv/primeira"], [], None, ns, painel=False, repetir=False, criar=criar)
    rc = acompanhar_multiplas(sup, parar, "CPU", comandos, unica=True, acordar_ev=acordar,
                              fechar_ev=fechar, unica_cel=unica_cel)
    assert rc == 0
    ultima = [e for e in emitidos if len(e.get("lives") or []) == 3][0]["lives"]
    assert {l["canal"] for l in ultima} == {"primeira", "segunda", "terceira"}
    assert all(l["pode_parar"] and len(l["id"]) == 32 for l in ultima)
    assert len({l["id"] for l in ultima}) == 3
    assert unica_cel[0] is False
    assert sup.repetir is True
