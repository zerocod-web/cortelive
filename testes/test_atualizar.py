"""O atualizador não deve operar na raiz de um checkout do Git."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest


POWERSHELL = shutil.which("powershell.exe")
SCRIPT = Path(__file__).resolve().parents[1] / "instalador" / "atualizar.ps1"
pytestmark = pytest.mark.skipif(not POWERSHELL, reason="Requer Windows PowerShell")


@pytest.mark.parametrize("git_file", [False, True], ids=["checkout", "worktree"])
@pytest.mark.parametrize("flags", [(), ("-Aplicar",), ("-Voltar",), ("-Checar",)])
def test_atualizador_bloqueia_checkout_git(tmp_path, git_file, flags):
    git = tmp_path / ".git"
    if git_file:
        git.write_text("gitdir: ../repo/.git/worktrees/exemplo\n", encoding="utf-8")
    else:
        git.mkdir()
    version = tmp_path / "VERSION"
    version.write_text("1.0.1\n", encoding="utf-8")

    result = subprocess.run(
        [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPT),
         *flags, "-Raiz", str(tmp_path), "-ApiBase", "http://127.0.0.1:1", "-TimeoutSeg", "1"],
        capture_output=True, encoding="utf-8", timeout=10,
    )

    assert result.returncode == 1
    event = json.loads(result.stdout)
    assert event["etapa"] == "erro"
    assert "repositório de desenvolvimento" in event["msg"]
    assert event["versao_atual"] == "1.0.1"
    assert version.read_text(encoding="utf-8") == "1.0.1\n"
    assert not (tmp_path / "versoes_antigas").exists()
    assert not (tmp_path / "_update_tmp").exists()


# --- webview_app (perfil do Edge do painel) nunca entra no backup nem na troca -------------------
import ctypes
import hashlib
import http.server
import socketserver
import threading
import zipfile
from ctypes import wintypes


def _servir_release(dirpath, versao, porta_holder):
    pkg = dirpath / "pkg.zip"

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            base = f"http://127.0.0.1:{self.server.server_address[1]}"
            if self.path.endswith("/releases/latest"):
                corpo = json.dumps({"tag_name": f"v{versao}", "prerelease": False, "draft": False, "assets": [
                    {"name": "cortelive_amigos.zip", "browser_download_url": base + "/pkg.zip"},
                    {"name": "cortelive_amigos.zip.sha256", "browser_download_url": base + "/pkg.zip.sha256"}]}).encode()
            elif self.path == "/pkg.zip":
                corpo = pkg.read_bytes()
            else:
                corpo = (hashlib.sha256(pkg.read_bytes()).hexdigest() + "  cortelive_amigos.zip\n").encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(corpo)))
            self.end_headers()
            self.wfile.write(corpo)

    socketserver.TCPServer.allow_reuse_address = True
    srv = socketserver.TCPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _travar_sem_compartilhar(caminho):
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    h = k32.CreateFileW(str(caminho), 0x80000000, 0, None, 3, 0x80, None)  # leitura, share=0 (como o msedge)
    assert h not in (None, wintypes.HANDLE(-1).value), ctypes.get_last_error()
    return h, k32


def test_atualizar_ignora_webview_app_travado_pelo_edge(tmp_path):
    raiz = tmp_path / "CorteLive"
    for rel, txt in {
        "VERSION": "1.0.4\n", "cortelive/__init__.py": "__version__ = '1.0.4'\n", "cortelive/cli.py": "",
        "instalador/instalar.ps1": "", "instalador/versoes.ps1": "$REPO_GITHUB = 't/x'\n",
        "instalador/atualizar.ps1": SCRIPT.read_text(encoding="utf-8"), "requirements.txt": "",
    }.items():
        arq = raiz / rel
        arq.parent.mkdir(parents=True, exist_ok=True)
        arq.write_text(txt, encoding="utf-8")
    cookies = raiz / "webview_app" / "Default" / "Network" / "Cookies"
    cookies.parent.mkdir(parents=True)
    cookies.write_bytes(b"sessao")
    (raiz / "webview_dev").mkdir()
    (raiz / "webview_dev" / "x").write_text("x")

    pacote = tmp_path / "srv"
    pacote.mkdir()
    with zipfile.ZipFile(pacote / "pkg.zip", "w") as z:
        for rel, txt in {
            "VERSION": "1.0.9\n", "cortelive/__init__.py": "__version__ = '1.0.9'  # pacote novo\n", "cortelive/cli.py": "",
            "instalador/instalar.ps1": "", "instalador/versoes.ps1": "$REPO_GITHUB = 't/x'\n",
            "instalador/atualizar.ps1": SCRIPT.read_text(encoding="utf-8"), "requirements.txt": "",
        }.items():
            z.writestr(rel, txt)
    srv = _servir_release(pacote, "1.0.9", None)
    handle, k32 = _travar_sem_compartilhar(cookies)
    try:
        r = subprocess.run(
            [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(raiz / "instalador" / "atualizar.ps1"),
             "-Aplicar", "-Raiz", str(raiz), "-Repo", "t/x", "-SemCache",
             "-ApiBase", f"http://127.0.0.1:{srv.server_address[1]}"],
            capture_output=True, encoding="utf-8", timeout=120, cwd=str(tmp_path),
        )
    finally:
        k32.CloseHandle(handle)
        srv.shutdown()
    ultimo = json.loads(r.stdout.strip().splitlines()[-1])
    assert r.returncode == 0, r.stdout
    assert ultimo["etapa"] == "concluido"
    assert (raiz / "VERSION").read_text(encoding="utf-8").strip() == "1.0.9"
    assert cookies.read_bytes() == b"sessao"
    assert not (raiz / "versoes_antigas" / "v1.0.4" / "webview_app").exists()
    assert not (raiz / "versoes_antigas" / "v1.0.4" / "webview_dev").exists()
