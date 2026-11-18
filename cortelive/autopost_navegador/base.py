"""Navegador, ritmo humano, prints e eventos JSON, compartilhados pelas três redes."""
from __future__ import annotations

import json
import os
import random
import re
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from . import pasta_base, pasta_perfil

ARGS_NAVEGADOR = [
    "--disable-blink-features=AutomationControlled",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-features=Translate",
    "--lang=pt-BR",
]


class ErroPostagem(Exception):
    """Falha que o usuário precisa entender (vai como `erro` na linha JSON)."""


class SemSessao(ErroPostagem):
    """O perfil da rede não está logado: rodar `conectar <rede>`."""


class ParouAntesDePublicar(Exception):
    """Modo --ate-antes-de-publicar: tudo pronto, clique final NÃO dado."""


def achar_navegador() -> tuple[str, str]:
    """-> (channel do Playwright, caminho do .exe). Edge primeiro (vem em todo Windows 10/11)."""
    pf = [os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
          os.environ.get("ProgramFiles", r"C:\Program Files"),
          os.environ.get("LOCALAPPDATA", "")]
    candidatos = [("msedge", r"Microsoft\Edge\Application\msedge.exe"),
                  ("chrome", r"Google\Chrome\Application\chrome.exe")]
    for canal, rel in candidatos:
        for base in pf:
            exe = Path(base) / rel
            if base and exe.is_file():
                return canal, str(exe)
    for canal, nome in (("msedge", "msedge"), ("chrome", "chrome")):
        exe = shutil.which(nome)
        if exe:
            return canal, exe
    raise ErroPostagem("Nenhum navegador encontrado (Edge ou Chrome).")


def abrir_para_login(rede: str, url: str) -> int:
    """Abre o navegador NORMAL (sem automação) no perfil da rede para o usuário logar à mão.
    Login do Google recusa navegador controlado por automação; por isso aqui não há Playwright.
    Bloqueia até o usuário fechar a janela."""
    _, exe = achar_navegador()
    perfil = pasta_perfil(rede)
    proc = subprocess.Popen([exe, f"--user-data-dir={perfil}", "--no-first-run",
                             "--no-default-browser-check", "--new-window", url])
    return proc.wait()


def pausa_humana(a: float = 0.4, b: float = 1.3) -> None:
    """Ritmo de gente entre ações (não é espera de carregamento: essa é sempre por elemento)."""
    time.sleep(random.uniform(a, b))


def digitar(page, texto: str) -> None:
    """Digita no campo focado, com atraso por tecla variável e quebras de linha por Shift+Enter."""
    linhas = texto.split("\n")
    for i, ln in enumerate(linhas):
        if ln:
            page.keyboard.type(ln, delay=random.randint(18, 55))
        if i < len(linhas) - 1:
            page.keyboard.press("Shift+Enter")


def limpar_e_digitar(page, locator, texto: str) -> None:
    locator.click()
    pausa_humana(0.2, 0.5)
    page.keyboard.press("Control+A")
    page.keyboard.press("Delete")
    pausa_humana(0.2, 0.5)
    digitar(page, texto)


def tentar(fn: Callable, vezes: int = 3, espera: float = 2.0, nome: str = ""):
    """Nova tentativa para passos instáveis. Erros de sessão e a parada proposital não repetem."""
    ultimo = None
    for i in range(vezes):
        try:
            return fn()
        except (SemSessao, ParouAntesDePublicar):
            raise
        except Exception as e:      # noqa: BLE001 - qualquer falha de página vale nova tentativa
            ultimo = e
            if i < vezes - 1:
                time.sleep(espera * (i + 1))
    raise ErroPostagem(f"{nome or 'passo'} falhou {vezes}x: {str(ultimo).splitlines()[0][:200]}")


def rx(*palavras: str) -> re.Pattern:
    """Regex sem diferenciar maiúsculas, para textos em PT e EN."""
    return re.compile("|".join(palavras), re.I)


class Sessao:
    """Abre o perfil persistente da rede no Playwright e guarda prints/log da execução."""

    def __init__(self, rede: str, emitir: Callable[[dict], None], visivel: bool = True):
        self.rede = rede
        self.emitir_cb = emitir
        self.visivel = visivel
        carimbo = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        self.pasta_log = pasta_base() / "logs" / f"{carimbo}_{rede}"
        self.pasta_log.mkdir(parents=True, exist_ok=True)
        self._n = 0
        self._pw = self.ctx = self.page = None

    # -- ciclo de vida
    def __enter__(self):
        from playwright.sync_api import sync_playwright
        canal, _ = achar_navegador()
        self._pw = sync_playwright().start()
        try:
            self.ctx = self._pw.chromium.launch_persistent_context(
                user_data_dir=str(pasta_perfil(self.rede)),
                channel=canal,
                headless=not self.visivel,
                args=ARGS_NAVEGADOR,
                ignore_default_args=["--enable-automation"],
                viewport={"width": 1366, "height": 860},
                locale="pt-BR",
                accept_downloads=False,
            )
        except Exception as e:
            self._pw.stop()
            msg = str(e).splitlines()[0]
            if "user data directory is already in use" in str(e) or "ProcessSingleton" in str(e):
                msg = "a janela dessa rede já está aberta; feche-a e tente de novo"
            raise ErroPostagem(f"não abriu o navegador: {msg}") from e
        self.ctx.set_default_timeout(30_000)
        self.page = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()
        return self

    def __exit__(self, *exc):
        try:
            if self.ctx:
                self.ctx.close()
        finally:
            if self._pw:
                self._pw.stop()
        return False

    # -- saída
    def emitir(self, etapa: str, pct: int, **extra) -> None:
        ev = {"rede": self.rede, "etapa": etapa, "pct": int(pct), **extra}
        self.emitir_cb(ev)
        with open(self.pasta_log / "eventos.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({**ev, "t": datetime.now().isoformat(timespec="seconds")},
                               ensure_ascii=False) + "\n")

    def print(self, nome: str) -> str:
        self._n += 1
        arq = self.pasta_log / f"{self._n:02d}_{nome}.png"
        try:
            self.page.screenshot(path=str(arq), full_page=False)
        except Exception:
            return ""
        return str(arq)
