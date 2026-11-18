from io import StringIO
import json
import os
from pathlib import Path
from queue import Queue
import subprocess
import sys
from threading import Event
import time
import types

from cortelive import captura, multi
from painel import motor_painel
from painel.motor_painel import acompanhar_multiplas, escutar_stdin


def _pid_vivo(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            return False
        try:
            code = ctypes.c_ulong()
            if not k32.GetExitCodeProcess(h, ctypes.byref(code)):
                return False
            return code.value == STILL_ACTIVE
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def test_comando_parar_live_nao_para_sessao_ate_fim_do_pipe():
    parar = Event()
    comandos = Queue()

    def entrada():
        yield "parar-live 0123456789abcdef0123456789abcdef\n"
        assert comandos.get_nowait() == "0123456789abcdef0123456789abcdef"
        assert not parar.is_set()
        yield "parar-live invalido\n"
        yield "parar-live -1\n"

    escutar_stdin(parar, entrada(), comandos)
    assert comandos.empty()
    assert parar.is_set()


def test_parar_sessao_ignora_comandos_posteriores():
    parar = Event()
    comandos = Queue()
    escutar_stdin(parar, StringIO("parar\nparar-live 0123456789abcdef0123456789abcdef\n"), comandos)
    assert comandos.empty()
    assert parar.is_set()


def test_comando_fechar_marca_fechar_ev_e_parar_ev():
    parar = Event()
    fechar = Event()
    acordar = Event()
    comandos = Queue()
    escutar_stdin(parar, StringIO("fechar\n"), comandos, acordar_ev=acordar, fechar_ev=fechar)
    assert parar.is_set()
    assert fechar.is_set()
    assert acordar.is_set()


class _SessaoSimulada:
    def __init__(self, canal, estado_inicial="ao vivo", cortes_prontos=0, descartados_ao_parar=0):
        self.canal = canal
        self.cfg_extra = {}
        self.terminal = False
        self._fim_ev = Event()
        self.estado = estado_inicial
        self.cortes = cortes_prontos
        self.descartados = 0
        self._descartados_ao_parar = descartados_ao_parar
        self.parada_pedida = False
        self.descartou_cortes = False
        self.resumo = None
        self.erro = None

    def iniciar(self):
        return self

    def parar(self, descartar_cortes=False):
        self.parada_pedida = True
        self.descartou_cortes = bool(descartar_cortes)
        if descartar_cortes and self._descartados_ao_parar:
            self.descartados = self._descartados_ao_parar
        self.estado = "encerrada"
        self.resumo = {"cortes": self.cortes, "motivo_fim": "parado pelo usuário"}
        self._fim_ev.set()

    def aguardar(self, timeout=None):
        self._fim_ev.wait(timeout)
        return self.resumo

    def status(self):
        return {
            "canal": self.canal,
            "pasta": f"C:/tmp/{self.canal}",
            "estado": self.estado,
            "cortes": self.cortes,
            "cortes_descartados_parada": self.descartados,
            "live_s": 42.0 if self.estado == "ao vivo" else 0.0,
            "atraso_transcricao_s": 1.5,
            "motivo_fim": (self.resumo or {}).get("motivo_fim"),
        }


def test_parar_uma_live_mantem_outra_ativa_cortes_prontos_e_avisa_descarte(monkeypatch):
    sessoes = {}

    def criar_falsa(fonte, arquivo, canal, a):
        if canal == "canal_espera":
            s = _SessaoSimulada(canal, estado_inicial="esperando live", cortes_prontos=2, descartados_ao_parar=1)
        else:
            s = _SessaoSimulada(canal, estado_inicial="ao vivo", cortes_prontos=3, descartados_ao_parar=0)
        sessoes[canal] = s
        return s

    emitidos = []
    parar_ev = Event()
    acordar_ev = Event()
    fechar_ev = Event()
    comandos = Queue()
    ja_parou_primeira = False
    ja_viu_parada = False

    monkeypatch.setattr(motor_painel, "status_servicos", lambda m: {"modo": m, "transcricao": {}, "render": {}})

    def capturar_emissao(dados):
        nonlocal ja_parou_primeira, ja_viu_parada
        emitidos.append(json.loads(json.dumps(dados)))
        lives = dados.get("lives") or []
        if len(lives) == 2 and not ja_parou_primeira:
            id_espera = next(l["id"] for l in lives if l["canal"] == "canal_espera")
            ja_parou_primeira = True
            comandos.put(id_espera)
            acordar_ev.set()
            return
        if ja_parou_primeira and not ja_viu_parada:
            por_canal = {l["canal"]: l for l in lives}
            if por_canal["canal_espera"]["estado"] == "parada":
                ja_viu_parada = True
                assert por_canal["canal_espera"]["pode_parar"] is False
                assert por_canal["canal_espera"]["cortes"] == 2
                assert por_canal["canal_espera"]["cortes_descartados_parada"] == 1
                assert "descartado" in (por_canal["canal_espera"].get("aviso") or "").lower()
                # A outra live segue gravando normalmente!
                assert por_canal["canal_gravando"]["estado"] == "ao vivo"
                assert por_canal["canal_gravando"]["pode_parar"] is True
                assert not sessoes["canal_gravando"].parada_pedida
                parar_ev.set()
                acordar_ev.set()

    monkeypatch.setattr(motor_painel, "emitir", capturar_emissao)

    ns = types.SimpleNamespace(
        minutos=None, detalhado=False, duracao_min=30, duracao_max=60,
        modelo="cam-em-cima", legenda="opus", saida=None, sem_chat=True,
    )
    sup = multi.Supervisor(
        ["https://twitch.tv/canal_espera", "https://twitch.tv/canal_gravando"],
        [], None, ns, painel=False, repetir=True, criar=criar_falsa,
    )
    rc = acompanhar_multiplas(
        sup, parar_ev, "CPU teste", comandos, unica=False,
        acordar_ev=acordar_ev, fechar_ev=fechar_ev,
    )
    assert rc == 0
    assert ja_viu_parada
    assert sessoes["canal_espera"].descartou_cortes is True
    # PARAR SESSÃO para todas sem forçar descarte dos cortes já em render
    assert sessoes["canal_gravando"].parada_pedida is True
    assert sessoes["canal_gravando"].descartou_cortes is False


def test_captura_solicitar_parada_mata_apenas_processos_daquela_live(tmp_path: Path, monkeypatch):
    pasta_a = tmp_path / "live_a"
    pasta_b = tmp_path / "live_b"
    pasta_a.mkdir()
    pasta_b.mkdir()
    pid_arq_a = tmp_path / "filho_a.pid"
    pid_arq_b = tmp_path / "filho_b.pid"

    script_filho = tmp_path / "fonte_lenta.py"
    script_filho.write_text(
        "import os, subprocess, sys, time\n"
        "neto = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "open(sys.argv[1], 'w').write(f'{os.getpid()},{neto.pid}')\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )

    def fonte_falsa(self):
        destino_pid = pid_arq_a if "live_a" in str(self.pasta) else pid_arq_b
        cmd = [sys.executable, str(script_filho), str(destino_pid)]
        if os.name == "nt":
            cmd = [sys.executable, "-m", "cortelive.processos", *cmd]
        return subprocess.Popen(
            cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ), "fonte-teste"

    # Evita precisar de ffmpeg no PATH do Python global durante este teste unitário de árvore de processos
    monkeypatch.setattr(
        captura.Captura, "_ffmpeg_seg",
        lambda self, rodada, entrada, extra_in=(): (
            [sys.executable, "-c", "import sys, time; time.sleep(60)"],
            self.pasta / f"lista_{rodada}.csv",
        ),
    )
    monkeypatch.setattr(captura.Captura, "_fonte", fonte_falsa)

    cap_a = captura.Captura(pasta_a, url="https://exemplo.invalid/a")
    cap_b = captura.Captura(pasta_b, url="https://exemplo.invalid/b")
    cap_a.start()
    cap_b.start()
    try:
        prazo = time.time() + 10
        while (not pid_arq_a.exists() or not pid_arq_b.exists()) and time.time() < prazo:
            time.sleep(0.05)
        assert pid_arq_a.exists() and pid_arq_b.exists()

        pids_a = [int(x) for x in pid_arq_a.read_text().strip().split(",")]
        pids_b = [int(x) for x in pid_arq_b.read_text().strip().split(",")]
        ff_a = cap_a._ff.pid
        ff_b = cap_b._ff.pid
        assert all(_pid_vivo(p) for p in (*pids_a, ff_a, *pids_b, ff_b))

        # Para SOMENTE a live A
        t0 = time.time()
        cap_a.solicitar_parada("parado pelo usuário")
        cap_a.join(timeout=5)
        assert not cap_a.is_alive()
        assert time.time() - t0 < 3.0

        # Aguarda propagação do Job Object no kernel Windows (~50ms)
        prazo_morte = time.time() + 2.0
        while any(_pid_vivo(p) for p in (*pids_a, ff_a)) and time.time() < prazo_morte:
            time.sleep(0.05)

        assert not any(_pid_vivo(p) for p in (*pids_a, ff_a)), "Processos da live A deveriam ter morrido"
        assert all(_pid_vivo(p) for p in (*pids_b, ff_b)), "Processos da live B deveriam continuar vivos"
    finally:
        cap_a.solicitar_parada("fim do teste")
        cap_b.solicitar_parada("fim do teste")
        cap_a.join(timeout=5)
        cap_b.join(timeout=5)

    prazo_fim = time.time() + 2.0
    while any(_pid_vivo(p) for p in (*pids_b, ff_b)) and time.time() < prazo_fim:
        time.sleep(0.05)
    assert not any(_pid_vivo(p) for p in (*pids_b, ff_b))
