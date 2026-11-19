"""Original de cada corte (v1.0.17): só o trecho do corte, quadro inteiro, com folga de 2 s em cada ponta.

A live é apagada depois (não dá para guardar centenas de GB), então é este arquivo que permite refazer o corte
depois (trocar o modo, Ajustar foco). Recodifica pela placa (h264_nvenc quando há NVIDIA) numa qualidade boa e leve,
na resolução da live (as caixas de câmera/gameplay são em px da live) e no máximo 30 fps (o corte final já é 30 fps).
Fica em _interno/<corte>.mkv, sai junto quando o corte vai para a Lixeira e é apagado depois de N dias
(painel: "Apagar originais depois de").
"""
import json
import os
import subprocess
import time
from pathlib import Path

FOLGA = 2.0
DIAS_PADRAO = 7
DIAS_OPCOES = (0, 1, 3, 7, 14, 30)       # 0 = nunca apagar
# Medido em 19/11/2026 (1080p60 de Twitch, 1080p30 no original): ver o LEIA-ME do commit.
ENC_ORIGINAL = {
    "h264_nvenc": ["-c:v", "h264_nvenc", "-preset", "p5", "-tune", "hq", "-profile:v", "high", "-rc", "vbr",
                   "-cq", "26", "-b:v", "3M", "-maxrate", "4500k", "-bufsize", "9M", "-spatial-aq", "1", "-bf", "3"],
    "h264_amf": ["-c:v", "h264_amf", "-quality", "quality", "-profile:v", "high", "-rc", "vbr_peak",
                 "-b:v", "3M", "-maxrate", "4500k", "-bufsize", "9M"],
    "h264_qsv": ["-c:v", "h264_qsv", "-profile:v", "high", "-global_quality", "26", "-b:v", "3M",
                 "-maxrate", "4500k", "-bufsize", "9M"],
    "libx264": ["-c:v", "libx264", "-preset", "veryfast", "-profile:v", "high", "-crf", "24",
                "-maxrate", "4500k", "-bufsize", "9M"],
}


def _duracao(arq):
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                              str(arq)], capture_output=True, text=True, timeout=30,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.strip()
        return float(out)
    except Exception:
        return None


def janela(inicio, fim, duracao=None, folga=FOLGA):
    """(a, b) do trecho guardado no tempo do arquivo de origem: o corte com `folga` s de cada lado, sem passar
    do começo/fim do arquivo."""
    a = max(0.0, float(inicio) - folga)
    b = float(fim) + folga
    if duracao:
        b = min(b, float(duracao))
    return a, max(b, float(fim))


def guardar(fonte, inicio, fim, destino, encoder=None):
    """Grava o original do corte [inicio, fim] (tempos no arquivo `fonte`) em `destino` (.mkv).
    -> {'arquivo', 'deslocamento', 'inicio', 'fim', 'encoder', 'bytes', 'segundos'}: inicio/fim já no tempo do
    original (deslocamento = quanto o original começa depois do começo da fonte). Erro -> RuntimeError."""
    from . import render
    destino = Path(destino)
    a, b = janela(inicio, fim, _duracao(fonte))
    enc = render.encoder_corte(encoder)
    try:
        fps = render._probe(fonte)[2]          # fps da live com teto de 30 (igual ao corte final)
    except Exception:
        fps = "30"
    tmp = destino.with_name(destino.stem + ".gravando.mkv")
    t0 = time.time()
    erro = ""
    for e_ in ([enc, "libx264"] if enc != "libx264" else [enc]):
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-ss", f"{a:.3f}", "-i", str(fonte),
               "-t", f"{b - a:.3f}", "-map", "0:v:0", "-map", "0:a:0?", "-vf", f"fps={fps}", *ENC_ORIGINAL[e_], "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-ar", "48000",
               str(tmp)]
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore",
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if r.returncode == 0 and tmp.is_file() and tmp.stat().st_size > 0:
            enc = e_
            break
        erro = f"ffmpeg (original, {e_}): " + (r.stderr or "")[-400:]
    else:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(erro)
    os.replace(tmp, destino)
    return {"arquivo": str(destino), "deslocamento": round(a, 3), "inicio": round(float(inicio) - a, 3),
            "fim": round(float(fim) - a, 3), "encoder": enc, "bytes": destino.stat().st_size,
            "segundos": round(time.time() - t0, 1)}


def dias_config(dados):
    """Dias do painel_config.json ('originais_dias'); inválido -> padrão."""
    try:
        d = int(dados.get("originais_dias", DIAS_PADRAO))
    except (TypeError, ValueError, AttributeError):
        return DIAS_PADRAO
    return d if d in DIAS_OPCOES else DIAS_PADRAO


def _nomes_originais(meta: dict):
    for chave in ("refazer", "foco_fonte"):
        nome = (meta.get(chave) or {}).get("video")
        if isinstance(nome, str) and nome and Path(nome).name == nome and nome.lower().endswith(".mkv"):
            yield nome


def limpar_vencidos(pasta_cortes, dias, agora=None):
    """Apaga os originais (.mkv nomeados no .json de cada corte) com mais de `dias` dias. dias=0: nunca.
    Só toca em arquivos citados por um .json de corte dentro de uma pasta _interno. -> (apagados, bytes)."""
    if not dias or dias <= 0:
        return 0, 0
    agora = agora or time.time()
    limite = agora - dias * 86400
    apagados = livres = 0
    for meta in Path(pasta_cortes).glob("**/_interno/*.json"):
        if meta.name.endswith((".foco.json", ".pedido.json")):
            continue
        try:
            d = json.loads(meta.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(d, dict):
            continue
        for nome in _nomes_originais(d):
            arq = meta.parent / nome
            try:
                st = arq.stat()
            except OSError:
                continue
            if st.st_mtime < limite:
                try:
                    arq.unlink()
                    apagados += 1
                    livres += st.st_size
                except OSError:
                    pass
    return apagados, livres
