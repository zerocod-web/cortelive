"""Miniatura do corte pronto (v1.0.17): um quadro do MEIO do corte final (jpg leve) e uma tira de quadros
para a prévia ao passar o mouse no painel. Ficam em _interno/<nome>.jpg e _interno/<nome>.previa.jpg."""
from __future__ import annotations

import subprocess
from pathlib import Path

LARGURA = 360          # miniatura (o painel mostra a ~110 px; 360 fica nítido em tela HiDPI)
QUADROS_PREVIA = 8
LARGURA_PREVIA = 180


def _rodar(cmd, timeout=20, env=None) -> bool:
    try:
        r = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=timeout, env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def duracao(video, env=None) -> float:
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(video)],
                           capture_output=True, text=True, timeout=10, env=env,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return float(r.stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0.0


def caminhos(pasta_interna, nome) -> tuple[Path, Path]:
    d = Path(pasta_interna)
    return d / f"{nome}.jpg", d / f"{nome}.previa.jpg"


def gerar(video, pasta_interna, nome, dur=None, env=None) -> dict:
    """Gera as duas imagens (sobrescreve). Nunca levanta erro: devolve o que conseguiu."""
    jpg, previa = caminhos(pasta_interna, nome)
    dur = dur or duracao(video, env)
    meio = max(0.0, dur / 2) if dur else 1.0
    ok_jpg = _rodar(["ffmpeg", "-v", "error", "-y", "-ss", f"{meio:.2f}", "-i", str(video), "-frames:v", "1",
                     "-vf", f"scale={LARGURA}:-2", "-q:v", "5", str(jpg)], env=env)
    ok_prev = False
    if dur and dur > 2:
        n = QUADROS_PREVIA
        # -skip_frame nokey: só decodifica os quadros-chave (rápido); a tira não precisa de mais precisão
        ok_prev = _rodar(["ffmpeg", "-v", "error", "-y", "-skip_frame", "nokey", "-i", str(video),
                          "-vf", f"fps={n / dur:.5f},scale={LARGURA_PREVIA}:-2,tile={n}x1", "-frames:v", "1",
                          "-q:v", "6", str(previa)], timeout=40, env=env)
    return {"jpg": jpg if ok_jpg else None, "previa": previa if ok_prev else None}
