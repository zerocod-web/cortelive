"""Destino dos cortes prontos (pasta do Google Drive, OneDrive ou Dropbox).

Cópia: cada corte pronto vai para <destino_pasta>/<AAAA-MM-DD HHhMM - canal>/<corte>.mp4. Roda numa thread
própria: o render não espera. O app do Drive/OneDrive/Dropbox sobe o arquivo sozinho; o CorteLive não usa
chave de API.
"""
from __future__ import annotations

import json
import os
import queue
import re
import shutil
import sqlite3
import string
import tempfile
import threading
import time
from concurrent.futures import Future
from contextlib import closing, contextmanager
from pathlib import Path

from . import pastas

PADRAO = {"destino_ativo": False, "destino_pasta": ""}
_NOMES_MEU_DRIVE = ("Meu Drive", "My Drive", "Mi unidad", "Mon Drive")
_trava = threading.Lock()
_fila: queue.Queue = queue.Queue()
_th: threading.Thread | None = None


class ErroDestino(Exception):
    """Mensagem pronta para mostrar à pessoa."""


# ------------------------------------------------------------------ config
def _ler_json(caminho: Path) -> dict:
    try:
        dados = json.loads(Path(caminho).read_text(encoding="utf-8-sig"))
        return dados if isinstance(dados, dict) else {}
    except (OSError, ValueError):
        return {}


def _gravar_json(caminho: Path, dados: dict) -> None:
    caminho = Path(caminho)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=caminho.parent, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(dados, f, ensure_ascii=False, indent=2)
        os.replace(tmp, caminho)
    finally:
        Path(tmp).unlink(missing_ok=True)


def opcao_valida(chave: str, valor) -> bool:
    if chave == "destino_pasta":
        return isinstance(valor, str) and "\0" not in valor and len(valor) <= 1024
    return chave in PADRAO and isinstance(valor, bool)


def ler_config(dados: dict | None = None) -> dict:
    """Padrões <- valores válidos de `dados` (config.toml ou painel_config.json)."""
    cfg = dict(PADRAO)
    for chave, valor in (dados or {}).items():
        if opcao_valida(chave, valor):
            cfg[chave] = valor.strip() if isinstance(valor, str) else valor
    return cfg


def config_atual(cfg: dict | None = None) -> dict:
    """Config do destino agora: cfg da sessão <- painel_config.json (lido de novo a cada corte,
    para valer a mudança feita no painel com a live rodando)."""
    cfg = cfg or {}
    raiz = Path(cfg.get("painel_raiz") or Path.cwd())
    painel = _ler_json(raiz / "painel_config.json")
    return ler_config({**cfg, **{k: v for k, v in painel.items() if opcao_valida(k, v)}})


def salvar_preferencias(cfg: dict, alteracoes: dict) -> dict:
    arquivo = Path(cfg["painel_raiz"]) / "painel_config.json"
    with _travar(arquivo.with_name("_painel_destino.sqlite3")):
        preferencias = ler_config({**config_atual(cfg), **alteracoes})
        if preferencias["destino_ativo"] or ("destino_pasta" in alteracoes and preferencias["destino_pasta"]):
            verificar_pasta(preferencias["destino_pasta"], cfg.get("pasta_saida"))
        _gravar_json(arquivo, {**_ler_json(arquivo), **{k: preferencias[k] for k in alteracoes}})
        return preferencias


# ------------------------------------------------------------------ detectar nuvens
def _drive_letras_registro() -> list[str]:
    """Pontos de montagem que o Google Drive para computador gravou no registro (conta e política)."""
    if os.name != "nt":
        return []
    import winreg
    achados = []
    for raiz in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(raiz, r"Software\Google\DriveFS") as k:
                for nome in ("DefaultMountPoint", "PerAccountPreferences"):
                    try:
                        valor, _ = winreg.QueryValueEx(k, nome)
                    except OSError:
                        continue
                    if nome == "DefaultMountPoint":
                        achados.append(str(valor))
                        continue
                    try:
                        prefs = json.loads(valor).get("per_account_preferences", [])
                    except (TypeError, ValueError, AttributeError):
                        continue
                    for conta in prefs if isinstance(prefs, list) else ():
                        detalhe = conta.get("value") if isinstance(conta, dict) else None
                        ponto = detalhe.get("mount_point_path") if isinstance(detalhe, dict) else None
                        if ponto:
                            achados.append(str(ponto))
        except OSError:
            continue
    return achados


def _letras_locais() -> list[str]:
    """Letras de unidade presentes, sem rede (4) nem CD (5): não trava em unidade desconectada."""
    if os.name != "nt":
        return []
    import ctypes
    k = ctypes.windll.kernel32
    mascara = k.GetLogicalDrives()
    letras = []
    for i, letra in enumerate(string.ascii_uppercase):
        if mascara >> i & 1 and letra not in "AB" and k.GetDriveTypeW(f"{letra}:\\") not in (4, 5):
            letras.append(letra)
    return letras


def _meu_drive(base: Path) -> Path | None:
    for nome in _NOMES_MEU_DRIVE:
        if (base / nome).is_dir():
            return base / nome
    return None


def detectar(env: dict | None = None, letras: list[str] | None = None,
             registro: list[str] | None = None) -> list[dict]:
    """-> [{nome, pasta_raiz, sugestao}] (Google Drive primeiro). Parâmetros só para os testes."""
    env = os.environ if env is None else env
    achados: list[dict] = []

    def add(nome, raiz: Path | None):
        if raiz and raiz.is_dir() and all(Path(a["pasta_raiz"]) != raiz for a in achados):
            achados.append({"nome": nome, "pasta_raiz": str(raiz), "sugestao": str(raiz / "CorteLive")})

    pontos = list(_drive_letras_registro() if registro is None else registro)
    pontos += [f"{L}:\\" for L in (_letras_locais() if letras is None else letras)]
    for ponto in pontos:
        texto = str(ponto).strip()
        if not texto:
            continue
        if len(texto) == 1:
            texto += ":"
        if len(texto) == 2 and texto[1] == ":":
            texto += "\\"
        base = Path(texto)
        add("Google Drive", base if base.name in _NOMES_MEU_DRIVE else _meu_drive(base))
    for chave in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        if env.get(chave):
            add("OneDrive", Path(env[chave]))
    for pasta in (env.get("LOCALAPPDATA"), env.get("APPDATA")):
        if not pasta:
            continue
        info = _ler_json(Path(pasta) / "Dropbox" / "info.json")
        for conta in ("personal", "business"):
            detalhe = info.get(conta)
            caminho = detalhe.get("path") if isinstance(detalhe, dict) else None
            if isinstance(caminho, str) and caminho:
                add("Dropbox", Path(caminho))
    return achados


# ------------------------------------------------------------------ caminhos
def subpasta_da_live(video: Path) -> str:
    """'AAAA-MM-DD HHhMM - canal' (pasta antiga 2026-11-17_1628_canal é convertida)."""
    nome = pastas.pasta_sessao_do_video(Path(video)).name
    m = re.match(r"^(\d{4}-\d{2}-\d{2})_(\d{2})(\d{2})_(.+)$", nome)
    return f"{m[1]} {m[2]}h{m[3]} - {m[4]}" if m else pastas.limpar(nome) or "CorteLive"


def _arq_copia(video: Path) -> Path:
    return pastas.interno_do_video(video) / f"{Path(video).stem}.destino.json"


def verificar_pasta(pasta: str, saida=None) -> Path:
    if not pasta:
        raise ErroDestino("Escolha a pasta para onde os cortes serão copiados.")
    raiz = Path(pasta)
    if not raiz.is_absolute():
        raise ErroDestino("Escolha o caminho completo da pasta do Drive.")
    raiz = raiz.resolve()
    if saida is not None and raiz.is_relative_to(Path(saida).resolve()):
        raise ErroDestino("Escolha uma pasta do Drive fora da pasta dos cortes.")
    if not raiz.is_dir() and (raiz.exists() or not raiz.parent.is_dir()):
        raise ErroDestino("A pasta do destino não está disponível. Abra o Google Drive no computador "
                          "ou escolha outra pasta.")
    return raiz


@contextmanager
def _travar(arquivo: Path):
    """O sistema libera a trava se o motor ou o processo do painel encerrar."""
    try:
        with closing(sqlite3.connect(arquivo, timeout=5)) as db:
            db.execute("BEGIN IMMEDIATE")
            yield
    except sqlite3.Error as erro:
        if getattr(erro, "sqlite_errorcode", None) in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
            raise TimeoutError("Esta pasta está ocupada. Aguarde e tente de novo.") from None
        raise OSError("Não consegui abrir o registro local.") from erro


def _trava_corte(video: Path) -> Path:
    return pastas.interno_do_video(video) / "_destino.sqlite3"


def estado_copia(video) -> dict | None:
    dados = _ler_json(_arq_copia(Path(video)))
    return dados or None


# ------------------------------------------------------------------ cópia
MENSAGEM_COPIA = "Corte copiado para a pasta de destino. Confira a sincronização no aplicativo da nuvem."


def copiar(video, cfg: dict | None = None) -> dict:
    """Copia agora e devolve o estado da operação e os caminhos dos arquivos prontos."""
    video = Path(video)
    cfg = cfg or {}
    dest = config_atual(cfg)
    agora = time.strftime("%Y-%m-%dT%H:%M:%S")
    try:
        with _travar(_trava_corte(video)):
            reg = estado_copia(video) or {}
            try:
                raiz = verificar_pasta(dest["destino_pasta"], cfg.get("pasta_saida"))
                alvo_dir = raiz / subpasta_da_live(video)
                alvo = alvo_dir / video.name
                resolvido = alvo.resolve()
                saida = cfg.get("pasta_saida")
                if resolvido == video.resolve() or (saida and resolvido.is_relative_to(Path(saida).resolve())):
                    raise ErroDestino("Escolha uma pasta do Drive fora da pasta dos cortes.")
                alvo_dir.mkdir(parents=True, exist_ok=True)
                if reg.get("caminho") != str(alvo) or not alvo.is_file():
                    _copiar_arquivo(alvo, lambda tmp: shutil.copyfile(video, tmp))
                    reg.pop("txt", None)
                reg["caminho"] = str(alvo)
                if reg.get("txt") and not Path(reg["txt"]).is_file():
                    reg.pop("txt")
                reg.pop("erro", None)
                reg["estado"] = "copiado"
            except ErroDestino as e:
                reg.update(estado="erro", erro=str(e))
            except OSError:
                reg.update(estado="erro", erro="Não consegui copiar para o destino. Confira se há espaço "
                           "e se a pasta do Drive está disponível, e tente de novo.")
            reg["quando"] = agora
            _gravar_json(_arq_copia(video), reg)
            return reg
    except TimeoutError as e:
        return {"estado": "erro", "erro": str(e), "quando": agora}
    except OSError:
        return {"estado": "erro", "erro": "Não consegui salvar o registro local da cópia.", "quando": agora}


def _copiar_arquivo(alvo: Path, escrever) -> None:
    fd, nome = tempfile.mkstemp(dir=alvo.parent, prefix=".copiando-", suffix=".tmp")
    os.close(fd)
    tmp = Path(nome)
    try:
        escrever(tmp)
        os.replace(tmp, alvo)
    finally:
        tmp.unlink(missing_ok=True)


def _trabalhador():
    while True:
        tarefa, futuro = _fila.get()
        try:
            if futuro.set_running_or_notify_cancel():
                futuro.set_result(tarefa())
        except Exception as erro:
            futuro.set_exception(erro)
        finally:
            _fila.task_done()


def disparar(video, cfg: dict | None = None) -> Future | None:
    """Chamado no fim de cada corte: se o destino estiver ligado, põe na fila e volta na hora.
    O retorno permite esperar pelas cópias da própria live no encerramento."""
    global _th
    if not config_atual(cfg)["destino_ativo"]:
        return None
    from . import log

    video, cfg = Path(video).resolve(), dict(cfg or {})

    def tarefa():
        reg = copiar(video, cfg)
        if reg["estado"] == "copiado":
            log.info(MENSAGEM_COPIA)
        else:
            log.aviso(f"Cópia de {video.name} não concluída. {reg['erro']}")
        return reg

    with _trava:
        if _th is None or not _th.is_alive():
            _th = threading.Thread(target=_trabalhador, daemon=True, name="destino")
            _th.start()
    futuro = Future()
    _fila.put((log.herdar(tarefa), futuro))
    return futuro
