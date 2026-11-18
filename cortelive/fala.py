"""Fala: transcrição incremental em português com tempo por palavra.

Motores:
- faster-whisper CUDA (NVIDIA). O PyAV é bloqueado nesta máquina, então o módulo 'av' é trocado por um vazio
  antes do import e o áudio entra como numpy (vindo do ffmpeg).
- whisper.cpp (AMD/Intel, binário Vulkan) por arquivo WAV temporário, saída JSON com uma palavra por segmento.
- faster-whisper CPU int8 (último recurso).

Janela: cada pedaço novo é transcrito junto com ~2 s do anterior (sobreposição); palavras que caem
perto da borda direita ficam para a próxima rodada, para não cortar palavra no meio.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import types
import wave
from pathlib import Path

import numpy as np

from . import hw, log

SR = 16000


def audio_de(arquivo) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-nostdin", "-i", str(arquivo), "-f", "s16le",
                          "-ac", "1", "-ar", str(SR), "-"], capture_output=True).stdout
    return np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0


def juntar_fragmentos(words):
    """Fragmento sem espaço na frente pertence à palavra anterior (ideia do subtitles.py do OpenShorts, MIT)."""
    out = []
    for w in words:
        t = w["w"]
        if out and t and not t.startswith(" "):
            out[-1]["w"] += t
            out[-1]["e"] = w["e"]
        else:
            out.append(dict(w))
    for w in out:
        w["w"] = w["w"].strip()
    return [w for w in out if w["w"]]


class Transcritor:
    def __init__(self, modo: hw.Modo, modelo: str | None = None):
        if modelo:
            import dataclasses
            modo = dataclasses.replace(modo, modelo_whisper=modelo)
        self.modo = modo
        self.nome_modelo = modo.modelo_whisper
        self.motor = modo.transcricao
        self._m = None
        if self.motor.startswith("faster-whisper"):
            sys.modules.setdefault("av", types.ModuleType("av"))
            if self.motor == "faster-whisper-cuda":
                hw.libs_cuda()
            from faster_whisper import WhisperModel
            if self.motor == "faster-whisper-cuda":
                self._m = WhisperModel(modo.modelo_whisper, device="cuda", compute_type="float16")
            else:
                self._m = WhisperModel(modo.modelo_whisper, device="cpu", compute_type="int8",
                                       cpu_threads=max(2, (os.cpu_count() or 4) - 2))
        elif self.motor == "whisper-cpp":
            if not modo.whisper_cpp:
                raise SystemExit("whisper.cpp não encontrado (ferramentas\\whispercpp).")
            self.modelo_cpp = hw.RAIZ / "ferramentas" / "whispercpp" / f"ggml-{modo.modelo_whisper}.bin"
            if not self.modelo_cpp.exists():
                self.modelo_cpp = next((hw.RAIZ / "ferramentas" / "whispercpp").glob("ggml-*.bin"), None)
            if not self.modelo_cpp:
                raise SystemExit("Modelo ggml do whisper.cpp não encontrado em ferramentas\\whispercpp.")

    def transcrever(self, audio: np.ndarray, t0: float):
        """Devolve [{'w','s','e','p'}] no tempo da live (t0 = início do áudio)."""
        if len(audio) < SR // 2:
            return []
        if self._m is not None:
            segs, _ = self._m.transcribe(audio, language="pt", word_timestamps=True, beam_size=1,
                                         vad_filter=True, condition_on_previous_text=False,
                                         vad_parameters={"min_silence_duration_ms": 300})
            ws = [{"w": w.word, "s": float(t0 + w.start), "e": float(t0 + w.end), "p": float(w.probability)}
                  for sg in segs for w in (sg.words or [])]
            return juntar_fragmentos(ws)
        return self._cpp(audio, t0)

    def _cpp(self, audio, t0):
        with tempfile.TemporaryDirectory() as d:
            wav = Path(d) / "a.wav"
            with wave.open(str(wav), "wb") as f:
                f.setnchannels(1); f.setsampwidth(2); f.setframerate(SR)
                f.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())
            base = Path(d) / "out"
            cmd = [self.modo.whisper_cpp, "-m", str(self.modelo_cpp), "-l", "pt", "-ml", "1", "-sow",
                   "-oj", "-of", str(base), "-np", "-t", str(max(2, (os.cpu_count() or 4) - 2)), str(wav)]
            r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
            js = base.with_suffix(".json")
            if r.returncode != 0 or not js.exists():
                log.debug("whisper.cpp falhou: " + (r.stderr or "")[-500:])
                return []
            dados = json.loads(js.read_text(encoding="utf-8", errors="ignore"))
        ws = []
        for sg in dados.get("transcription", []):
            txt = sg.get("text", "")
            if not txt.strip() or txt.strip().startswith("["):
                continue
            o = sg.get("offsets", {})
            ws.append({"w": txt, "s": t0 + o.get("from", 0) / 1000, "e": t0 + o.get("to", 0) / 1000, "p": 1.0})
        return juntar_fragmentos(ws)


class Fala:
    """Guarda áudio por segmento e transcreve em janelas sobrepostas."""

    def __init__(self, transcritor: Transcritor, sobreposicao=2.0, margem_borda=0.8):
        self.tr = transcritor
        self.audio = {}            # idx -> (ini, np.array)
        self.palavras = []         # [{'w','s','e','p'}] em ordem
        self.feito_ate = 0.0       # tempo da live até onde as palavras são definitivas
        self.sobre, self.borda = sobreposicao, margem_borda
        self.tempo_trans = []      # (segundos de áudio, segundos gastos)
        self.ultimo_fator = 0.0    # segundos gastos / segundos de áudio na última rodada
        self.por_modelo = {}       # modelo -> [(áudio, gasto)]

    def novo_segmento(self, seg, audio):
        self.audio[seg.idx] = (seg.ini, audio)
        # descarta áudio velho (> 6 min)
        for i in [i for i, (ini, _) in self.audio.items() if ini < seg.fim - 360]:
            del self.audio[i]

    def audio_trecho(self, ini, fim):
        partes = []
        for i in sorted(self.audio):
            a0, a = self.audio[i]
            a1 = a0 + len(a) / SR
            if a1 <= ini or a0 >= fim:
                continue
            k0, k1 = max(0, int((ini - a0) * SR)), min(len(a), int((fim - a0) * SR))
            partes.append(a[k0:k1])
        return np.concatenate(partes) if partes else np.zeros(0, np.float32)

    gen = 0   # muda quando o watchdog troca o motor: resultado de rodada abandonada é ignorado

    buracos = None   # trechos pulados pelo watchdog, retranscritos depois: [[ini, fim]]

    def pular_ate(self, t):
        self.gen += 1
        if self.buracos is None:
            self.buracos = []
        if t > self.feito_ate:
            self.buracos.append([self.feito_ate, t])
        self.feito_ate = max(self.feito_ate, t)

    def buraco_em(self, ini, fim):
        return any(a < fim and b > ini for a, b in (self.buracos or []))

    def preencher_buraco(self, max_s=30.0):
        """Retranscreve até max_s do buraco mais antigo e encaixa as palavras na ordem. -> (ini, fim, n) ou None."""
        if not self.buracos:
            return None
        a, b = self.buracos[0]
        fim = min(b, a + max_s)
        aud = self.audio_trecho(max(0.0, a - 0.5), fim + 0.5)
        if len(aud) < SR // 2:          # áudio já saiu do buffer: desiste do buraco
            self.buracos.pop(0)
            return (a, fim, -1)
        ws = self.tr.transcrever(aud, max(0.0, a - 0.5))
        novas = [w for w in ws if w["s"] >= a - 0.05 and w["e"] <= (fim if fim < b else b + 0.3)]
        if fim < b:   # não corta palavra no meio: continua da última aceita
            novas = [w for w in novas if w["e"] <= fim - 0.6] or novas
            prox = novas[-1]["e"] if novas else fim
            self.buracos[0][0] = max(prox, a + 1.0)
        else:
            self.buracos.pop(0)
        ja = {(round(w["s"], 1), w["w"]) for w in self.palavras if a - 1 <= w["s"] <= fim + 1}
        novas = [w for w in novas if (round(w["s"], 1), w["w"]) not in ja]
        self.palavras = sorted(self.palavras + novas, key=lambda w: w["s"])
        return (a, fim, len(novas))

    def avancar(self, fim_disponivel: float, final=False):
        """Transcreve de (feito_ate - sobreposição) até fim_disponivel."""
        import time
        gen = self.gen
        ini = max(0.0, self.feito_ate - self.sobre)
        if fim_disponivel - self.feito_ate < 1.0:
            return []
        a = self.audio_trecho(ini, fim_disponivel)
        t = time.time()
        tr = self.tr
        ws = tr.transcrever(a, ini)
        if gen != self.gen:
            return []
        self.tempo_trans.append((len(a) / SR, time.time() - t))
        self.ultimo_fator = (time.time() - t) / max(0.5, len(a) / SR)
        self.por_modelo.setdefault(tr.nome_modelo, []).append((len(a) / SR, time.time() - t))
        corte = fim_disponivel if final else fim_disponivel - self.borda
        novas = [w for w in ws if w["s"] >= self.feito_ate - 0.05 and w["e"] <= corte]
        if self.palavras and novas:
            ult = self.palavras[-1]
            novas = [w for w in novas if w["s"] >= ult["e"] - 0.05]
        self.palavras.extend(novas)
        # fecha até o fim da última palavra aceita, ou até a borda se não houve fala
        pendentes = [w for w in ws if w["e"] > corte]
        if pendentes:
            # para logo antes da palavra que ficou na borda (ela entra na próxima rodada)
            alvo = min(w["s"] for w in pendentes) - 0.05
            if novas:
                alvo = max(alvo, novas[-1]["e"])
            self.feito_ate = max(self.feito_ate, alvo)
        else:
            self.feito_ate = max(self.feito_ate, corte)
        return novas

    def palavras_entre(self, ini, fim):
        return [w for w in self.palavras if w["e"] > ini and w["s"] < fim]
