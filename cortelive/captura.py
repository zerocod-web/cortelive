"""Captura: live (streamlink, yt-dlp de reserva) ou arquivo lido a 1x -> FFmpeg em segmentos .ts.

Cada segmento pronto vira um Segmento(idx, ini, fim) no tempo da live (segundos desde o começo da captura).
O tempo é contado somando as durações reais, então uma reconexão não bagunça a linha do tempo
(o buraco da queda fica registrado no log). Buffer rolante: segmentos mais velhos que N minutos são apagados.
"""
import os
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from . import log


@dataclass
class Segmento:
    idx: int
    arquivo: Path
    ini: float          # tempo da live (s)
    fim: float
    chegou: float       # relógio (time.time()) em que ficou pronto


def _bin(nome):
    """Executável do venv (streamlink/yt-dlp) ou do PATH."""
    d = Path(sys.executable).parent
    for c in (d / f"{nome}.exe", d / nome):
        if c.exists():
            return str(c)
    return shutil.which(nome)


def canal_da_url(url: str) -> str:
    u = url.rstrip("/").split("?")[0]
    partes = [p for p in u.split("/") if p]
    if "youtube" in u or "youtu.be" in u:
        if "@" in u:
            return [p for p in partes if p.startswith("@")][0].lstrip("@")
        return "youtube_" + (url.split("v=")[-1][:11] if "v=" in url else partes[-1])
    return partes[-1] if partes else "live"


class Captura(threading.Thread):
    def __init__(self, pasta: Path, url=None, arquivo=None, seg_s=4, buffer_min=5, ao_chegar=None,
                 travou_s=40, esperar_live_min=0, fim_apos_offline_min=10):
        self.travou_s, self.esperar_min, self.fim_offline_min = travou_s, esperar_live_min, fim_apos_offline_min
        self.quedas = []            # [(tempo_live, segundos_fora)]
        self.emendas = []           # tempos da live onde a captura reconectou (conteúdo pula)
        self._caiu_em = None
        self.motivo_fim = ""
        self._ff = None
        super().__init__(daemon=True)
        self._reg = log.atual()
        self.estado = "iniciando"
        self.pasta = pasta
        self.url, self.arquivo = url, arquivo
        self.seg_s, self.buffer_s = seg_s, buffer_min * 60
        self.ao_chegar = ao_chegar
        self.segs: dict[int, Segmento] = {}
        self.lock = threading.Lock()
        self.fim_live = 0.0
        self.parar = threading.Event()
        self.acabou = threading.Event()
        self.presos: set[int] = set()   # segmentos em uso por um corte (não apagar)
        self._n = 0

    # --- comandos ---------------------------------------------------------------------------
    def _ffmpeg_seg(self, rodada, entrada, extra_in=()):
        lista = self.pasta / f"lista_{rodada}.csv"
        padrao = self.pasta / f"r{rodada}_%06d.ts"
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", *extra_in, "-i", entrada,
               "-map", "0:v:0", "-map", "0:a:0", "-c", "copy",
               "-f", "segment", "-segment_time", str(self.seg_s), "-segment_list", str(lista),
               "-segment_list_type", "csv", "-segment_list_flags", "live", "-reset_timestamps", "1",
               str(padrao)]
        return cmd, lista

    def _fonte(self):
        """Processo que escreve o vídeo da live no stdout (ou None para arquivo)."""
        sl = _bin("streamlink")
        if sl:
            return subprocess.Popen([sl, "--stdout", "--loglevel", "error", "--twitch-disable-ads",
                                     "--hls-live-edge", "2", self.url, "720p60,720p,best"],
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL), "streamlink"
        yd = _bin("yt-dlp")
        return subprocess.Popen([yd, "-q", "-f", "best[height<=1080]/best", "-o", "-", self.url],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL), "yt-dlp"

    # --- laço ---------------------------------------------------------------------------------
    def run(self):
        log.usar(self._reg)
        try:
            self._laco()
        except Exception as e:          # nunca morrer calado
            log.aviso(f"Captura parou por erro inesperado: {e}")
            self.motivo_fim = f"erro na captura: {e}"
        self.estado = "encerrada"
        self.acabou.set()

    def _laco(self):
        espera, rodada = 2, 0
        t0 = time.time()
        fora_desde = time.time()        # desde quando estamos sem receber nada
        while not self.parar.is_set():
            rodada += 1
            fonte = None
            n_antes = self._n
            if self.arquivo:
                # retomada depois de queda: continua de onde parou
                extra = ("-re", "-ss", f"{self.fim_live:.3f}") if self.fim_live > 0 else ("-re",)
                cmd, lista = self._ffmpeg_seg(rodada, str(self.arquivo), extra_in=extra)
                ff = subprocess.Popen(cmd, stderr=subprocess.PIPE)
                if rodada == 1:
                    log.info("Lendo o arquivo em tempo real (1x), como se fosse live.")
                else:
                    log.info(f"Captura reiniciada, continuando do ponto {self.fim_live:.0f} s.")
            else:
                fonte, nome = self._fonte()
                cmd, lista = self._ffmpeg_seg(rodada, "pipe:0")
                ff = subprocess.Popen(cmd, stdin=fonte.stdout, stderr=subprocess.PIPE)
                fonte.stdout.close()
                log.debug(f"rodada {rodada} via {nome}")
            self._ff = ff
            fim_normal = self._acompanhar(ff, lista, rodada)
            if fonte:
                fonte.kill()
            recebeu = self._n > n_antes
            self._caiu_em = time.time()
            if recebeu:
                fora_desde = None
                espera = 2
            if self.parar.is_set():
                self.motivo_fim = self.motivo_fim or "parado pelo usuário"
                break
            if self.arquivo and fim_normal:
                self.motivo_fim = "arquivo terminou"
                break
            if fora_desde is None:
                fora_desde = time.time()
            fora_min = (time.time() - fora_desde) / 60
            if self._n == 0:
                if self.esperar_min and (time.time() - t0) / 60 > self.esperar_min:
                    self.motivo_fim = f"a live não começou em {self.esperar_min} min"
                    log.aviso("A live não começou no tempo configurado (esperar_live_min). Encerrando.")
                    break
                self.estado = "esperando live"
                if rodada == 1 or rodada % 10 == 0:
                    log.info("Live offline ou link sem vídeo. Esperando ela começar…")
            else:
                if fora_min > self.fim_offline_min:
                    self.motivo_fim = f"live encerrada (offline há {self.fim_offline_min} min)"
                    log.info(f"A live está offline há mais de {self.fim_offline_min} min: considerando encerrada.")
                    break
                self.estado = "reconectando"
                log.aviso(f"A live caiu ou travou. Tentando de novo em {espera} s.")
            self.parar.wait(espera)
            espera = min(60, espera * 2)

    def _acompanhar(self, ff, lista: Path, rodada: int):
        """Devolve True se o ffmpeg terminou normalmente (fim do arquivo/live)."""
        lidas = 0
        ultimo = time.time()
        while True:
            vivo = ff.poll() is None
            if vivo and time.time() - ultimo > self.travou_s:
                log.aviso(f"Captura sem vídeo novo há {self.travou_s} s: reiniciando (watchdog).")
                ff.kill()
                ff.wait()
                return False
            if lista.exists():
                linhas = lista.read_text(encoding="utf-8", errors="ignore").splitlines()
                for ln in linhas[lidas:]:
                    partes = ln.rsplit(",", 2)
                    if len(partes) != 3:
                        continue
                    nome, a, b = partes
                    dur = max(0.0, float(b) - float(a))
                    seg = Segmento(self._n, self.pasta / nome, self.fim_live, self.fim_live + dur, time.time())
                    with self.lock:
                        self.segs[seg.idx] = seg
                        self.fim_live = seg.fim
                    self._n += 1
                    lidas += 1
                    ultimo = time.time()
                    self.estado = "ao vivo"
                    if lidas == 1:
                        if self._n > 1:
                            fora = time.time() - (self._caiu_em or time.time())
                            self.quedas.append((round(seg.ini, 1), round(fora, 1)))
                            self.emendas.append(seg.ini)     # conteúdo da live pula aqui
                            log.ok(f"Captura voltou sozinha ({fora:.0f} s fora).")
                        elif not self.arquivo:
                            log.ok("Live online: recebendo vídeo.")
                        else:
                            pass
                    if self.ao_chegar:
                        self.ao_chegar(seg)
                    self._limpar()
            if not vivo or self.parar.is_set():
                if vivo:
                    ff.terminate()
                err = (ff.stderr.read() or b"").decode("utf-8", "ignore").strip() if ff.stderr else ""
                if err:
                    log.debug("ffmpeg captura: " + err[-800:])
                return ff.returncode == 0 and not self.parar.is_set()
            time.sleep(0.25)

    def _limpar(self):
        with self.lock:
            limite = self.fim_live - self.buffer_s
            for i in [i for i, s in self.segs.items() if s.fim < limite and i not in self.presos]:
                try:
                    os.remove(self.segs[i].arquivo)
                except OSError:
                    pass
                del self.segs[i]

    # --- leitura pelo resto do programa -----------------------------------------------------
    def trecho(self, ini: float, fim: float):
        with self.lock:
            return [s for s in sorted(self.segs.values(), key=lambda s: s.idx) if s.fim > ini and s.ini < fim]

    def prender(self, segs, sim=True):
        with self.lock:
            for s in segs:
                (self.presos.add if sim else self.presos.discard)(s.idx)
