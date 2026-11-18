"""Log curto e amigável no terminal + log completo em arquivo — um por sessão de live.

Cada sessão cria um Registro (arquivo log.txt próprio, terminal opcional). As funções do módulo
(info/ok/aviso/corte/debug) escrevem no Registro da thread atual; threads criadas por uma sessão
chamam usar(registro) ao começar (ou recebem via herdar()). Fora de sessão, vão para o Registro global.
"""
import logging
import sys
import threading
import time
from pathlib import Path

_local = threading.local()
# Uma escrita por linha: o painel lê o stdout linha a linha e não pode ver duas mensagens coladas.
ESCRITA = threading.Lock()


def escrever_linha(texto: str):
    with ESCRITA:
        sys.stdout.write(texto + "\n")
        sys.stdout.flush()


class Registro:
    def __init__(self, arquivo: Path | None = None, terminal=True, detalhado=False, nome="cortelive"):
        self.t0 = time.time()
        self.terminal = terminal
        self.arquivo = arquivo
        self.lg = logging.getLogger(f"cortelive.{nome}.{id(self)}")
        self.lg.setLevel(logging.DEBUG)
        self.lg.propagate = False
        if arquivo:
            Path(arquivo).parent.mkdir(parents=True, exist_ok=True)
            h = logging.FileHandler(arquivo, encoding="utf-8")
            h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            self.lg.addHandler(h)
        if detalhado:
            h = logging.StreamHandler(sys.stderr)
            h.setLevel(logging.DEBUG)
            self.lg.addHandler(h)
        self.ultimas = []          # últimas mensagens (para painel)

    def _rel(self):
        s = int(time.time() - self.t0)
        return f"{s // 60:02d}:{s % 60:02d}"

    def info(self, msg):
        if self.terminal:
            escrever_linha(f"[{self._rel()}] {msg}")
        self.lg.info(msg)
        self.ultimas = (self.ultimas + [msg])[-5:]

    def debug(self, msg):
        self.lg.debug(msg)

    def fechar(self):
        for h in list(self.lg.handlers):
            h.close()
            self.lg.removeHandler(h)


_global = Registro(terminal=True)


def usar(reg: Registro):
    _local.reg = reg


def atual() -> Registro:
    return getattr(_local, "reg", None) or _global


def herdar(fn):
    """Embrulha uma função para rodar em outra thread com o Registro de quem a criou."""
    reg = atual()

    def f(*a, **k):
        usar(reg)
        return fn(*a, **k)
    return f


def configurar(pasta: Path | None = None, detalhado: bool = False, terminal=True):
    """Compatível com o modo antigo: cria o Registro da sessão e o põe na thread atual."""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    reg = Registro(Path(pasta) / "log.txt" if pasta else None, terminal, detalhado,
                   nome=Path(pasta).name if pasta else "cortelive")
    usar(reg)
    if detalhado and pasta:
        # bibliotecas (faster-whisper etc.) também no log desta sessão
        logging.getLogger().addHandler(reg.lg.handlers[0])
        logging.getLogger().setLevel(logging.DEBUG)
    return reg.arquivo


def info(msg: str):
    atual().info(msg)


def ok(msg: str):
    info(f"OK  {msg}")


def aviso(msg: str):
    info(f"!   {msg}")


def corte(msg: str):
    info(f">>> {msg}")


def debug(msg: str):
    atual().debug(msg)
