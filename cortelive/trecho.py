"""Junta os segmentos .ts do buffer num arquivo único (sem recodificar) para o render.

Usa o concat DEMUXER: os segmentos têm timestamps zerados (-reset_timestamps 1) e o concat de protocolo
emendava errado (corte saía com 0,5 s). O demuxer reescreve os tempos em sequência.
"""
import subprocess
from pathlib import Path


def juntar(segs, saida: Path) -> float:
    """Escreve 'saida' (.mkv) com os segmentos; devolve o tempo da live do início do arquivo."""
    lista = saida.with_suffix(".txt")
    lista.write_text("".join(f"file '{Path(s.arquivo).resolve().as_posix()}'\n" for s in segs), encoding="utf-8")
    r = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-f", "concat",
                        "-safe", "0", "-i", str(lista), "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy",
                        str(saida)], capture_output=True, text=True, encoding="utf-8", errors="ignore")
    lista.unlink(missing_ok=True)
    if r.returncode != 0:
        raise RuntimeError("ffmpeg (juntar trecho): " + r.stderr[-600:])
    return segs[0].ini


def stub_render(video, inicio, fim, palavras, modelo, legenda, cam, saida_dir, nome):
    """Reserva se render.render_corte não existir: crop central 9:16, sem legenda."""
    out = Path(saida_dir)
    mp4 = out / f"{nome}.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-nostdin", "-y", "-ss", f"{inicio:.3f}", "-t",
                    f"{fim - inicio:.3f}", "-i", str(video), "-vf", "crop=ih*9/16:ih,scale=1080:1920",
                    "-c:v", "h264_nvenc", "-cq", "20", "-c:a", "aac", str(mp4)], check=True)
    return {"mp4": str(mp4), "mp4_sem_legenda": str(mp4), "ass": None}
