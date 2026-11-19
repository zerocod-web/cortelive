"""Momento bom: sinais por segundo (volume, palavras, risada/grito, chat) -> nota -> janela do corte.

Volume: pico de dB contra a mediana dos últimos 2 min (o "normal" da live).
Palavras: palavras quentes da config, risada escrita (kkkk, hahaha), muitas exclamações/fala rápida.
Chat (Twitch, opcional): mensagens por segundo contra a média, puxadas para trás pela defasagem.
Seleção: a nota combina os sinais de áudio, fala e chat.
Alinhamento do corte: início e fim caem em pausa da fala (palavras) e, dentro de ±0,3 s, no vale de energia
(regra 1 da etapa P3 do squad de edição: 'corte só em silêncio', adaptada de cortar_reel.py).
"""
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


# muletas: o streamer fala o tempo todo, não indicam momento bom (e não entram nas palavras quentes)
MULETAS = {"mano", "rapaz", "pegou", "cara", "velho", "vei"}


class Sinais:
    def __init__(self, cfg):
        self.cfg = cfg
        self.db = {}                     # segundo inteiro -> dB (pico de janelas de 100 ms)
        self.risada_ac = set()           # segundos com risada detectada no áudio (pulsos de 3,5-8 Hz)
        self.quentes = [q for q in (_norm(p) for p in cfg["palavras_quentes"]) if q not in MULETAS]
        self.apoio = [q for q in (_norm(p) for p in cfg.get("palavras_apoio", [])) if q not in MULETAS]
        self.chat = deque()              # tempos (live) de mensagens
        self.chat_ativo = False
        self.chat_desde = None           # tempo (live) da conexão/1ª mensagem: base do chat só vale depois de 30 s

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
        self.risada_ac |= detectar_risada(audio, ini)
        for s in [s for s in self.risada_ac if s < ini - 400]:
            self.risada_ac.discard(s)

    def volume_z(self, s):
        """Quão acima do 'normal' (só segundos com atividade; silêncio profundo não conta como normal) está s."""
        hist = [v for t, v in self.db.items() if s - 120 <= t < s]
        ativos = [v for v in hist if v > -46.0]
        if len(ativos) < 20 or s not in self.db:
            return 0.0
        if self.db[s] < self.cfg.get("volume_piso_db", -26.0):    # fala normal depois de silêncio não é grito
            return 0.0
        med = float(np.median(ativos))
        dev = float(np.percentile(ativos, 90) - med) or 3.0
        return max(0.0, (self.db[s] - med) / max(dev, 3.0))

    # --- fala -------------------------------------------------------------------------------
    def sinais_fala(self, palavras):
        """{segundo: (pontos, [motivos])}. Palavra repetida no mesmo segundo vale uma vez; 'apoio' vale 0,3."""
        out = {}
        txt = [(_norm(w["w"]), w) for w in palavras]
        vistos = {}
        for i, (t, w) in enumerate(txt):
            limpo = re.sub(r"[^\w ]", "", t)
            s = int(w["s"])
            pts, mot = 0.0, []
            if RISADA.match(limpo):
                pts, mot = 1.5, ["risada"]
            dupla = limpo + " " + re.sub(r"[^\w ]", "", txt[i + 1][0]) if i + 1 < len(txt) else limpo
            tripla = dupla + " " + re.sub(r"[^\w ]", "", txt[i + 2][0]) if i + 2 < len(txt) else dupla
            for lista, peso in ((self.quentes, 1.0), (self.apoio, 0.3)):
                achou = next((q for q in lista if q == limpo or (" " in q and (q == dupla or q == tripla))), None)
                if achou:
                    if achou not in vistos.setdefault(s, set()):
                        vistos[s].add(achou)
                        pts, mot = pts + peso, mot + [f"falou '{achou}'"]
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
        for s in self.risada_ac:
            a, b = out.get(s, (0.0, []))
            if "risada" not in b:
                out[s] = (a + 1.2, b + ["risada"])
        return out

    # --- chat ---------------------------------------------------------------------------------
    def chat_z(self, s):
        """Chat contra a taxa REAL observada (mediana/p90 de janelas de 5 s); só depois de 30 s de histórico."""
        if not self.chat_ativo:
            return 0.0
        lag = self.cfg["chat_defasagem_s"]
        desde = self.chat_desde
        if desde is None and self.chat:
            desde = self.chat[0]
        if desde is None or (s - 5) - (desde - lag) < 30.0:
            return 0.0
        tempos = [t - lag for t in self.chat]
        janela = sum(1 for t in tempos if s - 2 <= t < s + 3)
        if janela < 4:
            return 0.0
        ini_base = max(s - 120.0, desde - lag)
        n_bins = int((s - 5 - ini_base) // 5)
        if n_bins < 6:
            return 0.0
        cont = [0] * n_bins
        for t in tempos:
            k = int((t - ini_base) // 5)
            if 0 <= k < n_bins:
                cont[k] += 1
        med = float(np.median(cont))
        p90 = float(np.percentile(cont, 90))
        dev = max(1.5, p90 - med, 2.0 * med ** 0.5)
        return max(0.0, (janela - med) / dev)

    # --- nota por segundo ---------------------------------------------------------------------
    def notas(self, ini, fim, palavras):
        fala = self.sinais_fala(palavras)
        res = {}
        for s in range(int(ini), int(fim)):
            vz = self.volume_z(s)
            # fala perto do segundo (±1 s): a palavra e o grito raramente caem no mesmo segundo
            fp = min(max((fala.get(k, (0.0, []))[0] for k in (s - 1, s, s + 1)), default=0.0),
                     self.cfg.get("fala_teto", 1.8))
            fm = fala.get(s, (0.0, []))[1]
            cz = self.chat_z(s)
            # chat sozinho nunca aprova trecho mudo: precisa de volume ou fala por perto
            if cz and not (vz >= 0.6 or fp >= 0.4):
                cz = 0.0
            # palavra sozinha é fraca: só vale cheia junto com volume alto ou chat
            combina = vz >= self.cfg.get("combina_volume_z", 1.15) or cz >= 1.0
            peso_fala = 1.0 if combina else (0.6 if any("risada" in m for m in fm) else 0.35)
            # volume sozinho sem fala nem chat não é momento do streamer (é tiro/motor/barulho do jogo)
            peso_vol = 0.9 if (fp >= 0.3 or cz >= 0.5) else 0.55
            nota = min(vz, 3.0) * peso_vol + fp * peso_fala + min(cz, 3.0) * 0.8
            mot = []
            if vz >= 1.2:
                mot.append(f"volume alto ({vz:.1f}x acima do normal)")
            mot += fm
            if cz >= 1.0:
                mot.append(f"chat explodiu ({cz:.1f})")
            res[s] = (nota, mot, {"volume": round(vz, 2), "fala": round(fp, 2), "chat": round(cz, 2)})
        return res


def detectar_risada(audio, ini, db_min=-30.0):
    """Risada pelo ritmo: 'ha-ha-ha' = pulsos de voz a 3,5-8 Hz (5 seguidos, espaçamento regular, vales fundos)
    numa janela de 1,6 s com energia de voz. Só NumPy. -> conjunto de segundos inteiros marcados."""
    n = int(0.02 * SR)
    q = audio[: len(audio) // n * n].reshape(-1, n)
    out = set()
    if len(q) < 80:
        return out
    e = np.convolve(np.sqrt((q ** 2).mean(1)), np.ones(3) / 3, "same")
    for a in range(0, len(e) - 79, 25):
        w = e[a:a + 80]
        if 20 * np.log10(w.mean() + 1e-9) < db_min:
            continue
        pk = [i for i in range(1, 79) if w[i] >= w[i - 1] and w[i] > w[i + 1] and w[i] > 1.5 * np.median(w)]
        fund = []                         # funde picos a menos de 100 ms, ficando com o maior
        for i in pk:
            if fund and i - fund[-1] < 5:
                if w[i] > w[fund[-1]]:
                    fund[-1] = i
            else:
                fund.append(i)
        for k in range(len(fund) - 4):    # 5 picos seguidos, 125-285 ms entre eles, regulares, vale fundo
            g = fund[k:k + 5]
            iv = np.diff(g)
            if iv.min() < 6 or iv.max() > 14 or iv.std() / iv.mean() > 0.2:
                continue
            vales = [w[g[j]:g[j + 1] + 1].min() / min(w[g[j]], w[g[j + 1]]) for j in range(4)]
            if max(vales) < 0.25:
                t0 = ini + a * 0.02
                out.update(range(int(t0), int(t0 + 1.6) + 1))
                break
    return out


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
                self.sinais.chat_desde = self.relogio()
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


# palavras que não podem fechar um título (fica com cara de frase cortada) e não contam como conteúdo
VAZIAS = set("""a o as os um uma uns umas e ou de do da dos das em no na nos nas pra pro pras pros para por pelo pela
com sem que se eu tu ele ela nos vos eles elas me te lhe mim ti vc voce isso esse essa este esta aquilo aqui ai la
la ta to tô tá ne né mas porque pq quando como onde tipo ate até ja já so só mais mano cara velho vei véi irmao irmão
ah oh ô eh hum uh é foi vai ser ter tem""".split())

# palavras e conectores subordinativos que deixam o título com cara de corte no meio da frase
VAZIAS_INICIO = set("""a o as os um uma uns umas e ou de do da dos das em no na nos nas pra pro pras pros para por pelo pela
com que se como quando onde tipo entao então dai daí ai aí ne né mas porque pq ja já so só ah oh ô eh hum uh
mano cara velho vei véi eita vish""".split())
CLITICOS = ("-se", "-te", "-me", "-nos", "-vos", "-lhe", "-lhes")


def _limpa_titulo(ws):
    """Tira pontuação, repetição colada ('mano mano'), conectores do início e palavras vazias do fim."""
    t = [re.sub(r"[.,;:…\"]+", "", w).strip() for w in ws]
    t = [w for w in t if w]
    out = []
    for w in t:
        if not out or _norm(w).strip("!?") != _norm(out[-1]).strip("!?"):
            out.append(w)
    while out and _norm(out[-1]).strip("!?") in VAZIAS:
        out.pop()
    while len(out) > 3 and _norm(out[0]).strip("!?") in VAZIAS_INICIO:
        out.pop(0)
    while out and _norm(out[-1]).strip("!?") in VAZIAS:
        out.pop()
    if out and len(out) > 3 and any(out[-1].lower().endswith(sfx) for sfx in CLITICOS):
        out.pop()
    return out


def _conteudo(ws):
    return sum(1 for w in ws if len(_norm(w).strip("!?")) >= 3 and _norm(w).strip("!?") not in VAZIAS)


def titulo_heuristico(texto, motivos, palavras=None, t_evento=None, quentes=()):
    """Usa até 10 palavras da frase mais forte perto do momento (palavra-gatilho, exclamação, risada).
    Preserva orações completas sem cortar na 8ª palavra em verbo/conector; limpa conectores soltos do início;
    garante pontuação adequada."""
    if palavras and t_evento is not None:
        perto = [w for w in palavras if t_evento - 10 <= w["s"] <= t_evento + 8]
        if perto:
            q = [_norm(x) for x in quentes]

            def forca(i):
                w = perto[i]
                n = re.sub(r"[^\w ]", "", _norm(w["w"]))
                f = 3.5 if n in q else 0.0
                f += 2.5 if RISADA.match(n) else 0.0
                f += 2.0 if "!" in w["w"] or "?" in w["w"] else 0.0
                return f - abs(w["s"] - t_evento) / 5
            vistos = set()
            for i in sorted(range(len(perto)), key=forca, reverse=True)[:14]:
                # frase em volta do gatilho, sem atravessar pausa longa nem fim de frase anterior
                a = i
                while a > 0 and i - a < 5 and perto[a]["s"] - perto[a - 1]["e"] < 0.7 \
                        and perto[a - 1]["w"][-1:] not in ".!?":
                    a -= 1
                if a in vistos:
                    continue
                vistos.add(a)
                frase = [w["w"] for w in perto[a:a + 12]]
                for k, t in enumerate(frase):   # para no fim de frase depois do gatilho
                    if k >= i - a and t[-1:] in ".!?":
                        frase = frase[:k + 1]
                        break
                else:
                    if len(frase) > 9:
                        frase = frase[:9]
                ws = _limpa_titulo(frase)
                if _conteudo(ws) >= 3:
                    t = " ".join(ws)
                    return (t[0].upper() + t[1:])[:70]
    for f in re.split(r"(?<=[.!?…])\s+", texto):
        ws = _limpa_titulo(f.split()[:10])
        if _conteudo(ws) >= 3:
            t = " ".join(ws)
            return (t[0].upper() + t[1:])[:70]
    return "Momento da live"


# --- alinhamento -----------------------------------------------------------------------------
def energia_db(audio):
    n = int(JAN * SR)
    q = audio[: len(audio) // n * n].reshape(-1, n)
    return 20 * np.log10(np.sqrt((q ** 2).mean(1)) + 1e-9)


# palavras que não fecham frase (artigo, preposição, conjunção) e conectores que não abrem frase
SOLTAS = set(_norm(x) for x in """a o as os um uma uns umas de do da dos das em no na nos nas pra pro para por pelo pela
com sem e ou mas que se como porque pq quando onde tipo então aí daí né também já até nem""".split())
CONECTORES = set(_norm(x) for x in "e ou mas que então aí daí porque pq tipo né também nem".split())


def _lim(w):
    return re.sub(r"[^\w]", "", _norm(w))


def _terminal(w):
    t = w.rstrip("\"”')»")
    if t.endswith("..") or t.endswith("…"):
        return False
    return t.endswith((".", "!", "?"))


DURACAO_MONETIZACAO_S = 61.0       # corte "de mais de 1 minuto" (monetização): nunca abaixo de 1:01
DURACAO_MAX_MONETIZACAO_S = 90.0


ESCADA_PISOS = (30, 45, 61)         # degraus do 'mínimo' escolhido: 30 -> 45 -> 61 s
TETO_PARTE_S = 90.0                 # cada parte de um corte dividido


def modo_piso(cfg):
    """Mínimo escolhido: 0 (Livre), 30, 45 ou 61 s. 'duracao_minima_modo' ("livre"|"30"|"45"|"61"; vem do painel) manda;
    sem ele vale o antigo 'cortes_longos' (true = 61 s)."""
    m = cfg.get("duracao_minima_modo")
    if m is not None:
        m = str(m).strip().lower()
        if m in ("livre", "0", ""):
            return 0
        if m in ("30", "45", "61", "60", "1min"):
            return 61 if m in ("60", "1min") else int(m)
    return int(DURACAO_MONETIZACAO_S) if cfg.get("cortes_longos") else 0


def piso_s(cfg):
    """Piso do corte que está sendo escolhido agora (degrau da escada); 0 = livre."""
    return float(cfg["piso_s"]) if cfg.get("piso_s") is not None else float(modo_piso(cfg))


def cortes_longos(cfg):
    return piso_s(cfg) > 0


def duracao_minima(cfg):
    """Corte enxuto: pode ficar abaixo de duracao_min quando só há silêncio para completar (60% do mínimo).
    Com um mínimo escolhido (30/45/61 s) o piso é fixo: trecho menor é descartado, nunca esticado."""
    if cortes_longos(cfg):
        return piso_s(cfg)
    return cfg.get("duracao_piso_frac", 0.6) * cfg["duracao_min"]


def ajustar_cortes_longos(cfg):
    """Com um mínimo escolhido, abre espaço para sustentá-lo: olha mais para trás/frente do pico. Livre: cfg intacto."""
    p = piso_s(cfg)
    if p <= 0:
        return cfg
    cfg["duracao_min"] = max(cfg["duracao_min"], p)
    cfg["duracao_max"] = duracao_maxima(cfg)
    cfg["antes_max"] = max(cfg.get("antes_max", 18.0), 30.0 if p >= 45 else 24.0)
    cfg["depois_do_momento"] = max(cfg["depois_do_momento"], 0.65 * p)
    cfg["depois_min"] = max(cfg.get("depois_min", 7.0), 12.0 if p >= 45 else 9.0)
    return cfg


def duracao_maxima(cfg):
    """Teto do corte: o configurado; com mínimo escolhido e teto < mínimo vira 90 s (ou mínimo + 15 s)."""
    dmax = cfg["duracao_max"]
    p = piso_s(cfg)
    if p > 0 and dmax < p:
        return max(dmax, DURACAO_MAX_MONETIZACAO_S if p >= DURACAO_MONETIZACAO_S else p + 15.0)
    return dmax


def espera_fala_s(cfg):
    """Quanto de fala depois do pico o seletor espera antes de desistir de um momento (0 = decide na hora: Livre)."""
    m = modo_piso(cfg)
    return m + 6.0 if m > 0 else 0.0


def cfg_degrau(cfg, t):
    """Cópia do cfg para tentar o degrau t da escada: corte de t até (próximo degrau - 0,5 s); o último vai a 90 s."""
    prox = [x for x in ESCADA_PISOS if x > t]
    c = dict(cfg, piso_s=float(t), duracao_min=float(t), duracao_max=(prox[0] - 0.5) if prox else DURACAO_MAX_MONETIZACAO_S)
    c["antes_max"] = max(cfg.get("antes_max", 18.0), 30.0 if t >= 45 else 24.0)
    c["depois_do_momento"] = max(cfg["depois_do_momento"], 0.65 * t)
    c["depois_min"] = max(cfg.get("depois_min", 7.0), 12.0 if t >= 45 else 9.0)
    return c


def dividir_em_partes(t, palavras, fim_disponivel, cfg, min_ini=-1e9, max_fim=1e9):
    """Contexto maior que 1 minuto e meio: DUAS partes, cada uma começando e terminando em frase completa, cada uma
    com pelo menos o mínimo escolhido (ou 30 s) e no máximo 90 s. -> janela com 'partes' [(ini, fim, lim_ini, lim_fim)] ou None."""
    mp = max(float(modo_piso(cfg)), 30.0)
    cs = dict(cfg, piso_s=91.0, duracao_min=91.0, duracao_max=2 * TETO_PARTE_S,
              antes_max=max(cfg.get("antes_max", 18.0), 30.0), depois_do_momento=max(cfg["depois_do_momento"], 100.0),
              depois_min=max(cfg.get("depois_min", 7.0), 30.0))
    j = escolher_janela(t, palavras, fim_disponivel, cs, min_ini=min_ini, max_fim=max_fim)
    if j.get("recusado"):
        return None
    ini, fim = j["ini"], j["fim"]
    P = [w for w in palavras if w["s"] >= ini - 0.05 and w["e"] <= fim + 0.05]
    melhor = None
    for k in range(len(P) - 1):
        a, b = P[k], P[k + 1]
        gap = b["s"] - a["e"]
        if not (_terminal(a["w"]) or gap >= 0.6) or gap < 0.15:
            continue
        if _lim(a["w"]) in SOLTAS or a["w"].rstrip().endswith(","):
            continue
        m = min(0.3, gap * 0.4)
        f1, i2 = a["e"] + m, b["s"] - m
        l1, l2 = f1 - ini, fim - i2
        if not (mp <= l1 <= TETO_PARTE_S and mp <= l2 <= TETO_PARTE_S):
            continue
        sc = abs(l1 - l2) - (3.0 if _terminal(a["w"]) else 0.0)
        if melhor is None or sc < melhor[0]:
            mm = min(0.08, gap / 3)
            melhor = (sc, f1, i2, (a["e"] + mm, min(b["s"] - mm, a["e"] + 0.5)))
    if melhor is None:
        return None
    _, f1, i2, lim_mid = melhor
    out = dict(j)
    out["partes"] = [
        {"ini": ini, "fim": f1, "lim_ini": j["lim_ini"], "lim_fim": lim_mid},
        {"ini": i2, "fim": fim, "lim_ini": lim_mid, "lim_fim": j["lim_fim"]},
    ]
    return out


def _score_ini(a, b, gap, t, hook, alvo=8.0):
    sc = 0.0
    term = a is None or _terminal(a["w"])
    sc += 3.5 if term else 0.0
    sc += 1.5 if gap >= 0.5 else 0.0
    sc += 0.8 * min(gap, 1.2)
    if not term:
        if _lim(a["w"]) in SOLTAS or a["w"].rstrip().endswith(","):
            sc -= 3.5
        if _lim(b["w"]) in CONECTORES:
            sc -= 2.5
        if b["w"][:1].islower() and gap < 0.6:
            sc -= 2.0
    if _lim(b["w"]) in SOLTAS:   # abrir em artigo/preposição solto ('uma', 'até') soa cortado
        sc -= 1.5
    if b["w"][:1].islower() is False and b["w"][:1].isalpha():
        sc += 0.8
    if gap < 0.15:
        sc -= 6.0
    sc += hook * 1.5
    sc -= 0.08 * abs((t - b["s"]) - alvo)
    return sc


def _score_fim(a, b, gap, t, alvo):
    term = _terminal(a["w"])
    sc = 3.5 if term else 0.0
    if gap >= 0.5:
        sc += 2.0
    sc += 0.8 * min(gap, 1.2)
    if not term:
        if _lim(a["w"]) in SOLTAS:
            sc -= 4.0
        if a["w"].rstrip().endswith(","):
            sc -= 2.5
    if gap < 0.15:
        sc -= 6.0
    # Fechamento pós-reação: se já fechou frase e há pausa/fim, prioriza fechar limpo sem cauda morta
    if term and (b is None or gap >= 0.8):
        sc += 2.5
    else:
        sc -= 0.08 * abs((a["e"] - t) - alvo)
    return sc


def escolher_janela(t, palavras, fim_disponivel, cfg, min_ini=-1e9, max_fim=1e9):
    """Janela do corte em volta do evento t. Início numa frase que abre (pergunta/exclamação/pico), fim numa frase que
    fecha; nunca recua além de um buraco mudo; fim logo depois da reação (sem cauda muda). Os pontos de corte caem
    DENTRO da pausa entre palavras. -> dict(ini, fim, lim_ini, lim_fim, buraco_frac) (lim_* = intervalo de silêncio)."""
    dmin, dmax = cfg["duracao_min"], duracao_maxima(cfg)
    longo = cortes_longos(cfg)
    amax, amin = cfg.get("antes_max", 18.0), cfg.get("antes_min", 6.0)
    depois = cfg["depois_do_momento"]
    dep_min = cfg.get("depois_min", 7.0)
    bur, bur_pos = cfg.get("buraco_max_s", 2.8), cfg.get("cauda_max_s", 2.0)
    soft = duracao_minima(cfg)
    # corte de 30/45/61 s: pausa de jogo (quem joga fica uns segundos calado) não corta o contexto de antes do pico;
    # o início mira ~30% do mínimo antes do pico (61 s -> ~18 s de contexto) em vez de 8 s
    bur_ini = max(bur, cfg.get("buraco_contexto_s", 6.0)) if longo else bur
    alvo_antes = max(8.0, 0.3 * soft) if longo else 8.0
    P = [w for w in palavras if w["e"] > t - 60 and w["s"] < t + max(60.0, depois + 12.0)]
    hi = min(fim_disponivel, max_fim)

    # --- limite para trás: onde começa a fala contínua que leva ao evento
    antes = [w for w in P if w["s"] <= t + 1.0]
    piso = max(t - amax, min_ini, 0.0)
    if antes:
        i = len(antes) - 1
        while i > 0 and antes[i]["s"] - antes[i - 1]["e"] < bur_ini:
            i -= 1
        piso = max(piso, antes[i]["s"] - 0.5)

    amin_cand = amin
    cands_pre = [b for b in P if piso - 0.3 <= b["s"] <= t - amin]
    if not cands_pre and antes:
        amin_cand = max(1.8, min(amin, t - antes[-1]["s"]))

    cand = []
    for k, b in enumerate(P):
        a = P[k - 1] if k else None
        gap = b["s"] - a["e"] if a else 9.0
        if not (piso - 0.3 <= b["s"] <= t - amin_cand):
            continue
        if a is not None and gap >= 9.0:
            a = None
        hook = 0.0
        if any(("?" in w["w"] or "!" in w["w"]) for w in P[k:k + 8] if w["s"] < b["s"] + 3.0):
            hook = 1.0
        cand.append((_score_ini(a, b, gap, t, hook, alvo_antes), a, b, gap))
    ini, lim_ini = None, None
    cand = [c for c in cand if c[2]["s"] - min(0.3, c[3] * 0.4) >= min_ini]
    if cand:
        sc, a, b, gap = max(cand, key=lambda c: c[0])
        ini = b["s"] - min(0.25, gap * 0.4)
        m = min(0.08, gap / 3)
        lim_ini = (max(a["e"] + m, b["s"] - 0.4), b["s"] - m) if a is not None and gap < 9.0 else (b["s"] - 0.4, b["s"] - 0.08)
    else:
        if antes:
            ini = max(piso, antes[0]["s"] - 0.4, min_ini)
        else:
            ini = max(piso, t - 6.0, min_ini)
        lim_ini = None
    ini = max(ini, min_ini, 0.0)

    # Gancho imediato nos primeiros 2s: elimina silêncio inicial morto > 0.8s antes da primeira fala
    primeira_pal = next((w for w in P if w["s"] >= ini - 0.1), None)
    if primeira_pal and (primeira_pal["s"] - ini) > 0.8:
        ini = max(min_ini, primeira_pal["s"] - 0.35)

    if ini > t - 1.5:       # sem lugar para abrir o corte antes do pico (colado no corte anterior/emenda)
        return {"ini": ini, "fim": ini, "lim_ini": None, "lim_fim": None, "buraco_frac": 1.0, "recusado": True}

    # --- fim: depois da reação, em frase que fecha, sem passar de uma cauda muda
    depois_p = [w for w in P if w["s"] >= t - 1.0]
    teto = t + depois + 6.0
    prev_fim = None
    for j, w in enumerate(depois_p):
        if prev_fim is not None and w["s"] - prev_fim >= bur_pos and prev_fim >= t + dep_min:
            teto = min(teto, prev_fim + 0.3)
            break
        prev_fim = w["e"]
    if longo:        # procura frase que fecha logo depois dos 61 s (a cauda muda continua limitando o teto acima)
        teto = max(teto, ini + soft + 12.0)
    teto = min(teto, ini + dmax - 0.3, hi - 0.3 if hi < 1e8 else teto)
    candf = []
    for k, a in enumerate(P):
        b = P[k + 1] if k + 1 < len(P) else None
        if not (t + dep_min <= a["e"] <= teto):
            continue
        gap = (b["s"] - a["e"]) if b else 9.0
        candf.append((_score_fim(a, b, gap, t, depois), a, b, gap))
    if not candf and not longo:     # nada fecha depois da reação mínima: aceita frase que fecha logo depois do pico
        for k, a in enumerate(P):
            b = P[k + 1] if k + 1 < len(P) else None
            if t + 2.5 <= a["e"] <= teto and _terminal(a["w"]):
                gap = (b["s"] - a["e"]) if b else 9.0
                candf.append((_score_fim(a, b, gap, t, depois), a, b, gap))
    longos = [c for c in candf if c[1]["e"] - ini >= soft]
    if longo and not longos:        # nenhuma frase que fecha sustenta 61 s: descarta (nunca estica)
        return {"ini": ini, "fim": ini, "lim_ini": None, "lim_fim": None, "buraco_frac": 1.0, "recusado": True}
    candf = longos if longo else (candf or [])
    if candf:
        sc, a, b, gap = max(candf, key=lambda c: c[0])
        fim = a["e"] + min(0.4, gap * 0.4)
        m = min(0.08, gap / 3)
        lim_fim = (a["e"] + m, min(b["s"] - m, a["e"] + 0.5)) if b is not None else (a["e"] + 0.08, a["e"] + 0.5)
    else:
        fim, lim_fim = min(teto, t + depois), None
    fim = min(fim, hi, ini + dmax)

    # Eliminação de cauda morta: se fim - ini < soft e NÃO é modo longo:
    # em vez de esticar com silêncio artificial (ini + soft), tenta recuar ini em fala anterior
    # ou fecha limpo na piada sem adicionar silêncio morto!
    if fim - ini < soft:
        if longo:
            return {"ini": ini, "fim": ini, "lim_ini": None, "lim_fim": None, "buraco_frac": 1.0, "recusado": True}
        falas_antes = [w for w in P if min_ini <= w["s"] < ini]
        if falas_antes and (ini - falas_antes[-1]["e"]) < bur:
            ini_cand = max(min_ini, fim - soft)
            p_cand = next((w for w in P if w["s"] >= ini_cand - 0.1), None)
            if p_cand:
                ini = max(min_ini, p_cand["s"] - 0.35)
        # Não empurrar fim com silêncio vazio!

    # fração do corte em buracos mudos (>= 3 s sem palavra)
    pal = sorted((max(w["s"], ini), min(w["e"], fim)) for w in P if w["e"] > ini and w["s"] < fim)
    mudo, cur = 0.0, ini
    for s0, e0 in pal + [(fim, fim)]:
        if s0 - cur >= 3.0:
            mudo += s0 - cur
        cur = max(cur, e0)
    return {"ini": ini, "fim": fim, "lim_ini": lim_ini, "lim_fim": lim_fim,
            "buraco_frac": mudo / max(fim - ini, 1e-6)}


def janela_corte(t_evento, palavras, fim_disponivel, cfg):
    """Compatibilidade: só (ini, fim)."""
    j = escolher_janela(t_evento, palavras, fim_disponivel, cfg)
    return j["ini"], j["fim"]


def _banda_voz(x):
    """Filtra 300-3400 Hz (FFT): o vale de silêncio ignora o ronco constante de jogo/motor."""
    if len(x) < 64:
        return x
    f = np.fft.rfft(x)
    fr = np.fft.rfftfreq(len(x), 1.0 / SR)
    f[(fr < 300) | (fr > 3400)] = 0
    return np.fft.irfft(f, len(x))


def ajustar_no_vale(t, audio, a0, raio=0.3, limites=None):
    """Move t para o ponto de menor energia (média de 30 ms) dentro de ±raio — 'corte só em silêncio'.
    Com 'limites' (intervalo de silêncio entre duas palavras) a busca fica ESTRITAMENTE dentro dele, na banda da voz:
    nunca entra numa palavra."""
    if limites is not None:
        lo, hi = limites
        if hi - lo < 0.06:
            return min(max(t, lo), hi) if hi >= lo else t
        i0, i1 = max(0, int((lo - a0) * SR)), min(len(audio), int((hi - a0) * SR))
        if i1 - i0 < int(0.03 * SR):
            return min(max(t, lo), hi)
        x = _banda_voz(audio[i0:i1].astype(np.float64))
        n = int(JAN * SR)
        q = x[: len(x) // n * n].reshape(-1, n)
        db = 20 * np.log10(np.sqrt((q ** 2).mean(1)) + 1e-9)
        sm = np.convolve(db, np.ones(3) / 3, "same") if len(db) >= 3 else db
        # entre os pontos mais silenciosos, o mais perto do alvo t
        lim_db = sm.min() + 1.5
        cands = [k for k in range(len(sm)) if sm[k] <= lim_db]
        tt = lo + (min(cands, key=lambda k: abs(lo + (k + 0.5) * JAN - t)) + 0.5) * JAN
        return float(min(max(tt, lo), hi))
    db = energia_db(audio)
    if not len(db):
        return t
    i0, i1 = max(1, int((t - raio - a0) / JAN)), min(len(db) - 2, int((t + raio - a0) / JAN))
    if i1 <= i0:
        return t
    sm = np.convolve(db, np.ones(3) / 3, "same")
    return a0 + (i0 + int(np.argmin(sm[i0:i1 + 1]))) * JAN


# --- seleção do momento (clusters) e deduplicação ---------------------------------------------
class Seletor:
    """Escolhe os picos a cortar. Segundos acima de 0,75*limiar separados por até 12 s formam UM momento; o evento é o
    máximo do momento (média de 4 s), só decidido quando o momento fecha (nada acima do corte nos 12 s seguintes).
    Distância entre cortes medida em ini (novo) contra fim (anterior)."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.avaliado_ate = 0.0
        self.ultimo_fim = -1e9
        self.ultima_nota = 0.0

    def proximo_pico(self, notas, a0, limite, notas_ate, limiar, final=False):
        """-> None | ("adiar", s_inicio) | ("pico", s, nota, fim_cluster). 'notas_ate' = último segundo com nota."""
        alto, junta = 0.75 * limiar, self.cfg.get("cluster_junta_s", 12)
        s = int(a0)
        while s < int(limite):
            if s in notas and notas[s][0] >= limiar:
                break
            s += 1
        else:
            return None
        last, t = s, s
        while t < last + junta:
            t += 1
            if t > notas_ate:
                if final:
                    break
                return ("adiar", s)          # momento ainda aberto: espera chegar mais dados
            if t in notas and notas[t][0] >= alto:
                last = t
            if last - s >= 30:
                break
        cl = [k for k in range(s, last + 1) if k in notas]
        # o pico real pode estar antes do primeiro acima do limiar? não: parte do 1º segundo >= limiar

        def media4(k):
            v = [notas[j][0] for j in range(k - 2, k + 2) if j in notas]
            return sum(v) / len(v) if v else 0.0
        pk = max(cl, key=lambda k: (media4(k), notas[k][0]))
        return ("pico", pk, max(notas[k][0] for k in cl), last)

    def planejar(self, pk_s, nota, sinais, palavras, disponivel, emendas, final=False, pendente=None):
        """Janela do corte para o pico. -> (janela|None, motivo_recusa|None, substitui_pendente(bool))."""
        cfg = self.cfg
        if final and disponivel - pk_s < cfg.get("pos_pico_final_s", 6.0):
            return None, "pico colado no fim da live", False
        gap_min = cfg["intervalo_min_entre_cortes"]
        min_ini = self.ultimo_fim + gap_min
        substitui = False
        if pendente is not None and nota >= pendente[0] + cfg.get("salto_climax", 1.2):
            min_ini, substitui = pendente[1] + gap_min, True      # clímax muito maior: troca o pré-pico na fila
        max_fim = 1e9
        for e in emendas:        # não atravessar emenda de reconexão (o conteúdo da live pula ali)
            if pk_s - 60 < e < pk_s + 40:
                if e <= pk_s:
                    min_ini = max(min_ini, e + 0.3)
                else:
                    max_fim = min(max_fim, e - 0.3)
        M = modo_piso(cfg)
        if M > 0 and cfg.get("piso_s") is None:
            # escada: tenta o mínimo escolhido; se o contexto completo não cabe, sobe 30 -> 45 -> 61 s; se nem 61 s
            # comporta, divide em duas partes
            j = None
            for t in [x for x in ESCADA_PISOS if x >= M]:
                ct = cfg_degrau(cfg, t)
                jt = escolher_janela(pk_s, palavras, disponivel, ct, min_ini=min_ini, max_fim=max_fim)
                if not (jt.get("recusado") or jt["ini"] < min_ini - 1e-6 or jt["fim"] - jt["ini"] < duracao_minima(ct)):
                    j = dict(jt, degrau=t)
                    break
            if j is None:
                j = dividir_em_partes(pk_s, palavras, disponivel, cfg, min_ini=min_ini, max_fim=max_fim)
                if j is None or j["ini"] < min_ini - 1e-6:
                    return None, "perto demais do corte anterior ou curto demais", False
        else:
            j = escolher_janela(pk_s, palavras, disponivel, cfg, min_ini=min_ini, max_fim=max_fim)
            if j.get("recusado") or j["ini"] < min_ini - 1e-6 or j["fim"] - j["ini"] < duracao_minima(cfg):
                return None, "perto demais do corte anterior ou curto demais", False
        if j["buraco_frac"] > cfg.get("buraco_frac_max", 0.35) and sinais.get("volume", 0) < 1.5:
            return None, "cheio de silêncio sem pico de volume", False
        return j, None, substitui

    def passo(self, notas, palavras, disponivel, limite, notas_ate, limiar, emendas, aceitar, pendente=None,
              final=False, juiz=None):
        """Um passo de avaliação. 'aceitar(decisao)->bool' faz o resto (áudio, fila); se aceitar, vira o último corte.
        'pendente()' -> (nota, ultimo_fim_antes) do corte ainda esperando render, ou None."""
        cur = max(self.avaliado_ate, getattr(self, "bloqueado_ate", -1e9) + 1)
        while True:
            r = self.proximo_pico(notas, cur, limite, notas_ate, limiar, final)
            if r is None:
                self.avaliado_ate = max(self.avaliado_ate, limite)
                return
            if r[0] == "adiar":
                self.avaliado_ate = max(self.avaliado_ate, r[1])
                return
            _, pk, nota, fim_cl = r
            inicio_busca, bloqueado_antes = cur, getattr(self, "bloqueado_ate", -1e9)
            self.bloqueado_ate = fim_cl
            cur = fim_cl + 1
            mot, sinais = notas[pk][1], notas[pk][2]
            if juiz is not None:
                nota, sinais = juiz(pk, nota, palavras, sinais)
                if nota < limiar:
                    continue
            j, porque, subst = self.planejar(pk, nota, sinais, palavras, disponivel, emendas, final,
                                             pendente() if pendente else None)
            if j is None:
                if not final and disponivel < pk + espera_fala_s(self.cfg):
                    # o mínimo (30/45/61 s) ainda não cabe só porque a fala de depois do pico não chegou:
                    # o momento volta a ser avaliado no próximo passo, em vez de ser perdido
                    self.bloqueado_ate = bloqueado_antes
                    self.avaliado_ate = max(self.avaliado_ate, inicio_busca)
                    return
                continue
            dec = {"pico": pk, "nota": nota, "mot": mot, "sinais": sinais, "janela": j, "substitui": subst}
            if aceitar(dec):
                self.ultimo_fim, self.ultima_nota = dec.get("fim", j["fim"]), nota
