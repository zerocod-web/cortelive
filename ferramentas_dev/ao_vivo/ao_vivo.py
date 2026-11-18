r"""Visualizador 'Ao vivo' de desenvolvimento para o CorteLive.

Sobe o painel\motor_painel.py exatamente como o host faz (token em
CORTELIVE_PAINEL_TOKEN + leitura da porta JSON em stdout), serve o painel real
em loopback e entrega na porta própria (padrão 54777) uma tela dividida:
  - Esquerda (~70%): iframe com o painel real do CorteLive, recarregando em até
    2 s quando qualquer arquivo em painel\ ou cortelive\ muda (polling de mtime).
    Se painel\motor_painel.py mudar, reinicia o motor automaticamente.
    Se o motor cair ou falhar ao subir, exibe o erro na tela em vez de branco.
  - Direita (~30%): 'Diário de mudanças' em português com hora, arquivo,
    contagem de linhas alteradas (diff em memória contra a última versão vista)
    e visualização do trecho do diff ao clicar.

Sem dependências externas (somente biblioteca padrão do Python 3.12 do .venv).
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import http.client
import json
import mimetypes
import os
import re
import secrets
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

RAIZ_PADRAO = Path(__file__).resolve().parents[2]
PORTA_PADRAO = 54777
INTERVALO_POLL_ARQUIVOS_S = 0.5
MAX_ITENS_DIARIO = 120
MAX_LINHAS_DIFF = 220
MAX_BYTES_TEXTO = 2 * 1024 * 1024

EXTENSOES_BINARIAS = {
    ".png", ".jpg", ".jpeg", ".webp", ".gif", ".ico", ".bmp",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".mp4", ".mkv", ".mov", ".webm", ".mp3", ".wav",
    ".exe", ".dll", ".pyd", ".pyc", ".pyo", ".zip", ".gz", ".tar",
}

MIMES_PAINEL = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".mjs": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".txt": "text/plain; charset=utf-8",
}


def agora_hms() -> str:
    return datetime.now().strftime("%H:%M:%S")


def html_escape(texto: str) -> str:
    return (
        (texto or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


class GerenciadorMotor:
    """Inicia, monitora e reinicia painel\\motor_painel.py com CORTELIVE_PAINEL_TOKEN."""

    def __init__(self, raiz: Path):
        self.raiz = raiz.resolve()
        self.python = self._achar_python()
        self.caminho_motor = self.raiz / "painel" / "motor_painel.py"
        self._lock = threading.RLock()
        self._proc: subprocess.Popen | None = None
        self._porta: int = 0
        self._token: str = ""
        self._erro: str | None = None
        self._reiniciando: bool = False
        self._iniciado_em: str = ""
        self._reinicios: int = 0
        self._linhas_saida: deque[str] = deque(maxlen=80)

    def _achar_python(self) -> Path:
        vpy = self.raiz / ".venv" / "Scripts" / "python.exe"
        if vpy.is_file():
            return vpy
        return Path(sys.executable).resolve()

    def _ambiente_motor(self, token: str) -> dict[str, str]:
        env = os.environ.copy()
        env["CORTELIVE_PAINEL_TOKEN"] = token
        env["PYTHONUTF8"] = "1"
        env["PYTHONPATH"] = str(self.raiz)
        env["HF_HOME"] = str(self.raiz / "modelos")
        partes_path = [
            str(self.raiz / "ffmpeg" / "bin"),
            str(self.python.parent),
            env.get("PATH", ""),
        ]
        env["PATH"] = os.pathsep.join(p for p in partes_path if p)
        return env

    def iniciar(self, eh_reinicio: bool = False) -> bool:
        with self._lock:
            self._reiniciando = True
            self._encerrar_processo_interno()
            self._linhas_saida.clear()
            self._porta = 0
            self._erro = None

            if not self.caminho_motor.is_file():
                self._erro = f"Arquivo não encontrado: {self.caminho_motor}"
                self._reiniciando = False
                return False

            token = secrets.token_hex(24)
            self._token = token
            cmd = [
                str(self.python),
                str(self.caminho_motor),
                "--servir",
                "--porta",
                "0",
                "--raiz",
                str(self.raiz),
            ]
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            try:
                proc = subprocess.Popen(
                    cmd,
                    cwd=str(self.raiz),
                    env=self._ambiente_motor(token),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    creationflags=flags,
                )
            except Exception as exc:
                self._erro = f"Falha ao iniciar motor_painel.py: {exc}"
                self._reiniciando = False
                return False

            self._proc = proc
            porta_evento = threading.Event()
            porta_lida: dict[str, int] = {}

            def ler_stdout():
                assert proc.stdout is not None
                try:
                    for linha in proc.stdout:
                        limpa = linha.rstrip("\r\n")
                        if not limpa:
                            continue
                        if "porta" not in porta_lida:
                            try:
                                obj = json.loads(limpa.lstrip("\ufeff"))
                                if isinstance(obj, dict) and isinstance(obj.get("porta"), int):
                                    porta_lida["porta"] = int(obj["porta"])
                                    porta_evento.set()
                                    continue
                            except ValueError:
                                pass
                        self._linhas_saida.append(f"[stdout] {limpa}")
                except Exception:
                    pass
                finally:
                    porta_evento.set()

            def ler_stderr():
                assert proc.stderr is not None
                try:
                    for linha in proc.stderr:
                        limpa = linha.rstrip("\r\n")
                        if limpa:
                            self._linhas_saida.append(limpa)
                except Exception:
                    pass

            threading.Thread(target=ler_stdout, daemon=True).start()
            threading.Thread(target=ler_stderr, daemon=True).start()

            porta_evento.wait(timeout=8.0)

            if "porta" in porta_lida and proc.poll() is None:
                self._porta = porta_lida["porta"]
                self._iniciado_em = agora_hms()
                if eh_reinicio:
                    self._reinicios += 1
                self._erro = None
                self._reiniciando = False
                return True

            # Falhou ao subir ou não informou porta JSON
            time.sleep(0.15)
            codigo = proc.poll()
            detalhe = "\n".join(self._linhas_saida).strip()
            if codigo is None:
                self._encerrar_processo_interno()
                msg_base = "motor_painel.py não informou a porta HTTP em até 8 segundos."
            else:
                msg_base = f"motor_painel.py encerrou ao iniciar (código {codigo})."
            self._erro = f"{msg_base}\n{detalhe}".strip()
            self._reiniciando = False
            return False

    def reiniciar(self) -> bool:
        return self.iniciar(eh_reinicio=True)

    def _encerrar_processo_interno(self):
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=2.5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=1.5)
        except Exception:
            pass

    def encerrar(self):
        with self._lock:
            self._encerrar_processo_interno()
            self._porta = 0

    def estado(self) -> dict:
        with self._lock:
            proc = self._proc
            vivo = proc is not None and proc.poll() is None and self._porta > 0
            if not vivo and not self._reiniciando and self._erro is None:
                codigo = proc.poll() if proc is not None else None
                detalhe = "\n".join(self._linhas_saida).strip()
                if codigo is not None:
                    self._erro = (
                        f"O processo motor_painel.py caiu inesperadamente (código {codigo}).\n{detalhe}"
                    ).strip()
                else:
                    self._erro = "O motor do painel não está em execução."
            return {
                "ok": vivo,
                "reiniciando": self._reiniciando,
                "porta": self._porta if vivo else 0,
                "iniciado_em": self._iniciado_em,
                "reinicios": self._reinicios,
                "erro": None if vivo else (self._erro or "Motor indisponível."),
            }

    def chamar(self, metodo: str, caminho: str, corpo_bytes: bytes = b"", content_type: str = "") -> tuple[int, str, bytes]:
        st = self.estado()
        if not st["ok"]:
            erro_json = json.dumps(
                {"ok": False, "erro": st["erro"] or "Motor do painel parado."},
                ensure_ascii=False,
            ).encode("utf-8")
            return 502, "application/json; charset=utf-8", erro_json

        porta = st["porta"]
        with self._lock:
            token = self._token

        cabecalhos = {
            "Host": f"127.0.0.1:{porta}",
            "Origin": f"http://127.0.0.1:{porta}",
            "X-CorteLive-Token": token,
            "Accept": "application/json",
            "Connection": "close",
        }
        if corpo_bytes:
            cabecalhos["Content-Type"] = content_type or "application/json"
            cabecalhos["Content-Length"] = str(len(corpo_bytes))

        conn = http.client.HTTPConnection("127.0.0.1", porta, timeout=35)
        try:
            conn.request(metodo, caminho, body=corpo_bytes if corpo_bytes else None, headers=cabecalhos)
            resp = conn.getresponse()
            dados = resp.read()
            tipo = resp.getheader("Content-Type") or "application/json; charset=utf-8"
            return resp.status, tipo, dados
        except Exception as exc:
            # Verifica se o processo morreu durante a chamada
            st_pos = self.estado()
            msg = st_pos["erro"] if not st_pos["ok"] else f"Falha ao comunicar com motor_painel.py: {exc}"
            erro_json = json.dumps({"ok": False, "erro": msg}, ensure_ascii=False).encode("utf-8")
            return 502, "application/json; charset=utf-8", erro_json
        finally:
            try:
                conn.close()
            except Exception:
                pass


class HostComplementarPainel:
    """Atende os arquivos estáticos de painel\\ e as rotas locais do host (API.md)
    quando motor_painel.py responde 404 'Rota não encontrada.'."""

    def __init__(self, raiz: Path, motor: GerenciadorMotor):
        self.raiz = raiz.resolve()
        self.pasta_painel = (self.raiz / "painel").resolve()
        self.motor = motor
        self._cache_instalado: tuple[float, bool] | None = None
        self._cache_atualizacao: tuple[float, dict] | None = None
        self._cache_nav: tuple[float, dict] | None = None

    def ler_versao(self) -> str:
        arq = self.raiz / "VERSION"
        if arq.is_file():
            try:
                v = arq.read_text(encoding="utf-8-sig").strip().lstrip("vV")
                if v:
                    return v
            except OSError:
                pass
        init_py = self.raiz / "cortelive" / "__init__.py"
        if init_py.is_file():
            try:
                m = re.search(r'__version__\s*=\s*"([^"]+)"', init_py.read_text(encoding="utf-8-sig"))
                if m:
                    return m.group(1).strip()
            except OSError:
                pass
        return "0.1.0"

    def _tem_ffmpeg(self) -> bool:
        fb = self.raiz / "ffmpeg" / "bin"
        if (fb / "ffmpeg.exe").is_file() and (fb / "ffprobe.exe").is_file():
            return True
        for p in (os.environ.get("PATH") or "").split(os.pathsep):
            p = p.strip()
            if not p:
                continue
            d = Path(p)
            if (d / "ffmpeg.exe").is_file() and (d / "ffprobe.exe").is_file():
                return True
        return False

    def esta_instalado(self) -> bool:
        agora = time.monotonic()
        if self._cache_instalado and (agora - self._cache_instalado[0]) < 8.0:
            return self._cache_instalado[1]

        vpy = self.raiz / ".venv" / "Scripts" / "python.exe"
        hub = self.raiz / "modelos" / "hub"
        ok = False
        if vpy.is_file() and self._tem_ffmpeg() and hub.is_dir():
            soma = 0
            try:
                for raiz_d, _, arqs in os.walk(hub):
                    for a in arqs:
                        try:
                            soma += (Path(raiz_d) / a).stat().st_size
                        except OSError:
                            pass
                        if soma >= 100 * 1024 * 1024:
                            break
                    if soma >= 100 * 1024 * 1024:
                        break
            except OSError:
                pass
            ok = soma >= 100 * 1024 * 1024

        self._cache_instalado = (agora, ok)
        return ok

    def ler_modo_descricao(self) -> str:
        arq = self.raiz / "instalador" / "modo.txt"
        if arq.is_file():
            try:
                for linha in arq.read_text(encoding="utf-8-sig").splitlines():
                    if linha.startswith("modo="):
                        m = linha[5:].strip().lower()
                        if m == "nvidia":
                            return "Modo NVIDIA ativo (transcrição CUDA + vídeo NVENC na placa)"
                        if m == "amd":
                            return "Modo AMD ativo (fala no processador + vídeo AMF na placa)"
                        if m == "intel":
                            return "Modo Intel ativo (fala no processador + vídeo QuickSync na placa)"
                        return "Modo Processador (CPU) ativo"
            except OSError:
                pass
        return "Ambiente local pronto para cortar"

    def _cortes_do_motor(self) -> list:
        status, _, dados = self.motor.chamar("GET", "/api/cortes")
        if status == 200:
            try:
                obj = json.loads(dados.decode("utf-8"))
                if isinstance(obj, dict) and isinstance(obj.get("cortes"), list):
                    return obj["cortes"]
            except ValueError:
                pass
        return []

    def montar_estado(self) -> dict:
        versao = self.ler_versao()
        instalado = self.esta_instalado()
        cortes = self._cortes_do_motor() if instalado else []
        return {
            "ok": True,
            "versao": versao,
            "pasta_raiz": str(self.raiz),
            "instalado": instalado,
            "modo_descricao": self.ler_modo_descricao() if instalado else "",
            "instalacao": {
                "rodando": False,
                "etapa": 7 if instalado else 0,
                "porcentagem": 100 if instalado else 0,
                "titulo_etapa": "",
                "detalhe": "",
                "erro": None,
                "o_que_fazer": None,
                "log_recente": "",
            },
            "sessao": {
                "rodando": False,
                "estado": "parado",
                "canal": "",
                "live_s": 0.0,
                "atraso_s": None,
                "cortes_sessao": 0,
                "ultima_mensagem": "",
                "detalhe_servicos": "",
                "erro": None,
                "log_recente": "",
                "log_completo": "",
                "lives": [],
            },
            "cortes": cortes,
            "atualizacao": {
                "rodando": False,
                "etapa": "ocioso",
                "pct": 0,
                "msg": "",
                "versao_atual": versao,
                "versao_nova": None,
                "erro": None,
            },
        }

    def checar_atualizacao_suave(self) -> dict:
        agora = time.monotonic()
        if self._cache_atualizacao and (agora - self._cache_atualizacao[0]) < 600.0:
            return self._cache_atualizacao[1]
        versao = self.ler_versao()
        res = {
            "ok": True,
            "etapa": "em_dia",
            "pct": 100,
            "msg": "Versão local em desenvolvimento (modo Ao vivo).",
            "versao_atual": versao,
            "versao_nova": None,
            "erro": None,
            "exit_code": 0,
        }
        self._cache_atualizacao = (agora, res)
        return res

    def status_navegador_reserva(self) -> dict:
        agora = time.monotonic()
        if self._cache_nav and (agora - self._cache_nav[0]) < 20.0:
            return self._cache_nav[1]
        pasta_mod = self.raiz / "cortelive" / "autopost_navegador"
        vpy = self.raiz / ".venv" / "Scripts" / "python.exe"
        if not vpy.is_file() or not (pasta_mod / "__init__.py").is_file():
            res = {"ok": False, "disponivel": False}
            self._cache_nav = (agora, res)
            return res
        try:
            proc = subprocess.run(
                [str(vpy), "-m", "cortelive.autopost_navegador", "status", "todas", "--json"],
                cwd=str(self.raiz),
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=12,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            redes = {}
            for l in reversed(proc.stdout.splitlines()):
                l = l.strip()
                if l.startswith("{") and l.endswith("}"):
                    try:
                        redes = json.loads(l)
                        break
                    except ValueError:
                        pass
            res = {"ok": True, "disponivel": True, "redes": redes}
        except Exception:
            res = {"ok": False, "disponivel": False}
        self._cache_nav = (agora, res)
        return res

    def rota_host(self, metodo: str, rota: str) -> tuple[int, dict] | None:
        if rota == "/api/ping":
            return 200, {"ok": True, "versao": self.ler_versao()}
        if rota in ("/api/heartbeat", "/api/janela-fechada"):
            return 200, {"ok": True}
        if rota == "/api/estado" and metodo == "GET":
            return 200, self.montar_estado()
        if rota == "/api/log-instalacao" and metodo == "GET":
            arq = self.raiz / "instalacao_log.txt"
            log = ""
            if arq.is_file():
                try:
                    log = arq.read_text(encoding="utf-8", errors="replace")[-12000:]
                except OSError:
                    pass
            return 200, {"ok": True, "log": log}
        if rota == "/api/atualizacao/checar" and metodo == "GET":
            return 200, self.checar_atualizacao_suave()
        if rota == "/api/autopost-navegador/status" and metodo == "GET":
            return 200, self.status_navegador_reserva()
        if rota == "/api/escolher-arquivo" and metodo == "POST":
            return 200, {"ok": True, "caminho": ""}
        return None

    def servir_estatico(self, rota: str) -> tuple[int, str, bytes]:
        rel = "index.html" if rota in ("", "/") else rota.lstrip("/")
        try:
            alvo = (self.pasta_painel / rel).resolve()
        except OSError:
            return 404, "text/plain; charset=utf-8", "Não encontrado".encode("utf-8")

        if not alvo.is_relative_to(self.pasta_painel) or not alvo.is_file():
            return 404, "text/plain; charset=utf-8", "Não encontrado".encode("utf-8")

        ext = alvo.suffix.lower()
        mime = MIMES_PAINEL.get(ext) or mimetypes.guess_type(str(alvo))[0] or "application/octet-stream"
        try:
            dados = alvo.read_bytes()
        except OSError as exc:
            return 500, "text/plain; charset=utf-8", f"Erro ao ler arquivo: {exc}".encode("utf-8")
        return 200, mime, dados


class MonitorMudancas:
    """Vigia painel\\ e cortelive\\ por mtime, guarda versões em memória, calcula
    diffs e reinicia motor_painel.py quando necessário."""

    def __init__(self, raiz: Path, motor: GerenciadorMotor):
        self.raiz = raiz.resolve()
        self.motor = motor
        self.pastas_vigiadas = [self.raiz / "painel", self.raiz / "cortelive"]
        self._lock = threading.RLock()
        self._arquivos: dict[str, dict] = {}
        self._diario: deque[dict] = deque(maxlen=MAX_ITENS_DIARIO)
        self._proximo_id = 1
        self._versao_reload = 1
        self._ultimo_recarregamento = agora_hms()
        self._parar = threading.Event()
        self._capturar_snapshot_inicial()

    def _ignorar_arquivo(self, caminho: Path) -> bool:
        for parte in caminho.parts:
            if parte == "__pycache__" or parte.startswith(".git"):
                return True
        nome = caminho.name
        if nome.startswith(".") or nome.endswith((".pyc", ".pyo", ".tmp", "~")):
            return True
        return False

    def _ler_snapshot_arquivo(self, caminho: Path) -> dict | None:
        try:
            st = caminho.stat()
        except OSError:
            return None

        ext = caminho.suffix.lower()
        eh_binario = ext in EXTENSOES_BINARIAS or st.st_size > MAX_BYTES_TEXTO
        if eh_binario:
            resumo_hash = f"bin:{st.st_size}:{st.st_mtime_ns}"
            try:
                if st.st_size <= 512 * 1024:
                    resumo_hash = hashlib.sha1(caminho.read_bytes()).hexdigest()
            except OSError:
                pass
            return {
                "mtime_ns": st.st_mtime_ns,
                "tamanho": st.st_size,
                "binario": True,
                "hash": resumo_hash,
                "linhas": [],
            }

        try:
            bruto = caminho.read_bytes()
        except OSError:
            return None

        if b"\x00" in bruto[:4096]:
            return {
                "mtime_ns": st.st_mtime_ns,
                "tamanho": st.st_size,
                "binario": True,
                "hash": hashlib.sha1(bruto).hexdigest(),
                "linhas": [],
            }

        try:
            texto = bruto.decode("utf-8-sig")
        except UnicodeDecodeError:
            texto = bruto.decode("latin-1", errors="replace")

        return {
            "mtime_ns": st.st_mtime_ns,
            "tamanho": st.st_size,
            "binario": False,
            "hash": hashlib.sha1(bruto).hexdigest(),
            "linhas": texto.splitlines(),
        }

    def _listar_atuais(self) -> dict[str, Path]:
        encontrados: dict[str, Path] = {}
        for pasta in self.pastas_vigiadas:
            if not pasta.is_dir():
                continue
            try:
                for dirpath, dirnames, filenames in os.walk(pasta):
                    dirnames[:] = [
                        d for d in dirnames
                        if d != "__pycache__" and not d.startswith(".")
                    ]
                    for fn in filenames:
                        p = Path(dirpath) / fn
                        if self._ignorar_arquivo(p):
                            continue
                        rel = str(p.relative_to(self.raiz)).replace("/", "\\")
                        encontrados[rel] = p
            except OSError:
                continue
        return encontrados

    def _capturar_snapshot_inicial(self):
        atuais = self._listar_atuais()
        with self._lock:
            for rel, p in atuais.items():
                snap = self._ler_snapshot_arquivo(p)
                if snap is not None:
                    self._arquivos[rel] = snap

    def _montar_diff(self, rel: str, antes: dict | None, agora: dict | None) -> tuple[str, int, int, int, str]:
        if antes is None and agora is not None:
            tipo = "novo"
        elif antes is not None and agora is None:
            tipo = "removido"
        else:
            tipo = "alterado"

        if (antes and antes["binario"]) or (agora and agora["binario"]):
            tam_antes = antes["tamanho"] if antes else 0
            tam_agora = agora["tamanho"] if agora else 0
            trecho = (
                f"Arquivo binário/mídia ({tipo})\n"
                f"--- antes/{rel} ({tam_antes} bytes)\n"
                f"+++ agora/{rel} ({tam_agora} bytes)"
            )
            return tipo, 0, 0, 1, trecho

        linhas_antes = antes["linhas"] if antes else []
        linhas_agora = agora["linhas"] if agora else []

        diff_gerado = list(
            difflib.unified_diff(
                linhas_antes,
                linhas_agora,
                fromfile=f"antes/{rel}",
                tofile=f"agora/{rel}",
                lineterm="",
                n=3,
            )
        )
        linhas_diff = diff_gerado[2:]  # Os dois cabeçalhos precedem os trechos do diff.
        adicionadas = sum(1 for linha in linhas_diff if linha.startswith("+"))
        removidas = sum(1 for linha in linhas_diff if linha.startswith("-"))
        total = adicionadas + removidas

        if len(diff_gerado) > MAX_LINHAS_DIFF:
            ocultas = len(diff_gerado) - MAX_LINHAS_DIFF
            trecho = "\n".join(diff_gerado[:MAX_LINHAS_DIFF]) + f"\n… (+{ocultas} linhas no diff)"
        elif diff_gerado:
            trecho = "\n".join(diff_gerado)
        else:
            trecho = "(Arquivo salvo novamente sem diferença de texto)"

        return tipo, adicionadas, removidas, total, trecho

    def verificar_uma_vez(self):
        atuais = self._listar_atuais()
        mudancas_lote: list[dict] = []
        reiniciar_motor = False

        with self._lock:
            conhecidos = set(self._arquivos.keys())
            vistos = set(atuais.keys())

            # Arquivos novos ou modificados
            for rel in sorted(vistos):
                caminho = atuais[rel]
                antigo = self._arquivos.get(rel)
                try:
                    st = caminho.stat()
                except OSError:
                    continue

                if antigo is not None and st.st_mtime_ns == antigo["mtime_ns"] and st.st_size == antigo["tamanho"]:
                    continue

                novo = self._ler_snapshot_arquivo(caminho)
                if novo is None:
                    continue

                self._arquivos[rel] = novo
                tipo, add, rem, total, trecho = self._montar_diff(rel, antigo, novo)
                if rel.lower() == r"painel\motor_painel.py" or (
                    rel.lower().startswith("cortelive\\") and rel.lower().endswith(".py")
                ):
                    reiniciar_motor = True

                mudancas_lote.append({
                    "id": self._proximo_id,
                    "hora": agora_hms(),
                    "arquivo": rel,
                    "tipo": tipo,
                    "adicionadas": add,
                    "removidas": rem,
                    "linhas_mudaram": total,
                    "reiniciou_motor": rel.lower() == r"painel\motor_painel.py",
                    "diff": trecho,
                })
                self._proximo_id += 1

            # Arquivos removidos
            for rel in sorted(conhecidos - vistos):
                antigo = self._arquivos.pop(rel, None)
                tipo, add, rem, total, trecho = self._montar_diff(rel, antigo, None)
                if rel.lower() == r"painel\motor_painel.py":
                    reiniciar_motor = True
                mudancas_lote.append({
                    "id": self._proximo_id,
                    "hora": agora_hms(),
                    "arquivo": rel,
                    "tipo": tipo,
                    "adicionadas": add,
                    "removidas": rem,
                    "linhas_mudaram": total,
                    "reiniciou_motor": rel.lower() == r"painel\motor_painel.py",
                    "diff": trecho,
                })
                self._proximo_id += 1

        if not mudancas_lote:
            return

        if reiniciar_motor:
            self.motor.reiniciar()

        with self._lock:
            for item in mudancas_lote:
                self._diario.appendleft(item)
            self._versao_reload += 1
            self._ultimo_recarregamento = agora_hms()

    def iniciar_thread(self):
        def loop():
            while not self._parar.wait(INTERVALO_POLL_ARQUIVOS_S):
                try:
                    self.verificar_uma_vez()
                except Exception:
                    pass

        threading.Thread(target=loop, daemon=True).start()

    def parar(self):
        self._parar.set()

    def marcar_recarregamento_manual(self):
        with self._lock:
            self._versao_reload += 1
            self._ultimo_recarregamento = agora_hms()

    def snapshot_estado(self) -> dict:
        st_motor = self.motor.estado()
        with self._lock:
            return {
                "ok": True,
                "versao_reload": self._versao_reload,
                "ultimo_recarregamento": self._ultimo_recarregamento,
                "arquivos_monitorados": len(self._arquivos),
                "motor": st_motor,
                "mudancas": list(self._diario),
            }


PAGINA_ERRO_PAINEL_HTML = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <title>Erro no motor do CorteLive</title>
  <style>
    body {{
      margin: 0; padding: 32px; background: #0f1116; color: #f1f5f9;
      font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
    }}
    .caixa {{
      max-width: 760px; margin: 40px auto; padding: 24px 28px;
      background: #1a1215; border: 1px solid #ef4444; border-radius: 14px;
    }}
    h1 {{ margin: 0 0 8px; font-size: 20px; color: #fca5a5; }}
    p {{ margin: 0 0 16px; color: #cbd5e1; line-height: 1.5; font-size: 14px; }}
    pre {{
      margin: 0; padding: 14px; background: #090b0e; color: #fecaca;
      border-radius: 8px; overflow-x: auto; font-size: 12.5px; line-height: 1.45;
      white-space: pre-wrap; word-break: break-word;
    }}
  </style>
</head>
<body>
  <div class="caixa">
    <h1>O motor do painel (motor_painel.py) parou</h1>
    <p>Assim que o arquivo for salvo novamente, o modo Ao vivo reinicia o motor e recarrega esta tela sozinho.</p>
    <pre>{erro}</pre>
  </div>
</body>
</html>
"""


PAGINA_AO_VIVO_HTML = r"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>CorteLive — Ao vivo (Acompanhamento de Mudanças)</title>
  <style>
    :root {
      --bg: #0b0d12;
      --bg-card: #131720;
      --bg-card-hover: #191f2b;
      --bg-diff: #090b10;
      --borda: #232a38;
      --texto: #f1f5f9;
      --texto-suave: #94a3b8;
      --verde: #22c55e;
      --verde-bg: rgba(34, 197, 94, 0.14);
      --vermelho: #ef4444;
      --vermelho-bg: rgba(239, 68, 68, 0.15);
      --ambar: #f59e0b;
      --azul: #38bdf8;
    }
    * { box-sizing: border-box; }
    html, body {
      margin: 0; padding: 0; width: 100%; height: 100%;
      background: var(--bg); color: var(--texto);
      font-family: "Segoe UI", system-ui, -apple-system, sans-serif;
      overflow: hidden;
    }
    body { display: flex; flex-direction: column; }
    .abas-visualizacao { display: none; }
    .aba-visualizacao {
      flex: 1; min-height: 44px;
      border: 0; border-bottom: 2px solid transparent;
      background: transparent; color: var(--texto-suave);
      font: inherit; font-weight: 600; cursor: pointer;
    }
    .aba-visualizacao:hover { background: var(--bg-card-hover); }
    .aba-visualizacao[aria-selected="true"] {
      border-bottom-color: var(--azul); color: var(--texto);
    }
    .layout {
      display: grid; grid-template-columns: minmax(0, 7fr) minmax(0, 3fr);
      flex: 1; min-height: 0;
    }
    .coluna-painel, .coluna-diario {
      display: flex; flex-direction: column; min-height: 0; min-width: 0;
    }
    /* Esquerda ~70%: Painel real */
    .coluna-painel {
      border-right: 1px solid var(--borda);
      background: #0e1117;
    }
    .barra-topo {
      min-height: 44px;
      display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap;
      padding: 0 12px; gap: 0 12px;
      background: #10141d; border-bottom: 1px solid var(--borda);
      font-size: 13px;
    }
    .grupo-status {
      display: flex; flex-wrap: wrap; align-items: center; gap: 4px 8px;
      padding: 8px 0; min-width: 0;
    }
    .ponto-vivo {
      width: 9px; height: 9px; border-radius: 50%;
      background: var(--verde);
      box-shadow: 0 0 0 3px rgba(34, 197, 94, 0.22);
      flex-shrink: 0;
    }
    .ponto-vivo.erro {
      background: var(--vermelho);
      box-shadow: 0 0 0 3px rgba(239, 68, 68, 0.25);
    }
    .ponto-vivo.reiniciando {
      background: var(--ambar);
      box-shadow: 0 0 0 3px rgba(245, 158, 11, 0.25);
    }
    .titulo-barra {
      font-weight: 700; letter-spacing: -0.01em; white-space: nowrap;
    }
    .pilula-motor {
      padding: 2px 9px; border-radius: 999px; font-size: 12px; font-weight: 600;
      background: var(--verde-bg); color: #86efac; border: 1px solid rgba(34, 197, 94, 0.3);
    }
    .pilula-motor.erro {
      background: var(--vermelho-bg); color: #fca5a5; border-color: rgba(239, 68, 68, 0.4);
    }
    .acoes-barra {
      display: flex; flex-wrap: wrap; align-items: center; gap: 0 8px; min-width: 0;
    }
    .info-recarregamento {
      color: var(--texto-suave); font-size: 12px; white-space: nowrap;
    }
    .info-recarregamento strong {
      color: var(--texto); font-variant-numeric: tabular-nums;
    }
    .btn-acao {
      background: #1a2130; color: var(--texto);
      border: 1px solid #2b3548; border-radius: 7px;
      padding: 5px 10px; font-size: 12px; font-weight: 600;
      cursor: pointer; transition: background 0.15s, border-color 0.15s;
    }
    .btn-acao:hover { background: #232c40; border-color: #3b4963; }
    .palco-iframe {
      flex: 1; position: relative; min-height: 0;
      background: #0e1117;
    }
    #iframe-painel {
      width: 100%; height: 100%; border: 0; display: block; background: #0e1117;
    }
    .overlay-erro {
      position: absolute; inset: 0; z-index: 20;
      background: rgba(11, 13, 18, 0.94);
      display: flex; align-items: center; justify-content: center;
      padding: 28px;
    }
    .overlay-erro[hidden] { display: none; }
    .card-erro-motor {
      width: 100%; max-width: 720px;
      background: #191114; border: 1px solid #ef4444;
      border-radius: 14px; padding: 22px 24px;
      box-shadow: 0 20px 50px rgba(0, 0, 0, 0.55);
    }
    .card-erro-motor h2 {
      margin: 0 0 8px; font-size: 18px; color: #fca5a5;
    }
    .card-erro-motor p {
      margin: 0 0 14px; font-size: 13.5px; color: #cbd5e1; line-height: 1.5;
    }
    .card-erro-motor pre {
      margin: 0 0 14px; padding: 12px 14px; max-height: 280px; overflow: auto;
      background: #090b0f; border: 1px solid rgba(239, 68, 68, 0.3);
      border-radius: 8px; color: #fecaca; font-size: 12px; line-height: 1.45;
      white-space: pre-wrap; word-break: break-word;
    }

    /* Direita ~30%: Diário de mudanças */
    .coluna-diario {
      background: #0e121a;
    }
    .cabecalho-diario {
      padding: 12px 16px; border-bottom: 1px solid var(--borda);
      background: #101520;
    }
    .linha-titulo-diario {
      display: flex; align-items: center; justify-content: space-between; gap: 8px;
    }
    .cabecalho-diario h2 {
      margin: 0; font-size: 15px; font-weight: 700; letter-spacing: -0.01em;
    }
    .contador-mudancas {
      padding: 2px 8px; border-radius: 999px; font-size: 11.5px; font-weight: 700;
      background: #1e293b; color: #e2e8f0; font-variant-numeric: tabular-nums;
    }
    .subtitulo-diario {
      margin: 6px 0 0; font-size: 12px; color: var(--texto-suave);
      display: flex; flex-wrap: wrap; justify-content: space-between; gap: 6px;
    }
    .subtitulo-diario strong {
      color: #e2e8f0; font-variant-numeric: tabular-nums;
    }
    .lista-diario {
      flex: 1; min-height: 0; overflow-y: auto; padding: 12px;
      display: flex; flex-direction: column; gap: 8px;
    }
    .lista-diario > * { flex: 0 0 auto; }
    .estado-aguardando {
      padding: 18px 16px; border: 1px dashed #293447; border-radius: 10px;
      background: rgba(19, 23, 32, 0.65); color: var(--texto-suave);
      font-size: 13px; line-height: 1.55;
    }
    .estado-aguardando strong { color: var(--texto); display: block; margin-bottom: 4px; }
    .item-mudanca {
      background: var(--bg-card); border: 1px solid var(--borda);
      border-radius: 10px; overflow: hidden;
      transition: border-color 0.15s, background 0.15s;
    }
    .item-mudanca:hover { border-color: #334155; }
    .item-cabecalho {
      width: 100%; min-height: 44px; text-align: left; background: transparent; border: 0;
      color: inherit; padding: 10px 12px; cursor: pointer;
      display: flex; flex-direction: column; gap: 5px;
    }
    .item-cabecalho:hover { background: var(--bg-card-hover); }
    .item-linha-1 {
      display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 4px 8px;
    }
    .item-hora {
      font-size: 11.5px; color: var(--texto-suave); font-variant-numeric: tabular-nums;
    }
    .item-badges {
      display: flex; flex-wrap: wrap; align-items: center; gap: 5px; font-size: 11.5px; font-weight: 700;
      font-variant-numeric: tabular-nums;
    }
    .badge-add { color: #4ade80; }
    .badge-rem { color: #f87171; }
    .badge-total {
      color: #cbd5e1; background: #1e2636; padding: 1px 6px; border-radius: 5px;
      font-weight: 600;
    }
    .badge-motor {
      color: #fbbf24; background: rgba(245, 158, 11, 0.15);
      border: 1px solid rgba(245, 158, 11, 0.35);
      padding: 1px 6px; border-radius: 5px; font-size: 10.5px;
    }
    .item-arquivo {
      font-family: Consolas, "Cascadia Mono", monospace;
      font-size: 12.5px; color: #f8fafc; word-break: break-all;
    }
    .item-dica {
      font-size: 11px; color: var(--texto-suave);
    }
    .bloco-diff {
      border-top: 1px solid var(--borda);
      background: var(--bg-diff); padding: 10px 12px;
      font-family: Consolas, "Cascadia Mono", monospace;
      font-size: 11.5px; line-height: 1.45;
      overflow-x: auto; max-height: 320px; overflow-y: auto;
    }
    .bloco-diff[hidden] { display: none; }
    .diff-linha { white-space: pre; }
    .diff-add { color: #86efac; background: rgba(34, 197, 94, 0.10); }
    .diff-rem { color: #fca5a5; background: rgba(239, 68, 68, 0.12); }
    .diff-hunk { color: #38bdf8; }
    .diff-hdr { color: #94a3b8; font-weight: 600; }
    button:focus-visible {
      outline: 2px solid var(--azul); outline-offset: -3px;
    }
    @media (max-width: 1066px) {
      .abas-visualizacao {
        display: flex; background: var(--bg-card); border-bottom: 1px solid var(--borda);
      }
      .layout { grid-template-columns: minmax(0, 1fr); }
      .layout > [hidden] { display: none; }
      .coluna-painel { border-right: 0; }
    }
    @media (prefers-reduced-motion: reduce) {
      .item-mudanca, .btn-acao { transition: none; }
    }
  </style>
</head>
<body>
  <div class="abas-visualizacao" role="tablist" aria-label="Visualização ao vivo">
    <button type="button" id="aba-painel" class="aba-visualizacao" role="tab"
      aria-controls="vista-painel" aria-selected="true">Painel</button>
    <button type="button" id="aba-diario" class="aba-visualizacao" role="tab"
      aria-controls="vista-diario" aria-selected="false" tabindex="-1">Mudanças</button>
  </div>
  <div class="layout">
    <section id="vista-painel" class="coluna-painel" aria-label="Painel">
      <header class="barra-topo">
        <div class="grupo-status">
          <span id="ponto-vivo" class="ponto-vivo"></span>
          <span class="titulo-barra">CorteLive — Ao vivo</span>
          <span id="pilula-motor" class="pilula-motor">Conectando ao motor…</span>
        </div>
        <div class="acoes-barra">
          <span class="info-recarregamento">
            Último recarregamento: <strong id="hora-recarregamento-topo">--:--:--</strong>
          </span>
          <button type="button" id="btn-recarregar" class="btn-acao">Recarregar painel</button>
          <button type="button" id="btn-reiniciar-motor" class="btn-acao">Reiniciar motor</button>
        </div>
      </header>

      <div class="palco-iframe">
        <iframe id="iframe-painel" title="Painel real do CorteLive" src="__URL_PAINEL_INICIAL__"></iframe>

        <div id="overlay-erro" class="overlay-erro" hidden>
          <div class="card-erro-motor" role="alert">
            <h2>O motor do painel encontrou um erro</h2>
            <p>
              O arquivo <code>painel\motor_painel.py</code> parou ou falhou ao iniciar.
              Continue acompanhando: assim que um ajudante salvar a correção, o motor reinicia e o painel volta sozinho.
            </p>
            <pre id="pre-erro-motor"></pre>
            <button type="button" id="btn-tentar-motor" class="btn-acao">Tentar reiniciar o motor agora</button>
          </div>
        </div>
      </div>
    </section>

    <aside id="vista-diario" class="coluna-diario" aria-label="Mudanças">
      <div class="cabecalho-diario">
        <div class="linha-titulo-diario">
          <h2>Diário de mudanças</h2>
          <span id="contador-mudancas" class="contador-mudancas">0 alterações</span>
        </div>
        <div class="subtitulo-diario">
          <span>Último recarregamento: <strong id="hora-recarregamento-diario">--:--:--</strong></span>
          <span id="resumo-vigiados">Vigiando painel\ e cortelive\</span>
        </div>
      </div>

      <div id="lista-diario" class="lista-diario"></div>
    </aside>
  </div>

  <script>
    (function () {
      "use strict";

      const URL_BASE_PAINEL = "__URL_PAINEL_BASE__";
      const iframe = document.getElementById("iframe-painel");
      const pontoVivo = document.getElementById("ponto-vivo");
      const pilulaMotor = document.getElementById("pilula-motor");
      const horaTopo = document.getElementById("hora-recarregamento-topo");
      const horaDiario = document.getElementById("hora-recarregamento-diario");
      const resumoVigiados = document.getElementById("resumo-vigiados");
      const contadorMudancas = document.getElementById("contador-mudancas");
      const listaDiario = document.getElementById("lista-diario");
      const overlayErro = document.getElementById("overlay-erro");
      const preErroMotor = document.getElementById("pre-erro-motor");

      let versaoAtual = null;
      let assinaturaDiario = "";
      const abertos = new Set();

      const janelaEstreita = window.matchMedia("(max-width: 1066px)");
      const abas = [...document.querySelectorAll(".aba-visualizacao")];
      let abaAtiva = abas[0];

      function atualizarAbas() {
        for (const aba of abas) {
          const ativa = aba === abaAtiva;
          aba.setAttribute("aria-selected", String(ativa));
          aba.tabIndex = ativa ? 0 : -1;
          const vista = document.getElementById(aba.getAttribute("aria-controls"));
          vista.hidden = janelaEstreita.matches && !ativa;
          if (janelaEstreita.matches) {
            vista.setAttribute("role", "tabpanel");
            vista.setAttribute("aria-labelledby", aba.id);
          } else {
            vista.removeAttribute("role");
            vista.removeAttribute("aria-labelledby");
          }
        }
      }

      for (const aba of abas) {
        aba.addEventListener("click", () => {
          abaAtiva = aba;
          atualizarAbas();
        });
        aba.addEventListener("keydown", (evento) => {
          const indice = abas.indexOf(aba);
          const destinos = { ArrowLeft: 1 - indice, ArrowRight: 1 - indice, Home: 0, End: 1 };
          if (!(evento.key in destinos)) return;
          evento.preventDefault();
          const destino = abas[destinos[evento.key]];
          destino.click();
          destino.focus();
        });
      }
      janelaEstreita.addEventListener("change", atualizarAbas);
      atualizarAbas();

      function recarregarIframe(forcarHora) {
        iframe.src = URL_BASE_PAINEL + "/?_av=" + Date.now();
        if (forcarHora) {
          const agora = new Date().toLocaleTimeString("pt-BR", { hour12: false });
          horaTopo.textContent = agora;
          horaDiario.textContent = agora;
        }
      }

      function escaparHtml(s) {
        return String(s || "")
          .replace(/&/g, "&amp;")
          .replace(/</g, "&lt;")
          .replace(/>/g, "&gt;")
          .replace(/"/g, "&quot;");
      }

      function formatarDiffHtml(texto) {
        let noTrecho = false;
        return String(texto || "").split("\n").map((linha) => {
          let cls = "diff-linha";
          if (linha.startsWith("@@")) {
            noTrecho = true;
            cls += " diff-hunk";
          }
          else if (!noTrecho && (linha.startsWith("+++ ") || linha.startsWith("--- "))) cls += " diff-hdr";
          else if (linha.startsWith("+")) cls += " diff-add";
          else if (linha.startsWith("-")) cls += " diff-rem";
          return `<div class="${cls}">${escaparHtml(linha) || " "}</div>`;
        }).join("");
      }

      function renderizarDiario(mudancas, arquivosMonitorados, horaUltimo) {
        const qtd = mudancas.length;
        contadorMudancas.textContent = qtd === 1 ? "1 alteração" : `${qtd} alterações`;
        resumoVigiados.textContent = `${arquivosMonitorados} arquivos vigiados em painel\\ e cortelive\\`;

        const novaAssinatura = qtd === 0
          ? "vazio:" + arquivosMonitorados
          : mudancas.map((m) => m.id).join(",");

        if (novaAssinatura === assinaturaDiario) return;
        assinaturaDiario = novaAssinatura;

        if (qtd === 0) {
          listaDiario.innerHTML = `
            <div class="estado-aguardando">
              <strong>Monitorando alterações em tempo real</strong>
              Vigiando <b>${arquivosMonitorados}</b> arquivos em <code>painel\\</code> e <code>cortelive\\</code> (iniciado às ${escaparHtml(horaUltimo)}).<br><br>
              Ao salvar um arquivo, o painel recarrega sozinho e a mudança aparece no topo da lista.
            </div>
          `;
          return;
        }

        listaDiario.innerHTML = mudancas.map((m) => {
          const aberto = abertos.has(m.id);
          const rotuloLinhas = m.linhas_mudaram === 1 ? "1 linha mudou" : `${m.linhas_mudaram} linhas mudaram`;
          const tagTipo = m.tipo === "novo" ? " · novo" : (m.tipo === "removido" ? " · removido" : "");
          const badgeMotor = m.reiniciou_motor
            ? `<span class="badge-motor">motor reiniciado</span>`
            : "";
          return `
            <article class="item-mudanca" data-id="${m.id}">
              <button type="button" class="item-cabecalho" data-toggle="${m.id}" aria-controls="diff-${m.id}" aria-expanded="${aberto ? "true" : "false"}">
                <span class="item-linha-1">
                  <span class="item-hora">${escaparHtml(m.hora)}${tagTipo}</span>
                  <span class="item-badges">
                    ${badgeMotor}
                    <span class="badge-add">+${m.adicionadas}</span>
                    <span class="badge-rem">−${m.removidas}</span>
                    <span class="badge-total">${rotuloLinhas}</span>
                  </span>
                </span>
                <span class="item-arquivo">${escaparHtml(m.arquivo)}</span>
                <span class="item-dica">${aberto ? "Recolher diff" : "Ver diff"}</span>
              </button>
              <div class="bloco-diff" id="diff-${m.id}" ${aberto ? "" : "hidden"}>${formatarDiffHtml(m.diff)}</div>
            </article>
          `;
        }).join("");

        listaDiario.querySelectorAll("[data-toggle]").forEach((btn) => {
          btn.addEventListener("click", () => {
            const id = Number(btn.getAttribute("data-toggle"));
            const bloco = document.getElementById("diff-" + id);
            const dica = btn.querySelector(".item-dica");
            if (abertos.has(id)) {
              abertos.delete(id);
              bloco.hidden = true;
              btn.setAttribute("aria-expanded", "false");
              if (dica) dica.textContent = "Ver diff";
            } else {
              abertos.add(id);
              bloco.hidden = false;
              btn.setAttribute("aria-expanded", "true");
              if (dica) dica.textContent = "Recolher diff";
            }
          });
        });
      }

      function atualizarStatusMotor(motor) {
        if (motor.reiniciando) {
          pontoVivo.className = "ponto-vivo reiniciando";
          pilulaMotor.className = "pilula-motor";
          pilulaMotor.textContent = "Reiniciando motor…";
          return;
        }
        if (motor.ok) {
          pontoVivo.className = "ponto-vivo";
          pilulaMotor.className = "pilula-motor";
          const sufixo = motor.reinicios > 0 ? ` · ${motor.reinicios}x reiniciado` : "";
          pilulaMotor.textContent = `Motor ativo (porta ${motor.porta}${sufixo})`;
          overlayErro.hidden = true;
        } else {
          pontoVivo.className = "ponto-vivo erro";
          pilulaMotor.className = "pilula-motor erro";
          pilulaMotor.textContent = "Erro no motor";
          preErroMotor.textContent = motor.erro || "O motor do painel parou.";
          overlayErro.hidden = false;
        }
      }

      async function consultarEstado() {
        try {
          const resp = await fetch("/__aovivo/estado", { cache: "no-store" });
          if (!resp.ok) throw new Error("HTTP " + resp.status);
          const dados = await resp.json();

          horaTopo.textContent = dados.ultimo_recarregamento || "--:--:--";
          horaDiario.textContent = dados.ultimo_recarregamento || "--:--:--";

          const motorEraErro = !overlayErro.hidden;
          atualizarStatusMotor(dados.motor || { ok: false, erro: "Sem resposta do motor." });
          renderizarDiario(dados.mudancas || [], dados.arquivos_monitorados || 0, dados.ultimo_recarregamento || "");

          if (versaoAtual === null) {
            versaoAtual = dados.versao_reload;
          } else if (dados.versao_reload !== versaoAtual) {
            versaoAtual = dados.versao_reload;
            if (dados.motor && dados.motor.ok) {
              recarregarIframe(false);
            }
          } else if (motorEraErro && dados.motor && dados.motor.ok) {
            recarregarIframe(false);
          }
        } catch (err) {
          pontoVivo.className = "ponto-vivo erro";
          pilulaMotor.className = "pilula-motor erro";
          pilulaMotor.textContent = "Servidor Ao vivo desconectado";
          preErroMotor.textContent = "Não foi possível consultar o servidor Ao vivo local (" + err.message + ").";
          overlayErro.hidden = false;
        }
      }

      async function acionarReinicioMotor() {
        pilulaMotor.textContent = "Reiniciando motor…";
        try {
          await fetch("/__aovivo/reiniciar-motor", { method: "POST" });
        } catch (_) {}
        await consultarEstado();
        recarregarIframe(true);
      }

      document.getElementById("btn-recarregar").addEventListener("click", () => recarregarIframe(true));
      document.getElementById("btn-reiniciar-motor").addEventListener("click", acionarReinicioMotor);
      document.getElementById("btn-tentar-motor").addEventListener("click", acionarReinicioMotor);

      consultarEstado();
      setInterval(consultarEstado, 600);
    })();
  </script>
</body>
</html>
"""


def criar_servidor_painel(host_comp: HostComplementarPainel, motor: GerenciadorMotor) -> ThreadingHTTPServer:
    class HandlerPainel(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def handle_one_request(self):
            self.connection.settimeout(35)
            super().handle_one_request()

        def _enviar(self, status: int, tipo: str, corpo: bytes):
            self.send_response(status)
            self.send_header("Content-Type", tipo)
            self.send_header("Content-Length", str(len(corpo)))
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
            self.end_headers()
            if corpo:
                self.wfile.write(corpo)

        def _enviar_json(self, status: int, obj: dict):
            corpo = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self._enviar(status, "application/json; charset=utf-8", corpo)

        def _executar(self):
            partes = urlsplit(self.path)
            rota = partes.path or "/"

            corpo_req = b""
            if self.command == "POST":
                try:
                    tam = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    tam = 0
                if tam > 0:
                    corpo_req = self.rfile.read(min(tam, 1048576))

            st_motor = motor.estado()
            if not st_motor["ok"]:
                if rota.startswith("/api/"):
                    self._enviar_json(502, {"ok": False, "erro": st_motor["erro"]})
                else:
                    pagina = PAGINA_ERRO_PAINEL_HTML.format(erro=html_escape(st_motor["erro"] or "")).encode("utf-8")
                    self._enviar(502, "text/html; charset=utf-8", pagina)
                return

            if rota in ("/api/heartbeat", "/api/janela-fechada"):
                self._enviar_json(200, {"ok": True})
                return

            if rota.startswith("/api/"):
                ct = self.headers.get("Content-Type", "application/json")
                if corpo_req.lstrip().startswith(b"{") and "application/json" not in ct.lower():
                    ct = "application/json"
                status, tipo, dados = motor.chamar(self.command, self.path, corpo_req, ct)
                # Se motor_painel.py respondeu 404 de rota ausente no motor, delega às rotas do host
                if status == 404:
                    eh_rota_ausente = True
                    try:
                        j = json.loads(dados.decode("utf-8"))
                        if isinstance(j, dict) and j.get("erro") not in (None, "Rota não encontrada."):
                            eh_rota_ausente = False
                    except ValueError:
                        pass
                    if eh_rota_ausente:
                        resposta_host = host_comp.rota_host(self.command, rota)
                        if resposta_host is not None:
                            st_h, obj_h = resposta_host
                            self._enviar_json(st_h, obj_h)
                            return
                self._enviar(status, tipo, dados)
                return

            status, tipo, dados = host_comp.servir_estatico(rota)
            self._enviar(status, tipo, dados)

        do_GET = do_POST = _executar

    return ThreadingHTTPServer(("127.0.0.1", 0), HandlerPainel)


def criar_servidor_ao_vivo(porta_preferida: int, porta_painel: int, monitor: MonitorMudancas, motor: GerenciadorMotor) -> ThreadingHTTPServer:
    url_base_painel = f"http://127.0.0.1:{porta_painel}"
    pagina_bytes = (
        PAGINA_AO_VIVO_HTML
        .replace("__URL_PAINEL_BASE__", url_base_painel)
        .replace("__URL_PAINEL_INICIAL__", f"{url_base_painel}/?_av={int(time.time())}")
        .encode("utf-8")
    )

    class HandlerAoVivo(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def handle_one_request(self):
            self.connection.settimeout(15)
            super().handle_one_request()

        def _enviar(self, status: int, tipo: str, corpo: bytes):
            self.send_response(status)
            self.send_header("Content-Type", tipo)
            self.send_header("Content-Length", str(len(corpo)))
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
            self.end_headers()
            if corpo:
                self.wfile.write(corpo)

        def _enviar_json(self, status: int, obj: dict):
            self._enviar(status, "application/json; charset=utf-8", json.dumps(obj, ensure_ascii=False).encode("utf-8"))

        def do_GET(self):
            rota = urlsplit(self.path).path or "/"
            if rota == "/__aovivo/ping":
                self._enviar_json(200, {"ok": True, "porta_painel": porta_painel})
                return
            if rota == "/__aovivo/estado":
                self._enviar_json(200, monitor.snapshot_estado())
                return
            if rota in ("/", "/index.html"):
                self._enviar(200, "text/html; charset=utf-8", pagina_bytes)
                return
            self._enviar(404, "text/plain; charset=utf-8", "Não encontrado".encode("utf-8"))

        def do_POST(self):
            rota = urlsplit(self.path).path or "/"
            if rota == "/__aovivo/reiniciar-motor":
                ok = motor.reiniciar()
                monitor.marcar_recarregamento_manual()
                self._enviar_json(200, {"ok": ok, "motor": motor.estado()})
                return
            self._enviar(404, "application/json; charset=utf-8", b'{"ok":false}')

    try:
        return ThreadingHTTPServer(("127.0.0.1", porta_preferida), HandlerAoVivo)
    except OSError:
        return ThreadingHTTPServer(("127.0.0.1", 0), HandlerAoVivo)


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    p = argparse.ArgumentParser(prog="ao_vivo")
    p.add_argument("--raiz", type=Path, default=RAIZ_PADRAO)
    p.add_argument("--porta", type=int, default=PORTA_PADRAO)
    args = p.parse_args(argv)

    raiz = args.raiz.resolve()
    motor = GerenciadorMotor(raiz)
    motor.iniciar(eh_reinicio=False)

    host_comp = HostComplementarPainel(raiz, motor)
    srv_painel = criar_servidor_painel(host_comp, motor)
    porta_painel = srv_painel.server_port

    monitor = MonitorMudancas(raiz, motor)
    srv_aovivo = criar_servidor_ao_vivo(args.porta, porta_painel, monitor, motor)
    porta_aovivo = srv_aovivo.server_port

    arq_instancia = Path(__file__).resolve().parent / "instancia.json"
    try:
        arq_instancia.write_text(
            json.dumps(
                {"porta": porta_aovivo, "porta_painel": porta_painel, "pid": os.getpid()},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except OSError:
        pass

    threading.Thread(target=srv_painel.serve_forever, daemon=True).start()
    monitor.iniciar_thread()

    encerrar_ev = threading.Event()

    def ao_sinal(*_):
        encerrar_ev.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, ao_sinal)
        except Exception:
            pass

    try:
        if sys.stdout is not None:
            print(
                json.dumps(
                    {
                        "ok": True,
                        "url": f"http://127.0.0.1:{porta_aovivo}",
                        "porta": porta_aovivo,
                        "porta_painel": porta_painel,
                        "motor": motor.estado(),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
    except Exception:
        pass

    threading.Thread(target=srv_aovivo.serve_forever, daemon=True).start()

    try:
        while not encerrar_ev.wait(0.5):
            pass
    except KeyboardInterrupt:
        pass
    finally:
        monitor.parar()
        try:
            srv_aovivo.shutdown()
        except Exception:
            pass
        try:
            srv_painel.shutdown()
        except Exception:
            pass
        motor.encerrar()
        try:
            arq_instancia.unlink(missing_ok=True)
        except OSError:
            pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
