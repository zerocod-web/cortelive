"""cortelive — acompanha uma live e solta cortes verticais legendados enquanto ela acontece.

  cortelive <url_da_live>                 (Twitch, YouTube, Kick)
  cortelive --arquivo video.mp4           (simula live lendo o arquivo a 1x — para testar)
  cortelive marcar-cam <url|arquivo>      (mede onde fica a cam do streamer; opcional)

Feito para rodar sozinho por horas: nada pergunta nada durante a live, toda etapa tem watchdog,
um corte com erro não derruba o resto.
"""
import argparse
import concurrent.futures as cf
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import tomllib
from pathlib import Path

from . import __version__, hw, log

AQUI = Path(__file__).resolve().parent


class ErroAmigavel(SystemExit):
    pass


def carregar_config(args=None):
    cfg = tomllib.loads((AQUI / "config.toml").read_text(encoding="utf-8-sig"))
    user = Path.cwd() / "config.toml"
    if user.exists():
        try:
            # utf-8-sig: o Bloco de Notas pode salvar com BOM
            cfg.update(tomllib.loads(user.read_text(encoding="utf-8-sig")))
        except tomllib.TOMLDecodeError as e:
            raise ErroAmigavel(f"Seu config.toml tem um erro de digitação ({e}). Corrija ou apague o arquivo.")
    if args:
        for k in ("duracao_min", "duracao_max", "modelo", "legenda"):
            v = getattr(args, k, None)
            if v:
                cfg[k] = v
        if args.saida:
            cfg["pasta_saida"] = args.saida
        if args.sem_chat:
            cfg["usar_chat"] = False
    if cfg["duracao_max"] < cfg["duracao_min"]:
        raise ErroAmigavel("duracao_max está menor que duracao_min no config.toml.")
    return cfg


def _hms(s):
    s = int(max(0, s))
    return f"{s // 3600:02d}:{s // 60 % 60:02d}:{s % 60:02d}"


def checar_ambiente():
    if not hw.ffmpeg_bin() or not shutil.which("ffprobe"):
        raise ErroAmigavel("FFmpeg não encontrado. Rode instalar.cmd (ou: winget install Gyan.FFmpeg) e abra o CMD de novo.")


def rodar(args):
    """Uma live só (modo de sempre): sessão com log na tela; Ctrl+C para parar."""
    from . import render, servicos
    from .sessao import Sessao

    cfg = carregar_config(args)
    for tipo, nome in (("modelo", cfg["modelo"]), ("legenda", cfg["legenda"])):
        if nome not in render.listar(tipo):
            raise ErroAmigavel(f"{tipo} '{nome}' não existe. Opções: {', '.join(render.listar(tipo))}")
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    modo = hw.detectar(args.encoder, args.fala, args.modelo_whisper, None, args.gpu)
    servicos.configurar(cfg, modo)
    s = Sessao(args=args, cfg=None, terminal=True).iniciar()
    try:
        while not s._fim_ev.wait(0.5):
            pass
    except KeyboardInterrupt:
        print("Parando (Ctrl+C)… terminando os cortes em andamento.", flush=True)
        s.parar()
        try:
            s.aguardar()
        except KeyboardInterrupt:
            pass
    servicos.desligar()
    if s.erro:
        raise ErroAmigavel(str(s.erro))
    return 0


def marcar_cam(args):
    import cv2
    from . import camauto, captura
    alvo = args.fonte
    quadro = Path.cwd() / "quadro_cam.jpg"
    if Path(alvo).exists():
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-ss", str(args.tempo), "-i", alvo,
                        "-frames:v", "1", str(quadro)], check=True)
        canal = args.canal or Path(alvo).stem
    else:
        sl = captura._bin("streamlink")
        p = subprocess.Popen([sl, "--stdout", "--loglevel", "error", alvo, "best"], stdout=subprocess.PIPE)
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", "pipe:0", "-frames:v", "1", str(quadro)],
                       stdin=p.stdout, timeout=60)
        p.kill()
        canal = args.canal or captura.canal_da_url(alvo)
    img = cv2.imread(str(quadro))
    if img is None:
        raise ErroAmigavel("Não consegui pegar um quadro. A live está online? O link está certo?")
    h, w = img.shape[:2]
    print(f"Quadro salvo em {quadro} ({w}x{h}).")
    fs = camauto.rostos(img)
    sug = camauto.caixa_da_cam(max(fs, key=lambda r: r[2] * r[3]), w, h) if len(fs) else None
    if sug:
        print(f"Sugestão (rosto detectado): {','.join(map(str, sug))}")
    rect = args.rect
    if args.sem_cam:
        cam = None
    else:
        if not rect:
            rect = input("Digite x,y,largura,altura da cam em pixels (Enter = sugestão): ").strip()
            if not rect and sug:
                rect = ",".join(map(str, sug))
        if not rect:
            raise ErroAmigavel("Nenhuma cam informada.")
        cam = tuple(int(v) for v in rect.split(","))
        prev = img.copy()
        x, y, cw, ch = cam
        cv2.rectangle(prev, (x, y), (x + cw, y + ch), (0, 255, 255), 4)
        cv2.imwrite(str(quadro.with_name("quadro_cam_marcado.jpg")), prev)
    camauto.salvar(canal, cam, "marcada")
    print(f"Guardado para o canal '{canal}' em {camauto.ARQ}.")
    return 0


def _main(argv):
    from . import render
    if argv and argv[0] == "marcar-cam":
        p = argparse.ArgumentParser(prog="cortelive marcar-cam")
        p.add_argument("fonte", help="URL da live ou arquivo de vídeo")
        p.add_argument("--canal", help="nome do canal (padrão: tirado do link)")
        p.add_argument("--tempo", type=float, default=30, help="segundo do arquivo para o quadro")
        p.add_argument("--rect", help="x,y,largura,altura em pixels (sem perguntar)")
        p.add_argument("--sem-cam", action="store_true", help="este canal não tem cam")
        return marcar_cam(p.parse_args(argv[1:]))

    p = argparse.ArgumentParser(prog="cortelive", description="Corta uma live em tempo real.")
    p.add_argument("url", nargs="?", help="link da live (Twitch, YouTube, Kick)")
    p.add_argument("--arquivo", help="vídeo para simular uma live (lido em tempo real)")
    p.add_argument("--modelo", help="layout: " + " | ".join(render.listar("modelo")))
    p.add_argument("--legenda", help="legenda: " + " | ".join(render.listar("legenda")))
    p.add_argument("--saida", help="pasta dos cortes (padrão: cortes)")
    p.add_argument("--canal", help="nome do canal (pasta, chat e cam memorizada)")
    p.add_argument("--cam", help="x,y,largura,altura da cam em pixels")
    p.add_argument("--duracao-min", type=float)
    p.add_argument("--duracao-max", type=float)
    p.add_argument("--minutos", type=float, help="parar depois de N minutos")
    p.add_argument("--sem-chat", action="store_true", help="não ler o chat da Twitch")
    p.add_argument("--gpu", choices=["nvidia", "amd", "intel", "cpu"], help="forçar a placa")
    p.add_argument("--encoder", choices=list(hw.ENCODERS), help="forçar encoder de vídeo")
    p.add_argument("--fala", choices=["faster-whisper-cuda", "whisper-cpp", "faster-whisper-cpu"],
                   help="forçar motor de transcrição")
    p.add_argument("--modelo-whisper", help="tamanho do modelo (small, medium, large-v3-turbo…)")
    p.add_argument("--detalhado", action="store_true")
    p.add_argument("--versao", action="version", version=__version__)
    a = p.parse_args(argv)
    if not a.url and not a.arquivo:
        p.print_help()
        return 2
    if a.arquivo and not Path(a.arquivo).exists():
        raise ErroAmigavel(f"Arquivo não encontrado: {a.arquivo}")
    checar_ambiente()
    if a.encoder:
        os.environ["CORTELIVE_ENCODER"] = a.encoder   # o render (processo separado) respeita
    return rodar(a)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        return _main(argv)
    except ErroAmigavel as e:
        print(f"\nERRO: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    except Exception as e:
        print(f"\nERRO inesperado: {e}\nO detalhe está no log.txt da pasta da sessão. "
              f"Rode de novo com --detalhado para ver mais.", file=sys.stderr)
        import logging
        logging.exception("erro inesperado")
        return 1
