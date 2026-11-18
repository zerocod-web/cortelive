"""Captura: live (streamlink, yt-dlp de reserva) ou arquivo lido a 1x -> FFmpeg em segmentos .ts.

Cada segmento pronto vira um Segmento(idx, ini, fim) no tempo da live (segundos desde o começo da captura).
O tempo é contado somando as durações reais, então uma reconexão não bagunça a linha do tempo
(o buraco da queda fica registrado no log). Buffer rolante: segmentos mais velhos que N minutos são apagados.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from . import log


_NO_WIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)


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


_PADROES_ERRO = (
    (("403", "forbidden"), "O site recusou o acesso à live (erro 403). Pode ser bloqueio por região, live restrita ou limite de acessos."),
    (("429", "too many requests"), "O site pediu para esperar (muitas tentativas seguidas). O CorteLive tenta de novo sozinho."),
    (("sign in", "login", "cookies", "members-only", "members only", "age-restricted", "confirm your age", "private video"),
     "A live exige login, é só para membros ou tem restrição de idade. O CorteLive não consegue abrir esse tipo de live sem conta."),
    (("unplayable", "not available", "unavailable"), "O site diz que esse vídeo não está disponível para reprodução (live restrita, bloqueada ou gravação indisponível)."),
    (("no plugin can handle", "unsupported url", "is not a valid url", "invalid url"),
     "Link não reconhecido. Use o endereço do canal ou da live (Twitch, YouTube ou Kick)."),
    (("unable to open url", "unable to connect", "connection", "timed out", "timeout", "getaddrinfo", "name resolution", "ssl"),
     "Não foi possível conectar ao site da live. Confira a internet; o CorteLive tenta de novo sozinho."),
    (("javascript runtime", "signature", "nsig", "unable to extract", "extractor error", "http error 4", "http error 5"),
     "O site mudou algo e o leitor de lives não conseguiu abrir o link. Atualize o CorteLive para a versão mais recente."),
)
_PADROES_OFFLINE = ("no playable streams found", "offline", "not currently live", "is not live", "isn't live",
                    "this live event will begin", "premieres in", "no streams found")


def explicar_erro_fonte(texto: str):
    """Lê o que o streamlink/yt-dlp escreveu e separa 'live offline de verdade' de erro real.

    Devolve (offline, mensagem_em_portugues_ou_None, trecho_original)."""
    bruto = (texto or "").strip()
    if not bruto:
        return True, None, ""
    baixo = bruto.lower()
    trecho = " | ".join(l.strip() for l in bruto.splitlines() if l.strip())[-300:]
    for chaves, msg in _PADROES_ERRO:
        if any(c in baixo for c in chaves):
            return False, msg, trecho
    if any(c in baixo for c in _PADROES_OFFLINE):
        return True, None, trecho
    return False, "Não deu para abrir a live e o motivo não é só 'offline'.", trecho


def normalizar_url(url: str) -> str:
    """Endereço de canal do YouTube (@nome, /channel/ID, /c/nome, /user/nome) -> .../live, que aponta
    para a transmissão em andamento. Sem isso o streamlink devolve 'sem vídeo' mesmo com o canal ao vivo."""
    if not url:
        return url
    base = url.strip().split("?")[0].split("#")[0].rstrip("/")
    baixo = base.lower()
    if "youtube.com/" in baixo:
        resto = base.split("youtube.com/", 1)[1].split("/")
        if resto and resto[0]:
            canal_so = (resto[0].startswith("@") and len(resto) == 1) or                        (resto[0] in ("channel", "c", "user") and len(resto) == 2)
            streams = (resto[0].startswith("@") and len(resto) == 2 and resto[1] in ("streams", "featured", "videos")) or                       (resto[0] in ("channel", "c", "user") and len(resto) == 3 and resto[2] in ("streams", "featured", "videos"))
            if canal_so:
                return base + "/live"
            if streams:
                return base.rsplit("/", 1)[0] + "/live"
    return url.strip()


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
        self._fonte_proc = None
        self._proc_lock = threading.Lock()
        super().__init__(daemon=True)
        self._reg = log.atual()
        self.estado = "iniciando"
        self.pasta = pasta
        self.url, self.arquivo = normalizar_url(url) if url else url, arquivo
        self._esperou_live = False      # ficou aguardando a live abrir (mostra "Live iniciada" quando chegar)
        self.seg_s, self.buffer_s = seg_s, buffer_min * 60
        self.ao_chegar = ao_chegar
        self.segs: dict[int, Segmento] = {}
        self.lock = threading.Lock()
        self.fim_live = 0.0
        self.parar = threading.Event()
        self.acabou = threading.Event()
        self.presos: set[int] = set()   # segmentos em uso por um corte (não apagar)
        self._n = 0
        self._usar_ytdlp = False        # alterna para o yt-dlp quando o streamlink falha de verdade
        self._erro_fonte = None         # (offline, mensagem, trecho) da última rodada sem vídeo
        self._ultimo_aviso_fonte = None

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
        erros_fonte = getattr(self, "_erros_fonte", None)
        """Processo que escreve o vídeo da live no stdout (ou None para arquivo)."""
        sl = _bin("streamlink")
        if sl and not self._usar_ytdlp:
            cmd = [sl, "--stdout", "--loglevel", "error", "--twitch-disable-ads",
                   "--hls-live-edge", "2", self.url, "720p60,720p,best"]
            nome = "streamlink"
        else:
            cmd = [_bin("yt-dlp"), "--no-warnings", "-f", "best[height<=1080]/best", "-o", "-", self.url]
            nome = "yt-dlp"
        if os.name == "nt":
            # O intermediário entra no próprio Job antes de iniciar o downloader.
            cmd = [sys.executable, "-m", "cortelive.processos", *cmd]
        return subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=erros_fonte if erros_fonte is not None else subprocess.DEVNULL,
                                creationflags=_NO_WIN), nome

    def solicitar_parada(self, motivo="parado pelo usuário"):
        if not self.acabou.is_set() and not self.motivo_fim:
            self.motivo_fim = motivo
        self.parar.set()
        with self._proc_lock:
            procs = (self._ff, self._fonte_proc)
        for p in procs:
            if p is not None and p.poll() is None:
                try:
                    p.kill()
                except OSError:
                    pass

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
            n_antes = self._n
            fim_normal = self._rodada(rodada)
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
                self._esperou_live = True
                erro = self._erro_fonte
                if erro and not erro[0] and not self.arquivo:
                    _, msg, trecho, nome_fonte = erro
                    chave = (msg, trecho)
                    if chave != self._ultimo_aviso_fonte or rodada % 10 == 0:
                        self._ultimo_aviso_fonte = chave
                        log.aviso(f"{msg} (detalhe: {nome_fonte or 'leitor'}: {trecho})")
                    # streamlink falhou de verdade: tenta o yt-dlp na próxima rodada (e volta ao streamlink depois)
                    if _bin("yt-dlp") and _bin("streamlink"):
                        self._usar_ytdlp = not self._usar_ytdlp
                elif rodada == 1 or rodada % 10 == 0:
                    self._ultimo_aviso_fonte = None
                    log.info("Live offline — aguardando. Vou checar de novo sozinho a cada 30 segundos.")
            else:
                if fora_min > self.fim_offline_min:
                    self.motivo_fim = f"live encerrada (offline há {self.fim_offline_min} min)"
                    log.info(f"A live está offline há mais de {self.fim_offline_min} min: considerando encerrada.")
                    break
                self.estado = "reconectando"
                log.aviso(f"A live caiu ou travou. Tentando de novo em {espera} s.")
            self.parar.wait(espera)
            espera = min(30 if self._n == 0 else 60, espera * 2)

    def _rodada(self, rodada):
        fonte = ff = None
        nome = ""
        self._erro_fonte = None
        with tempfile.TemporaryFile() as erros, tempfile.TemporaryFile() as erros_fonte:
            try:
                if self.arquivo:
                    extra = ("-re", "-ss", f"{self.fim_live:.3f}") if self.fim_live > 0 else ("-re",)
                    cmd, lista = self._ffmpeg_seg(rodada, str(self.arquivo), extra_in=extra)
                    ff = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stderr=erros, creationflags=_NO_WIN)
                    with self._proc_lock:
                        self._ff = ff
                    if self.parar.is_set():
                        return False
                    if rodada == 1:
                        log.info("Lendo o arquivo em tempo real (1x), como se fosse live.")
                    else:
                        log.info(f"Captura reiniciada, continuando do ponto {self.fim_live:.0f} s.")
                else:
                    self._erros_fonte = erros_fonte
                    fonte, nome = self._fonte()
                    with self._proc_lock:
                        self._fonte_proc = fonte
                    if self.parar.is_set():
                        return False
                    cmd, lista = self._ffmpeg_seg(rodada, "pipe:0")
                    ff = subprocess.Popen(cmd, stdin=fonte.stdout, stderr=erros, creationflags=_NO_WIN)
                    fonte.stdout.close()
                    with self._proc_lock:
                        self._ff = ff
                    if self.parar.is_set():
                        return False
                    log.debug(f"rodada {rodada} via {nome}")
                return self._acompanhar(ff, lista, rodada)
            finally:
                with self._proc_lock:
                    self._ff = None
                    self._fonte_proc = None
                for p in (ff, fonte):
                    if p is not None:
                        if p.poll() is None:
                            try:
                                p.kill()
                            except OSError:
                                pass
                        p.wait()
                if fonte and fonte.stdout:
                    fonte.stdout.close()
                if not self.arquivo:
                    try:
                        erros_fonte.seek(max(0, erros_fonte.tell() - 1500))
                        self._erro_fonte = explicar_erro_fonte(erros_fonte.read().decode("utf-8", "ignore"))
                        self._erro_fonte += (nome if fonte is not None else "",)
                    except (OSError, UnboundLocalError):
                        self._erro_fonte = None
                erros.seek(max(0, erros.tell() - 800))
                erro = erros.read().decode("utf-8", "ignore").strip()
                if erro:
                    log.debug("ffmpeg captura: " + erro)

    def _acompanhar(self, ff, lista: Path, rodada: int):
        """Devolve True se o ffmpeg terminou normalmente (fim do arquivo/live)."""
        lidas = 0
        ultimo = time.time()
        while True:
            vivo = ff.poll() is None
            if vivo and time.time() - ultimo > self.travou_s:
                log.aviso(f"Captura sem vídeo novo há {self.travou_s} s: reiniciando (watchdog).")
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
                            log.ok("Live iniciada — comecei a ver." if self._esperou_live else "Live online: recebendo vídeo.")
                        else:
                            pass
                    if self.ao_chegar:
                        self.ao_chegar(seg)
                    self._limpar()
            if not vivo or self.parar.is_set():
                return ff.returncode == 0 and not self.parar.is_set()
            self.parar.wait(0.25)

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
