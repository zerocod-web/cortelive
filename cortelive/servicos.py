"""Recursos compartilhados entre todas as sessões de live (ver CONTRATO_MULTI.md).

- transcricao(): UM modelo na GPU, fila justa (rodízio entre sessões), watchdog e degradação automática
  pela carga TOTAL (todas as lives somadas + o que mais estiver usando a placa, ex.: um jogo).
- render(): limite de encodes simultâneos (NVENC das GeForce aceita poucas sessões).
"""
import collections
import gc
import threading
import time

from . import log

_lock = threading.Lock()
_cfg = None
_modo = None
_trans = None
_rend = None


def configurar(cfg: dict | None = None, modo=None):
    """Opcional: config (dict do config.toml) e Modo detectado. Sem chamar, carrega sozinho."""
    global _cfg, _modo
    with _lock:
        if cfg is not None:
            _cfg = cfg
        if modo is not None:
            _modo = modo


def _garantir():
    global _cfg, _modo
    if _cfg is None:
        from .cli import carregar_config
        _cfg = carregar_config()
    if _modo is None:
        from . import hw
        _modo = hw.detectar()


def modo():
    with _lock:
        _garantir()
        return _modo


def transcricao():
    global _trans
    with _lock:
        if _trans is None:
            _garantir()
            _trans = ServicoTranscricao(_modo, _cfg)
        return _trans


def render():
    global _rend
    with _lock:
        if _rend is None:
            _garantir()
            _rend = FilaRender(int(_cfg.get("encodes_simultaneos", 2)))
        return _rend


def desligar():
    global _trans
    if _trans:
        _trans.desligar()
        _trans = None


# ------------------------------------------------------------------------------------------------
class _Pedido:
    __slots__ = ("sid", "audio", "t0", "pronto", "res", "erro", "criado", "ini_proc")

    def __init__(self, sid, audio, t0):
        self.sid, self.audio, self.t0 = sid, audio, t0
        self.pronto = threading.Event()
        self.res, self.erro = None, None
        self.criado, self.ini_proc = time.time(), None


class ServicoTranscricao:
    def __init__(self, modo, cfg):
        from . import fala
        self._fala = fala
        self.modo, self.cfg = modo, cfg
        self.degraus = [modo.modelo_whisper] + [m for m in cfg.get("modelos_reserva", []) if m != modo.modelo_whisper]
        self.nivel = 0
        self.bloco_s = cfg["bloco_normal_s"]
        self.tr = fala.Transcritor(modo)
        self.filas = collections.OrderedDict()        # sid -> deque de pedidos
        self.cv = threading.Condition()
        self.gen = 0
        self.atual = None                              # pedido em processamento
        self.historico = collections.deque(maxlen=400)  # (fim, gasto, audio_s, espera)
        self.trocas = []
        self.carregando = False
        self.ruim_desde = self.bom_desde = None
        self.parar = False
        self._reg = log.atual()
        self._iniciar_worker()
        threading.Thread(target=self._vigia, daemon=True).start()

    # --- API ------------------------------------------------------------------------------------
    def enviar(self, sid, audio, t0, timeout=None):
        p = _Pedido(sid, audio, t0)
        with self.cv:
            self.filas.setdefault(sid, collections.deque()).append(p)
            self.cv.notify_all()
        if not p.pronto.wait(timeout):
            with self.cv:
                q = self.filas.get(sid)
                if q and p in q:
                    q.remove(p)
            raise TimeoutError(f"transcrição demorou mais de {timeout} s")
        if p.erro:
            raise p.erro
        return p.res

    @property
    def nome_modelo(self):
        return self.degraus[self.nivel]

    def status(self):
        agora = time.time()
        rec = [h for h in self.historico if agora - h[0] < 60]
        carga = sum(h[1] for h in rec) / 60.0
        return {"modelo": self.nome_modelo, "fila": sum(len(q) for q in self.filas.values()),
                "carga_60s": round(carga, 3),
                "fator_tempo_real": round(sum(h[1] for h in rec) / max(1e-6, sum(h[2] for h in rec)), 3) if rec else None,
                "bloco_s": self.bloco_s, "trocas": list(self.trocas)}

    def desligar(self):
        self.parar = True
        with self.cv:
            self.cv.notify_all()

    def cancelar(self, sid):
        with self.cv:
            q = self.filas.pop(sid, None)
            if not q:
                return
            for p in q:
                p.erro = InterruptedError("live parada")
                p.pronto.set()

    # --- worker ---------------------------------------------------------------------------------
    def _iniciar_worker(self):
        self.gen += 1
        threading.Thread(target=self._worker, args=(self.gen,), daemon=True).start()

    def _proximo(self):
        """Rodízio justo: pega da primeira sessão com pedido e manda ela para o fim da fila."""
        for sid in list(self.filas):
            q = self.filas[sid]
            if q:
                p = q.popleft()
                self.filas.move_to_end(sid)
                return p
        return None

    def _worker(self, gen):
        log.usar(self._reg)
        while not self.parar and gen == self.gen:
            with self.cv:
                p = self._proximo()
                while p is None and not self.parar and gen == self.gen:
                    self.cv.wait(1)
                    p = self._proximo()
                if p is None:
                    return
                self.atual = p
                p.ini_proc = time.time()
            tr = self.tr
            try:
                p.res = tr.transcrever(p.audio, p.t0)
            except Exception as e:
                p.erro = e
            fim = time.time()
            if gen != self.gen:      # abandonado pelo vigia: o pedido já foi respondido com erro
                return
            self.atual = None
            self.historico.append((fim, fim - p.ini_proc, len(p.audio) / 16000, p.ini_proc - p.criado))
            p.pronto.set()
            self._avaliar_carga()

    def _vigia(self):
        """Watchdog do serviço: pedido preso no motor por mais que travou_transcricao_s -> motor novo."""
        log.usar(self._reg)
        lim = self.cfg["travou_transcricao_s"]
        while not self.parar:
            time.sleep(2)
            p = self.atual
            if p and p.ini_proc and time.time() - p.ini_proc > lim:
                log.aviso(f"Transcrição travou (> {lim} s) no motor compartilhado. Recarregando o modelo.")
                self.atual = None
                p.erro = TimeoutError("motor de transcrição travou")
                p.pronto.set()
                try:
                    self.tr = self._fala.Transcritor(self.modo, self.nome_modelo)
                except Exception as e:
                    log.aviso(f"Não consegui recarregar a transcrição: {e}")
                self._iniciar_worker()

    # --- degradação pela carga total ---------------------------------------------------------------
    def _avaliar_carga(self):
        c = self.cfg
        agora = time.time()
        rec = [h for h in self.historico if agora - h[0] < 30]
        if len(rec) < 3:
            return
        audio = sum(h[2] for h in rec)
        gasto = sum(h[1] for h in rec)
        fator = gasto / max(1e-6, audio)                    # segundos gastos por segundo de áudio
        espera = max(h[3] for h in rec)                     # maior tempo parado na fila
        ocupado = gasto / 30.0                              # fração do tempo com a placa transcrevendo
        ruim = fator > c["fator_max"] or espera > c["atraso_max_s"] / 2 or ocupado > 0.85
        bom = fator < c["fator_folga"] and espera < 3 and ocupado < 0.5
        self.ruim_desde = (self.ruim_desde or agora) if ruim else None
        self.bom_desde = (self.bom_desde or agora) if bom else None
        if self.carregando:
            return
        motivo = f"gastando {fator * 100:.0f}% do tempo real, fila de até {espera:.0f} s, placa {ocupado * 100:.0f}% ocupada"
        if ruim and agora - self.ruim_desde >= c["segundos_ruim_para_descer"]:
            self.ruim_desde = None
            if self.nivel + 1 < len(self.degraus):
                self._trocar(self.nivel + 1, motivo)
            elif self.bloco_s < c["bloco_lento_s"]:
                self.bloco_s = c["bloco_lento_s"]
                self._anotar(self.nome_modelo, self.nome_modelo, f"blocos de {self.bloco_s:.0f} s ({motivo})")
        elif bom and agora - self.bom_desde >= c["segundos_bom_para_subir"]:
            self.bom_desde = None
            if self.bloco_s > c["bloco_normal_s"]:
                self.bloco_s = c["bloco_normal_s"]
                self._anotar(self.nome_modelo, self.nome_modelo, f"placa folgou: blocos de volta a {self.bloco_s:.0f} s")
            elif self.nivel > 0:
                self._trocar(self.nivel - 1, f"placa folgou ({motivo})")

    def _anotar(self, de, para, motivo):
        self.trocas.append((time.strftime("%H:%M:%S"), de, para, motivo))
        log.aviso(f"Transcrição: {de} -> {para} ({motivo})." if de != para else f"Transcrição: {motivo}.")

    def _trocar(self, nivel, motivo):
        de, para = self.nome_modelo, self.degraus[nivel]
        self.carregando = True
        log.info(f"Transcrição: carregando '{para}' em segundo plano ({motivo})…")

        def carregar():
            log.usar(self._reg)
            try:
                novo = self._fala.Transcritor(self.modo, para)
                antigo, self.tr = self.tr, novo
                self.nivel = nivel
                self.historico.clear()
                self._anotar(de, para, motivo)
                del antigo
                gc.collect()                 # devolve a VRAM do modelo maior
            except Exception as e:
                log.aviso(f"Não consegui carregar '{para}': {e}. Sigo com '{de}'.")
            finally:
                self.carregando = False
        threading.Thread(target=carregar, daemon=True).start()


class Cliente:
    """O que a Fala de cada sessão usa no lugar do Transcritor: mesma interface, passando pelo serviço."""

    def __init__(self, servico: ServicoTranscricao, sid, timeout):
        self.sv, self.sid, self.timeout = servico, sid, timeout

    @property
    def nome_modelo(self):
        return self.sv.nome_modelo

    def transcrever(self, audio, t0):
        return self.sv.enviar(self.sid, audio, t0, timeout=self.timeout)


# ------------------------------------------------------------------------------------------------
class FilaRender:
    def __init__(self, limite):
        self.limite = max(1, limite)
        self.sem = threading.BoundedSemaphore(self.limite)
        self.rodando = 0
        self.esperando = 0
        self._l = threading.Lock()

    def executar(self, fn, *a, **k):
        with self._l:
            self.esperando += 1
        self.sem.acquire()
        with self._l:
            self.esperando -= 1
            self.rodando += 1
        try:
            return fn(*a, **k)
        finally:
            with self._l:
                self.rodando -= 1
            self.sem.release()

    def status(self):
        return {"rodando": self.rodando, "esperando": self.esperando, "limite": self.limite}
