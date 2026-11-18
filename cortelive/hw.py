"""Detecção de hardware: placa de vídeo, encoder H.264 e caminho de transcrição.

Ordem do encoder: NVIDIA h264_nvenc -> AMD h264_amf -> Intel h264_qsv -> CPU (libx264, último recurso).
Cada encoder é TESTADO de verdade (1 quadro codificado), não só listado: o FFmpeg lista h264_amf mesmo sem placa AMD.
Transcrição: NVIDIA -> faster-whisper CUDA; AMD/Intel -> whisper.cpp (Vulkan) se houver binário, senão
faster-whisper CPU int8; sem GPU -> faster-whisper CPU int8.
Decisão de 17/11/2026: AMD/Intel usam faster-whisper CPU int8 (small). O whisper.cpp oficial para Windows
não tem build Vulkan e o Const-me/Whisper (D3D11) não dá tempo por palavra. whisper-cpp continua disponível
via --fala whisper-cpp.
"""
import glob
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent

ENCODERS = {
    "h264_nvenc": ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "19", "-b:v", "0"],
    "h264_amf": ["-c:v", "h264_amf", "-quality", "quality", "-rc", "cqp", "-qp_i", "19", "-qp_p", "21", "-qp_b", "23"],
    "h264_qsv": ["-c:v", "h264_qsv", "-preset", "slower", "-global_quality", "21"],
    "libx264": ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"],
}
ORDEM_VENDOR = {"nvidia": "h264_nvenc", "amd": "h264_amf", "intel": "h264_qsv"}


def ffmpeg_bin():
    f = shutil.which("ffmpeg")
    if not f:
        # PATH desatualizado na sessão: recarrega do registro (Windows)
        p = os.environ.get("PATH", "")
        try:
            import winreg
            for raiz, chave in ((winreg.HKEY_CURRENT_USER, "Environment"),
                                (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")):
                with winreg.OpenKey(raiz, chave) as k:
                    p += os.pathsep + os.path.expandvars(winreg.QueryValueEx(k, "Path")[0])
            os.environ["PATH"] = p
        except Exception:
            pass
        f = shutil.which("ffmpeg")
    return f


def placas():
    """Nomes das placas de vídeo (Windows: CIM; fallback nvidia-smi)."""
    nomes = []
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
                              "(Get-CimInstance Win32_VideoController).Name"],
                             capture_output=True, text=True, timeout=15).stdout
        nomes = [l.strip() for l in out.splitlines() if l.strip()]
    except Exception:
        pass
    if not nomes and shutil.which("nvidia-smi"):
        out = subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                             capture_output=True, text=True).stdout
        nomes = [l.strip() for l in out.splitlines() if l.strip()]
    return nomes


def vendor_de(nome: str):
    n = nome.lower()
    if "nvidia" in n or "geforce" in n or "rtx" in n or "quadro" in n:
        return "nvidia"
    if "amd" in n or "radeon" in n:
        return "amd"
    if "intel" in n and ("arc" in n or "graphics" in n or "iris" in n or "uhd" in n):
        return "intel"
    return None


def encoder_funciona(enc: str) -> bool:
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=black:s=256x256:d=0.2:r=30",
           "-frames:v", "3", *ENCODERS[enc], "-f", "null", "-"]
    try:
        return subprocess.run(cmd, capture_output=True, timeout=30).returncode == 0
    except Exception:
        return False


def libs_cuda():
    """Põe as DLLs CUDA dos pacotes nvidia-* (cublas, cudnn) no caminho de busca."""
    import numpy as np
    sp = Path(np.__file__).resolve().parent.parent
    achou = False
    for d in glob.glob(str(sp / "nvidia" / "*" / "bin")):
        try:
            os.add_dll_directory(d)
        except Exception:
            pass
        os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
        achou = True
    return achou


def cuda_ok() -> bool:
    try:
        libs_cuda()
        import ctranslate2
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


def achar_whisper_cpp(cfg_caminho: str | None = None):
    """whisper-cli.exe (de preferência compilado com Vulkan). Procura: config, env, ferramentas/, PATH."""
    cands = [cfg_caminho, os.environ.get("CORTELIVE_WHISPER_CPP"),
             str(RAIZ / "ferramentas" / "whispercpp" / "vulkan" / "whisper-cli.exe"),
             str(RAIZ / "ferramentas" / "whispercpp" / "bin" / "Release" / "whisper-cli.exe"),
             str(RAIZ / "ferramentas" / "whispercpp" / "bin" / "whisper-cli.exe"),
             shutil.which("whisper-cli")]
    for c in cands:
        if c and Path(c).exists():
            return c
    return None


@dataclass
class Modo:
    placas: list = field(default_factory=list)
    vendor: str | None = None
    encoder: str = "libx264"
    transcricao: str = "faster-whisper-cpu"   # faster-whisper-cuda | whisper-cpp | faster-whisper-cpu
    modelo_whisper: str = "small"
    whisper_cpp: str | None = None

    def resumo(self):
        placa = ", ".join(self.placas) or "nenhuma detectada"
        return (f"Placa: {placa} | encoder: {self.encoder} | fala: {self.transcricao} "
                f"(modelo {self.modelo_whisper})")

    def args_encoder(self):
        return list(ENCODERS[self.encoder])


def detectar(forcar_encoder=None, forcar_fala=None, modelo=None, whisper_cpp=None, forcar_gpu=None) -> Modo:
    m = Modo()
    m.placas = placas()
    vendors = [v for v in (vendor_de(p) for p in m.placas) if v]
    if forcar_gpu:
        vendors = [] if forcar_gpu == "cpu" else [forcar_gpu]
    # prioridade: nvidia > amd > intel (placa dedicada ganha da integrada)
    for v in ("nvidia", "amd", "intel"):
        if v in vendors:
            m.vendor = v
            break

    # encoder
    if forcar_encoder:
        if forcar_encoder not in ENCODERS:
            raise SystemExit(f"Encoder desconhecido: {forcar_encoder}. Opções: {', '.join(ENCODERS)}")
        m.encoder = forcar_encoder
    else:
        ordem = [ORDEM_VENDOR[v] for v in ("nvidia", "amd", "intel") if v in vendors] + ["libx264"]
        for enc in ordem:
            if enc == "libx264" or encoder_funciona(enc):
                m.encoder = enc
                break

    # transcrição
    m.whisper_cpp = achar_whisper_cpp(whisper_cpp)
    if forcar_fala:
        m.transcricao = forcar_fala
    elif m.vendor == "nvidia" and cuda_ok():
        m.transcricao = "faster-whisper-cuda"
    else:
        m.transcricao = "faster-whisper-cpu"
    if modelo:
        m.modelo_whisper = modelo
    elif m.transcricao == "faster-whisper-cuda":
        m.modelo_whisper = "large-v3-turbo"
    else:
        m.modelo_whisper = "small"
    return m
