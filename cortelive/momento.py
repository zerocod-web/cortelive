"""Momento bom: sinais por segundo (volume, palavras, risada/grito, chat) -> nota -> janela do corte.

Volume: pico de dB contra a mediana dos últimos 2 min (o "normal" da live).
Palavras: palavras quentes da config, risada escrita (kkkk, hahaha), muitas exclamações/fala rápida.
Chat (Twitch, opcional): mensagens por segundo contra a média, puxadas para trás pela defasagem.
Juiz final: Ollama local se estiver rodando; se não, a nota heurística decide.
Alinhamento do corte: início e fim caem em pausa da fala (palavras) e, dentro de ±0,3 s, no vale de energia
(regra 1 da etapa P3 do squad de edição: 'corte só em silêncio', adaptada de cortar_reel.py).
"""
import json
import re
import socket
import threading
import time
import unicodedata
from collections import deque

import numpy as np

from . import log

SR = 16000
JAN = 0.01


def _norm(t):
    t = unicodedata.normalize("NFKD", t.lower())
    return "".join(c for c in t if not unicodedata.combining(c))


RISADA = re.compile(r"^(k{3,}|(ha){2,}h?|(he){2,}|(hu){2,}|(rs){2,}|kkk\w*)$")


class Sinais:
    def __init__(self, cfg):
        self.cfg = cfg
        self.db = {}                     # segundo inteiro -> dB (pico de janelas de 100 ms)
        self.quentes = [_norm(p) for p in cfg["palavras_quentes"]]
        self.chat = deque()              # tempos (live) de mensagens
        self.chat_ativo = False

    # --- volume ------------------------------------------------------------------------------
    def novo_audio(self, ini, audio):
        n = int(0.1 * SR)
        q = audio[: len(audio) // n * n].reshape(-1, n)
        if not len(q):
            return
        db = 20 * np.log10(np.sqrt((q ** 2).mean(1)) + 1e-9)
        for k, v in enumerate(db):
            s = int(ini + k * 0.1)
            self.db[s] = max(self.db.get(s, -99.0), float(v))
        for s in [s for s in self.db if s < ini - 400]:
            del self.db[s]

    def volume_z(self, s):
        hist = [v for t, v in self.db.items() if s - 120 <= t < s]
        if len(hist) < 20 or s not in self.db:
            return 0.0
        med = float(np.median(hist))
        dev = float(np.percentile(hist, 90) - med) or 3.0
        return max(0.0, (self.db[s] - med) / max(dev, 3.0))

    # --- fala -------------------------------------------------------------------------------
    def sinais_fala(self, palavras):
        """{segundo: (pontos, [motivos])}"""
        out = {}
        txt = [(_norm(w["w"]), w) for w in palavras]
        for i, (t, w) in enumerate(txt):
            limpo = re.sub(r"[^\w ]", "", t)
            s = int(w["s"])
            pts, mot = 0.0, []
            if RISADA.match(limpo):
                pts, mot = 1.5, ["risada"]
            dupla = limpo + " " + re.sub(r"[^\w ]", "", txt[i + 1][0]) if i + 1 < len(txt) else limpo
            tripla = dupla + " " + re.sub(r"[^\w ]", "", txt[i + 2][0]) if i + 2 < len(txt) else dupla
            for q in self.quentes:
                if q == limpo or (" " in q and (q == dupla or q == tripla)):
                    pts, mot = pts + 1.0, mot + [f"falou '{q}'"]
                    break
            if "!" in w["w"] or "?!" in w["w"]:
                pts, mot = pts + 0.3, mot + ["exclamação"]
            if pts:
                a, b = out.get(s, (0.0, []))
                out[s] = (a + pts, b + mot)
        # fala rápida/gritada: muitas palavras por segundo
        por_seg = {}
        for _, w in txt:
            por_seg[int(w["s"])] = por_seg.get(int(w["s"]), 0) + 1
        for s, n in por_seg.items():
            if n >= 5:
                a, b = out.get(s, (0.0, []))
                out[s] = (a + 0.4, b + ["fala acelerada"])
        return out

    # --- chat ---------------------------------------------------------------------------------
    def chat_z(self, s):
        if not self.chat_ativo:
            return 0.0
        lag = self.cfg["chat_defasagem_s"]
        tempos = [t - lag for t in self.chat]
        janela = sum(1 for t in tempos if s - 2 <= t < s + 3)
        base = [t for t in tempos if s - 120 <= t < s - 5]
        media = len(base) / 115 * 5 if base else 0
        if janela < 4:
            return 0.0
        return max(0.0, (janela - media) / max(1.5, media ** 0.5 * 2))

    # --- nota por segundo ---------------------------------------------------------------------
    def notas(self, ini, fim, palavras):
        fala = self.sinais_fala(palavras)
        res = {}
        for s in range(int(ini), int(fim)):
            vz = self.volume_z(s)
            # fala perto do segundo (±1 s): a palavra e o grito raramente caem no mesmo segundo
            fp = max((fala.get(k, (0.0, []))[0] for k in (s - 1, s, s + 1)), default=0.0)
            fm = fala.get(s, (0.0, []))[1]
            cz = self.chat_z(s)
            # palavra sozinha é fraca: só vale cheia junto com volume alto ou chat
            combina = vz >= 0.8 or cz >= 1.0
            peso_fala = 1.0 if combina else (0.6 if any("risada" in m for m in fm) else 0.35)
            nota = min(vz, 3.0) * 0.9 + fp * peso_fala + min(cz, 3.0) * 0.8
            mot = []
            if vz >= 1.2:
                mot.append(f"volume alto ({vz:.1f}x acima do normal)")
            mot += fm
            if cz >= 1.0:
                mot.append(f"chat explodiu ({cz:.1f})")
            res[s] = (nota, mot, {"volume": round(vz, 2), "fala": round(fp, 2), "chat": round(cz, 2)})
        return res


class ChatTwitch(threading.Thread):
    """IRC anônimo (justinfan). Só conta mensagens; nada é enviado ao chat."""

    def __init__(self, canal, sinais: Sinais, relogio_live):
        super().__init__(daemon=True)
        self.canal, self.sinais, self.relogio = canal.lower(), sinais, relogio_live
        self.parar = threading.Event()
        self.total = 0
        self._reg = log.atual()

    def run(self):
        log.usar(self._reg)
        espera = 2
        while not self.parar.is_set():
            try:
                s = socket.create_connection(("irc.chat.twitch.tv", 6667), timeout=15)
                s.sendall(f"PASS SCHMOOPIIE\r\nNICK justinfan{int(time.time()) % 90000 + 10000}\r\n"
                          f"JOIN #{self.canal}\r\n".encode())
                self.sinais.chat_ativo = True
                log.info(f"Chat da Twitch conectado (#{self.canal}), só leitura.")
                buf = b""
                s.settimeout(30)
                t_con, avisou = time.time(), False
                while not self.parar.is_set():
                    if not avisou and self.total == 0 and time.time() - t_con > 120:
                        avisou = True
                        log.aviso(f"Chat da Twitch conectado, mas nenhuma mensagem em 2 min (#{self.canal}). "
                                  "O canal pode estar com chat restrito/parado; os cortes seguem só com áudio e fala.")
                    try:
                        d = s.recv(4096)
                    except socket.timeout:
                        continue
                    if not d:
                        break
                    buf += d
                    *linhas, buf = buf.split(b"\r\n")
                    for ln in linhas:
                        if ln.startswith(b"PING"):
                            s.sendall(b"PONG :tmi.twitch.tv\r\n")
                        elif b" PRIVMSG " in ln:
                            self.total += 1
                            self.sinais.chat.append(self.relogio())
                            while len(self.sinais.chat) > 20000:
                                self.sinais.chat.popleft()
                espera = 2
            except Exception as e:
                log.debug(f"chat: {e}")
            self.parar.wait(espera)
            espera = min(60, espera * 2)


# --- juiz ------------------------------------------------------------------------------------
class Juiz:
    def __init__(self, cfg):
        self.url = cfg["ollama_url"].rstrip("/")
        self.modelo = cfg.get("ollama_modelo") or ""
        self.ativo = False
        try:
            import requests
            r = requests.get(self.url + "/api/tags", timeout=2)
            nomes = [m["name"] for m in r.json().get("models", [])]
            if nomes:
                self.modelo = self.modelo or nomes[0]
                self.ativo = True
        except Exception:
            pass

    def descricao(self):
        return f"Ollama ({self.modelo})" if self.ativo else "heurística (Ollama não encontrado)"

    def julgar(self, texto, motivos, palavras=None, t_evento=None, quentes=()):
        """-> (nota 0-10 | None, título, motivo)"""
        if not self.ativo:
            return None, titulo_heuristico(texto, motivos, palavras, t_evento, quentes), \
                "; ".join(dict.fromkeys(motivos))
        import requests
        prompt = ("Você escolhe cortes de live de GTA RP para TikTok/Shorts. Trecho transcrito (português):\n"
                  f"\"{texto[:1800]}\"\nSinais detectados: {', '.join(dict.fromkeys(motivos))}.\n"
                  "Responda só JSON: {\"nota\": 0-10 (quanto prende quem não conhece a live), "
                  "\"titulo\": título curto e chamativo em português (máx 60 caracteres), "
                  "\"motivo\": uma frase}")
        try:
            r = requests.post(self.url + "/api/generate", timeout=60, json={
                "model": self.modelo, "prompt": prompt, "format": "json", "stream": False,
                "options": {"temperature": 0.3}})
            d = json.loads(r.json()["response"])
            return float(d.get("nota", 5)), str(d.get("titulo", ""))[:80], str(d.get("motivo", ""))
        except Exception as e:
            log.debug(f"ollama falhou: {e}")
            return None, titulo_heuristico(texto, motivos, palavras, t_evento, quentes), \
                "; ".join(dict.fromkeys(motivos))


# palavras que não podem fechar um título (fica com cara de frase cortada) e não contam como conteúdo
VAZIAS = set("""a o as os um uma uns umas e ou de do da dos das em no na nos nas pra pro pras pros para por pelo pela
com sem que se eu tu ele ela nos vos eles elas me te lhe mim ti vc voce isso esse essa este esta aquilo aqui ai la
la ta to tô tá ne né mas porque pq quando como onde tipo ate até ja já so só mais mano cara velho vei véi irmao irmão
ah oh ô eh hum uh é foi vai ser ter tem""".split())


def _limpa_titulo(ws):
    """Tira pontuação, repetição colada ('mano mano') e palavras vazias do fim. -> lista de palavras."""
    t = [re.sub(r"[.,;:…\"]+", "", w).strip() for w in ws]
    t = [w for w in t if w]
    out = []
    for w in t:
        if not out or _norm(w).strip("!?") != _norm(out[-1]).strip("!?"):
            out.append(w)
    while out and _norm(out[-1]).strip("!?") in VAZIAS:
        out.pop()
    return out


def _conteudo(ws):
    return sum(1 for w in ws if len(_norm(w).strip("!?")) >= 3 and _norm(w).strip("!?") not in VAZIAS)


def titulo_heuristico(texto, motivos, palavras=None, t_evento=None, quentes=()):
    """Sem Ollama: até 8 palavras da frase mais forte perto do momento (palavra-gatilho, exclamação, risada).
    Precisa de pelo menos 3 palavras de conteúdo e terminar em palavra de conteúdo; senão tenta a próxima
    frase mais forte; por fim a primeira frase completa do corte."""
    if palavras and t_evento is not None:
        perto = [w for w in palavras if t_evento - 10 <= w["s"] <= t_evento + 8]
        if perto:
            q = [_norm(x) for x in quentes]

            def forca(i):
                w = perto[i]
                n = re.sub(r"[^\w ]", "", _norm(w["w"]))
                f = 3.0 if n in q else 0.0
                f += 2.0 if RISADA.match(n) else 0.0
                f += 1.5 if "!" in w["w"] or "?" in w["w"] else 0.0
                return f - abs(w["s"] - t_evento) / 6
            vistos = set()
            for i in sorted(range(len(perto)), key=forca, reverse=True)[:12]:
                # frase em volta do gatilho, sem atravessar pausa longa nem fim de frase anterior
                a = i
                while a > 0 and i - a < 4 and perto[a]["s"] - perto[a - 1]["e"] < 0.7 \
                        and perto[a - 1]["w"][-1:] not in ".!?":
                    a -= 1
                if a in vistos:
                    continue
                vistos.add(a)
                frase = [w["w"] for w in perto[a:a + 9]]
                for k, t in enumerate(frase):   # para no fim de frase depois do gatilho
                    if k >= i - a and t[-1:] in ".!?":
                        frase = frase[:k + 1]
                        break
                ws = _limpa_titulo(frase)[:8]
                ws = _limpa_titulo(ws)
                if _conteudo(ws) >= 3:
                    t = " ".join(ws)
                    return (t[0].upper() + t[1:])[:70]
    for f in re.split(r"(?<=[.!?…])\s+", texto):
        ws = _limpa_titulo(f.split()[:8])
        if _conteudo(ws) >= 3:
            t = " ".join(ws)
            return (t[0].upper() + t[1:])[:70]
    return "Momento da live"


# --- alinhamento -----------------------------------------------------------------------------
def energia_db(audio):
    n = int(JAN * SR)
    q = audio[: len(audio) // n * n].reshape(-1, n)
    return 20 * np.log10(np.sqrt((q ** 2).mean(1)) + 1e-9)


def janela_corte(t_evento, palavras, fim_disponivel, cfg):
    """Janela [ini, fim] em volta do evento, 30-60 s, presa em pausas da fala."""
    dmin, dmax = cfg["duracao_min"], cfg["duracao_max"]
    ini = t_evento - cfg["antes_do_momento"]
    fim = min(fim_disponivel, t_evento + cfg["depois_do_momento"])
    if fim - ini < dmin:
        ini = fim - dmin
    ini = max(0.0, ini)

    def pausa_antes(t, busca=8.0):
        # pausa (>= 0,35 s entre palavras) mais perto de t, olhando para trás até 'busca' s
        cand = [(b["s"] - a["e"], a["e"], b["s"]) for a, b in zip(palavras, palavras[1:])
                if t - busca <= b["s"] <= t + 1.0 and b["s"] - a["e"] >= 0.35]
        if cand:
            gap, a, b = max(cand, key=lambda c: (c[0] >= 0.6, -abs(c[2] - t)))
            return max(a, b - 0.35)
        return t

    def pausa_depois(t, busca=8.0):
        cand = [(b["s"] - a["e"], a["e"], b["s"]) for a, b in zip(palavras, palavras[1:])
                if t - 1.0 <= a["e"] <= t + busca and b["s"] - a["e"] >= 0.35 and a["e"] <= fim_disponivel]
        if cand:
            gap, a, b = max(cand, key=lambda c: (c[0] >= 0.6, -abs(c[1] - t)))
            return min(b, a + 0.45)
        return t

    ini = pausa_antes(ini)
    fim = min(fim_disponivel, pausa_depois(fim))
    if fim - ini > dmax:
        fim = pausa_depois(ini + dmax - 6, busca=6)
        fim = min(fim, ini + dmax)
    if fim - ini < dmin * 0.8:
        fim = min(fim_disponivel, ini + dmin)
    return ini, fim


def ajustar_no_vale(t, audio, a0, raio=0.3):
    """Move t para o ponto de menor energia (média de 30 ms) dentro de ±raio — 'corte só em silêncio'."""
    db = energia_db(audio)
    if not len(db):
        return t
    i0, i1 = max(1, int((t - raio - a0) / JAN)), min(len(db) - 2, int((t + raio - a0) / JAN))
    if i1 <= i0:
        return t
    sm = np.convolve(db, np.ones(3) / 3, "same")
    return a0 + (i0 + int(np.argmin(sm[i0:i1 + 1]))) * JAN
