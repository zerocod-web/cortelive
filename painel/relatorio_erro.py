"""Relatório de erro do CorteLive: junta tudo num .zip na Área de Trabalho para o amigo mandar ao Gabriel.

Nada sai pela internet. Antes de entrar no zip, todo texto passa por limpar(): tokens, chaves, senhas,
cookies, e-mails e o nome de usuário do Windows somem. Nunca entram vídeos nem a pasta webview_app.
"""
from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import tempfile
import time
import tomllib
import zipfile
from pathlib import Path

LIMITE_LOG = 400 * 1024         # últimos 400 KB de cada log
MAX_LOGS_SESSAO = 6             # sessões mais recentes que entram
LIMITE_TOTAL = 12 * 1024 * 1024  # teto do conteúdo do zip, antes de compactar
LIMITE_TEXTO_PAINEL = 200 * 1024

_CHAVE_SECRETA = re.compile(
    r"(token|secret|senha|password|passwd|pwd|api[_-]?key|apikey|chave|cookie|authorization|auth|bearer|"
    r"credential|private[_-]?key|session[_-]?id|sessionid|access[_-]?key|client[_-]?secret)", re.I)
# "chave": "valor" / chave=valor / chave: valor
_ATRIB = re.compile(
    r"""(?P<k>["']?[\w.\-]*(?:token|secret|senha|password|passwd|pwd|api[_-]?key|apikey|chave|cookie|"""
    r"""authorization|bearer|credential|private[_-]?key|session[_-]?id|sessionid|access[_-]?key)[\w.\-]*["']?)"""
    r"""(?P<s>\s*[:=]\s*)(?P<v>"[^"\n]*"|'[^'\n]*'|[^\s,;&}\]]+(?: [^\s,;&}\]]+)?)""", re.I)
_BEARER = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=\-]{8,}")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]*")
_PREFIXOS = re.compile(
    r"\b(?:sk|pk|rk|ghp|gho|ghs|ghu|github_pat|xox[abprs]|glpat|AKIA|AIza|ya29|hf|gsk|key)[-_][A-Za-z0-9_\-]{10,}"
    r"|\bAKIA[0-9A-Z]{12,}|\bAIza[0-9A-Za-z_\-]{20,}")
_LONGO = re.compile(r"(?<![A-Za-z0-9/+_\-=\\.])(?=[A-Za-z0-9+/_\-]*\d)(?=[A-Za-z0-9+/_\-]*[A-Za-z])[A-Za-z0-9+/_\-]{32,}={0,2}(?![A-Za-z0-9/+_\-])")
_HEX = re.compile(r"\b[0-9a-fA-F]{32,}\b")
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+")
_URL_SEGREDO = re.compile(r"(?i)([?&](?:token|key|apikey|api_key|access_token|auth|secret|sig|signature|password)=)[^&\s\"']+")


def _usuario_windows() -> str:
    return os.environ.get("USERNAME") or Path.home().name


def limpar(texto: str, usuario: str | None = None) -> str:
    """Tira de um texto tudo que identifica a pessoa ou dá acesso a uma conta."""
    if not texto:
        return texto
    usuario = usuario or _usuario_windows()
    # Caminhos: C:\Users\nome, C:/Users/nome, C:\\Users\\nome (JSON), \Users\nome em UNC.
    texto = re.sub(r"(?i)([A-Za-z]:(?:\\\\|\\|/)+Users(?:\\\\|\\|/)+)[^\\/\s\"'<>|:*?]+", r"\1USUARIO", texto)
    texto = re.sub(r"(?i)(/Users/|/home/)[^/\s\"']+", r"\1USUARIO", texto)
    if usuario and len(usuario) >= 3:
        texto = re.sub(r"(?i)(?<![A-Za-z0-9])" + re.escape(usuario) + r"(?![A-Za-z0-9])", "USUARIO", texto)
    texto = _URL_SEGREDO.sub(r"\1***", texto)
    texto = _JWT.sub("***", texto)
    texto = _BEARER.sub(lambda m: m.group(1) + " ***", texto)
    texto = _ATRIB.sub(lambda m: m.group("k") + m.group("s") + ('"***"' if m.group("v")[:1] in "\"'" else "***"), texto)
    texto = _PREFIXOS.sub("***", texto)
    texto = _EMAIL.sub("***@***", texto)
    texto = _LONGO.sub("***", texto)
    texto = _HEX.sub("***", texto)
    return texto


def _limpar_estrutura(obj, usuario):
    """Para JSON/TOML já lidos: valor de chave com cara de segredo vira ***; o resto passa por limpar()."""
    if isinstance(obj, dict):
        saida = {}
        for k, v in obj.items():
            if _CHAVE_SECRETA.search(str(k)) and not isinstance(v, (dict, list, bool)) and v not in (None, "", 0):
                saida[k] = "***"
            else:
                saida[k] = _limpar_estrutura(v, usuario)
        return saida
    if isinstance(obj, list):
        return [_limpar_estrutura(v, usuario) for v in obj]
    if isinstance(obj, str):
        return limpar(obj, usuario)
    return obj


def _ler_cauda(caminho: Path, limite=LIMITE_LOG) -> str:
    try:
        tam = caminho.stat().st_size
        with open(caminho, "rb") as f:
            if tam > limite:
                f.seek(tam - limite)
            bruto = f.read()
    except OSError:
        return ""
    texto = bruto.decode("utf-8", errors="replace")
    if tam > limite:
        texto = f"[... {tam - limite} bytes iniciais omitidos; só o final está aqui ...]\n" + texto.split("\n", 1)[-1]
    return texto


def pasta_area_de_trabalho() -> Path:
    try:
        import ctypes
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = [("a", wintypes.DWORD), ("b", wintypes.WORD), ("c", wintypes.WORD), ("d", ctypes.c_ubyte * 8)]

        g = GUID(0xB4BFCC3A, 0xDB2C, 0x424C, (ctypes.c_ubyte * 8)(0xB0, 0x29, 0x7F, 0xE9, 0x9A, 0x87, 0xC6, 0x41))
        ptr = ctypes.c_wchar_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(ptr)) == 0:
            p = Path(ptr.value)
            ctypes.windll.ole32.CoTaskMemFree(ptr)
            if p.is_dir():
                return p
    except Exception:
        pass
    for p in (Path.home() / "Desktop", Path.home() / "OneDrive" / "Desktop", Path.home() / "Área de Trabalho"):
        if p.is_dir():
            return p
    return Path.home()


def _placa_de_video() -> str:
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name + ' | driver ' + $_.DriverVersion + ' | ' + [math]::Round($_.AdapterRAM/1MB) + ' MB' }"],
            capture_output=True, text=True, timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return r.stdout.strip() or "não informado"
    except Exception as e:
        return f"não foi possível ler ({type(e).__name__})"


def _windows() -> str:
    try:
        return f"{platform.system()} {platform.release()} (build {platform.version()}) {platform.machine()}"
    except Exception:
        return "não informado"


def _disco(raiz: Path) -> str:
    linhas = []
    vistos = set()
    for p in (raiz, Path(tempfile.gettempdir())):
        try:
            drive = p.resolve().anchor
            if drive in vistos:
                continue
            vistos.add(drive)
            u = shutil.disk_usage(p)
            linhas.append(f"{drive} livre {u.free / 2**30:.1f} GB de {u.total / 2**30:.1f} GB")
        except OSError:
            pass
    return "; ".join(linhas) or "não informado"


def _versao(raiz: Path) -> str:
    try:
        return (raiz / "VERSION").read_text(encoding="utf-8-sig").strip()
    except OSError:
        return "desconhecida"


def _configuracoes(raiz: Path, usuario) -> dict:
    """Configurações lidas e já sem segredo (nome de chave suspeita vira ***)."""
    saida = {}
    for rel in ("config.toml", "cortelive/config.toml"):
        arq = raiz / rel
        try:
            saida[rel] = _limpar_estrutura(tomllib.loads(arq.read_text(encoding="utf-8-sig")), usuario)
        except OSError:
            continue
        except ValueError:
            saida[rel] = "(arquivo ilegível)"
    for rel in ("painel_config.json", "cams.json"):
        arq = raiz / rel
        try:
            saida[rel] = _limpar_estrutura(json.loads(arq.read_text(encoding="utf-8-sig")), usuario)
        except OSError:
            continue
        except ValueError:
            saida[rel] = "(arquivo ilegível)"
    return saida


def _sessoes_recentes(pasta_cortes: Path):
    """Pastas de sessão mais recentes (formato novo e antigo), sem entrar em pastas ocultas (_...) nem em vídeos."""
    achadas = []
    try:
        for d in pasta_cortes.iterdir():
            if not d.is_dir() or d.name.startswith("_"):
                continue
            ref = d / "_interno" if (d / "_interno").is_dir() else d
            if (ref / "log.txt").exists() or (ref / "sessao.json").exists():
                achadas.append((d.stat().st_mtime, d, ref))
    except OSError:
        return []
    achadas.sort(reverse=True)
    return achadas[:MAX_LOGS_SESSAO]


def _status_lives(sessoes, usuario) -> list:
    saida = []
    for _, d, ref in sessoes:
        item = {"pasta": limpar(d.name, usuario)}
        try:
            dados = json.loads((ref / "sessao.json").read_text(encoding="utf-8-sig"))
            for k in ("canal", "motivo_fim", "duracao_sessao_s", "live_acompanhada_s", "cortes", "falhas",
                      "descartados_por_fila", "quedas_da_captura", "reinicios_transcricao", "trocas_de_modelo"):
                if k in dados:
                    item[k] = dados[k]
            item["fonte_eh_arquivo_local"] = bool(dados.get("fonte")) and not str(dados.get("fonte")).startswith("http")
        except (OSError, ValueError):
            item["sessao_json"] = "ausente ou ilegível"
        saida.append(_limpar_estrutura(item, usuario))
    return saida


def gerar(raiz: Path, pasta_cortes: Path, corpo: dict | None = None, destino: Path | None = None,
          abrir=True) -> dict:
    """Cria o zip e devolve {arquivo, nome, bytes, itens}. `corpo` pode trazer o que só o painel sabe:
    mensagem_erro, estado (status das lives) e log_painel (registro do painel no navegador)."""
    corpo = corpo or {}
    usuario = _usuario_windows()
    agora = time.localtime()
    destino = Path(destino) if destino else pasta_area_de_trabalho()
    nome = time.strftime("CorteLive-erro-%Y-%m-%d-%H%M.zip", agora)
    alvo = destino / nome
    if alvo.exists():
        alvo = destino / time.strftime("CorteLive-erro-%Y-%m-%d-%H%M%S.zip", agora)
    destino.mkdir(parents=True, exist_ok=True)

    arquivos: dict[str, str] = {}   # caminho dentro do zip -> texto já limpo

    def add(nome_zip, texto):
        arquivos[nome_zip] = limpar(texto, usuario) if isinstance(texto, str) else texto

    sistema = [
        f"CorteLive versão: {_versao(raiz)}",
        f"Gerado em: {time.strftime('%Y-%m-%d %H:%M:%S', agora)}",
        f"Windows: {_windows()}",
        f"Placa de vídeo: {_placa_de_video()}",
        f"Espaço em disco: {_disco(raiz)}",
        f"Python do motor: {platform.python_version()}",
    ]
    if corpo.get("mensagem_erro"):
        sistema.append(f"Mensagem de erro mostrada na tela: {str(corpo['mensagem_erro'])[:2000]}")
    add("sistema.txt", "\n".join(sistema) + "\n")
    add("configuracoes.json", json.dumps(_configuracoes(raiz, usuario), ensure_ascii=False, indent=2))

    sessoes = _sessoes_recentes(pasta_cortes)
    status = {"sessoes_recentes": _status_lives(sessoes, usuario)}
    if isinstance(corpo.get("estado"), dict):
        status["painel"] = _limpar_estrutura(corpo["estado"], usuario)
    add("status_das_lives.json", json.dumps(status, ensure_ascii=False, indent=2))

    # registros: atualizador, instalação, motor (por sessão) e painel
    for rel, nome_zip in (("atualizacao.log", "logs/atualizador.log"), ("instalacao_log.txt", "logs/instalacao.log"),
                          ("atualizador.log", "logs/atualizador_2.log")):
        t = _ler_cauda(raiz / rel)
        if t:
            add(nome_zip, t)
    for i, (_, d, ref) in enumerate(sessoes, 1):
        t = _ler_cauda(ref / "log.txt")
        if t:
            add(f"logs/motor/{i:02d}-{limpar(d.name, usuario)[:60]}.log", t)
    if corpo.get("log_painel"):
        add("logs/painel.log", str(corpo["log_painel"])[-LIMITE_TEXTO_PAINEL:])
    for rel in ("painel.log", "logs/painel.log", "logs/motor.log"):
        t = _ler_cauda(raiz / rel)
        if t:
            add("logs/" + Path(rel).name.replace(".log", "") + "_arquivo.log", t)

    # corta o excesso pelo teto total, começando pelos logs mais antigos da lista
    total = sum(len(v.encode("utf-8")) for v in arquivos.values())
    for k in [k for k in arquivos if k.startswith("logs/motor/")][::-1]:
        if total <= LIMITE_TOTAL:
            break
        total -= len(arquivos[k].encode("utf-8"))
        del arquivos[k]

    leia_me = ("Relatório de erro do CorteLive.\n"
               "Este arquivo ficou só no seu computador. Nada foi enviado pela internet.\n"
               "Tokens, senhas, cookies, e-mails e o seu nome de usuário do Windows foram removidos.\n"
               "Não há vídeos nem dados do navegador do painel aqui.\n\nConteúdo:\n"
               + "\n".join(f"- {k}" for k in sorted(arquivos)) + "\n")
    arquivos["LEIA-ME.txt"] = leia_me

    tmp = alvo.with_suffix(".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for k, v in arquivos.items():
            z.writestr(k, v)
    os.replace(tmp, alvo)

    if abrir:
        try:
            subprocess.Popen(["explorer.exe", f"/select,{alvo}"])
        except OSError:
            pass
    return {"arquivo": str(alvo), "nome": alvo.name, "pasta": str(alvo.parent),
            "bytes": alvo.stat().st_size, "itens": sorted(arquivos)}
