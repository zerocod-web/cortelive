"""Título, legenda e hashtags de cada corte, por rede (YouTube Shorts, TikTok, Instagram Reels).

Camadas, sem chave de API nenhuma:
  (a) Ollama local, se estiver rodando (http://127.0.0.1:11434) — escreve o gancho e as legendas;
  (b) senão (ou se a resposta vier ruim), heurística local: a frase mais forte da fala perto do
      momento, o tipo do momento (risada, susto, morte, polícia, reação) e um template simples.
O resultado sempre passa pelos mesmos limites e pelo mesmo filtro de "cara de IA".
"""
from __future__ import annotations

import json
import re
import unicodedata

# limites das redes (deixamos folga)
YT_TITULO_MAX = 100          # YouTube: 100 caracteres no título
YT_DESC_MAX = 5000
YT_TAGS_MAX = 500            # soma dos caracteres das tags
TIKTOK_MAX = 2200            # TikTok: 2200 na legenda; "curta" = até 150 de texto
TIKTOK_TEXTO_MAX = 150
TIKTOK_HASHTAGS_MAX = 6
IG_MAX = 2200
IG_HASHTAGS_MAX = 5

# frases de "robô de marketing" que entregam texto de IA (e quase sempre prometem demais)
CARA_DE_IA = [
    "você não vai acreditar", "voce nao vai acreditar", "não vai acreditar", "inacreditável", "inacreditavel",
    "incrível", "incrivel", "épico", "epico", "prepare-se", "simplesmente", "confira", "neste vídeo",
    "nesse vídeo", "neste video", "nesse video", "momento épico", "imperdível", "imperdivel", "surreal",
    "insano", "chocante", "no final", "até o final", "ate o final", "espere até", "olha o que aconteceu",
    "o que aconteceu a seguir", "jornada", "mergulhe", "descubra",
]

JOGOS = [  # (padrão no nome do canal/arquivo ou na fala, nome bonito, hashtag)
    (r"\bgta\s*rp\b|gta_rp|gtarp|\brp\b.*\bcidade\b", "GTA RP", "gtarp"),
    (r"\bgta\b", "GTA", "gta"),
    (r"\bvalorant\b|\bvava\b", "Valorant", "valorant"),
    (r"\bcs2\b|counter[- ]?strike|\bcs\s?go\b", "CS2", "cs2"),
    (r"\bminecraft\b", "Minecraft", "minecraft"),
    (r"\bfortnite\b", "Fortnite", "fortnite"),
    (r"\bfree ?fire\b", "Free Fire", "freefire"),
    (r"\bleague of legends\b|\blolzinho\b", "LoL", "leagueoflegends"),
]

TIPOS = {  # tipo -> (palavras na fala/motivos, hashtags do tipo, contexto curto p/ descrição)
    "risada": (["kkk", "haha", "hahaha", "risada", "rindo", "rachei", "morri de rir"], ["humor", "engracado"],
               "chorou de rir"),
    "susto": (["socorro", "susto", "assustei", "meu deus", "ai meu deus", "mds", "que medo", "jumpscare"],
              ["susto"], "levou um susto"),
    "policia": (["polícia", "policia", "viatura", "preso", "prender", "algema", "fuga", "fugir", "perseguição",
                 "perseguicao", "dp", "tático", "tatico"], ["perseguicao"], "fuga da polícia"),
    "acao": (["morreu", "matou", "tiro", "bala", "headshot", "explodiu", "atira", "atirou", "mata", "clutch",
              "ace", "abateu"], [], "lance da partida"),
    "reacao": (["não acredito", "nao acredito", "que isso", "olha isso", "caralho", "pqp", "nossa", "vish",
                "eita", "rapaz", "misericórdia", "misericordia"], [], "reação ao vivo"),
}


# ------------------------------------------------------------------ util
def sem_acento(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", t) if not unicodedata.combining(c))


def slug_hashtag(t: str) -> str:
    """'GTA RP' -> 'gtarp'; 'Alanzoka' -> 'alanzoka'. Só letras e números, sem acento."""
    return re.sub(r"[^a-z0-9]", "", sem_acento(str(t)).lower())


def nome_bonito(canal: str) -> str:
    c = re.sub(r"[_\-]+", " ", str(canal or "")).strip()
    return " ".join(p[:1].upper() + p[1:] for p in c.split()) if c else ""


PALAVROES = {"porra": "p*rra", "caralho": "c*ralho", "puta": "p*ta", "foda": "f*da", "fodase": "f*da-se",
             "merda": "m*rda", "cacete": "c*cete", "buceta": "b*ceta", "pqp": "pqp", "fdp": "fdp"}


def suavizar(t: str) -> str:
    """Palavrão no título derruba alcance/monetização; troca uma letra por * (continua legível)."""
    def troca(m):
        w = m.group(0)
        s = PALAVROES.get(sem_acento(w.lower()).replace("-", ""))
        if not s:
            return w
        return s.upper() if w.isupper() else (s[:1].upper() + s[1:] if w[:1].isupper() else s)
    return re.sub(r"\b[\w-]+\b", troca, t)


def _tem_cara_de_ia(t: str) -> bool:
    n = t.lower()
    return any(f in n for f in CARA_DE_IA)


def _limpa_emoji(t: str, maximo=1) -> str:
    out, n = [], 0
    for ch in t:
        if unicodedata.category(ch) == "So" or 0x1F000 <= ord(ch) <= 0x1FAFF:
            n += 1
            if n > maximo:
                continue
        out.append(ch)
    return "".join(out)


def _palavras_conteudo(t: str) -> set[str]:
    from ..momento import VAZIAS
    ws = re.findall(r"\w+", sem_acento(t.lower()))
    return {w for w in ws if len(w) >= 4 and w not in {sem_acento(v) for v in VAZIAS}}


def _corta(t: str, n: int) -> str:
    """Corta em até n caracteres sem partir palavra."""
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) <= n:
        return t
    c = t[:n + 1].rsplit(" ", 1)[0].rstrip(" ,;:-—|")
    return c if c else t[:n]


def _hashtags(lista, maximo) -> list[str]:
    vistos, out = set(), []
    for h in lista:
        s = slug_hashtag(str(h).lstrip("#"))
        if s and s not in vistos:
            vistos.add(s)
            out.append("#" + s)
        if len(out) >= maximo:
            break
    return out


# ------------------------------------------------------------------ análise do trecho
def adivinhar_jogo(canal: str, texto: str, jogo_cfg: str = "") -> tuple[str, str] | None:
    """Jogo só quando há evidência (config, nome do canal/arquivo ou a fala). Sem evidência: nenhum —
    melhor não citar jogo do que prometer o errado."""
    if jogo_cfg:
        return jogo_cfg, slug_hashtag(jogo_cfg)
    alvo = f"{canal or ''} {texto or ''}".lower()
    for pad, nome, tag in JOGOS:
        if re.search(pad, alvo):
            return nome, tag
    return None


def tipo_do_momento(texto: str, motivos=()) -> str:
    alvo = " " + sem_acento((texto or "").lower()) + " " + sem_acento(" ".join(motivos or []).lower()) + " "
    if "risada" in alvo or re.search(r"\b(k{3,}|(ha){2,}h?)\b", alvo):
        return "risada"
    pontos = {}
    for tipo, (pal, _, _) in TIPOS.items():
        pontos[tipo] = sum(alvo.count(" " + sem_acento(p) + " ") + alvo.count(" " + sem_acento(p) + "!")
                           for p in pal)
    melhor = max(pontos, key=pontos.get)
    return melhor if pontos[melhor] > 0 else "reacao"


def frase_forte(texto: str, motivos=(), palavras=None, t_evento=None, quentes=()) -> str:
    """A frase que o streamer falou no pico (reaproveita o seletor do corte)."""
    from ..momento import titulo_heuristico
    if palavras:
        # palavras podem vir em {w,s,e} (pipeline) ou {w,start,end} (relativas)
        pal = [{"w": w["w"], "s": w.get("s", w.get("start", 0.0)), "e": w.get("e", w.get("end", 0.0))}
               for w in palavras if w.get("w")]
    else:
        pal = None
    f = titulo_heuristico(texto or "", list(motivos or []), pal, t_evento, quentes)
    f = re.sub(r"\s+", " ", f).strip(" -—")
    if not f or f == "Momento da live":
        return ""
    return _arruma_frase(f)


def _arruma_frase(f: str) -> str:
    ws = f.split()
    # repetição colada de 1 a 4 palavras ("vou entrar escada vou entrar escada" -> uma vez)
    for n in (4, 3, 2, 1):
        i = 0
        while i + 2 * n <= len(ws):
            if [sem_acento(w.lower()).strip("!?") for w in ws[i:i + n]] == \
                    [sem_acento(w.lower()).strip("!?") for w in ws[i + n:i + 2 * n]]:
                del ws[i + n:i + 2 * n]
            else:
                i += 1
    # duas frases emendadas sem ponto ("A coisa tá feíssima Seu soco"): fica a primeira, se tiver corpo
    for k in range(2, len(ws)):
        w = ws[k]
        if w[:1].isupper() and w not in ("Eu", "Deus") and not w.isupper() and len(ws[:k]) >= 3:
            ws = ws[:k]
            break
    return " ".join(ws)


# ------------------------------------------------------------------ montagem por rede
def _template(cfg: dict) -> dict:
    return {
        "prefixo": str(cfg.get("titulo_prefixo", "") or ""),
        "sufixo": str(cfg.get("titulo_sufixo", "") or ""),
        "fixas": list(cfg.get("hashtags_fixas", []) or []),
        "rodape": str(cfg.get("descricao_rodape", "") or ""),
    }


def montar(gancho: str, canal: str, jogo, tipo: str, cfg: dict, textos: dict | None = None) -> dict:
    """Aplica template, limites e hashtags. `textos` (do Ollama) pode trazer descricao/tiktok/instagram."""
    textos = textos or {}
    t = _template(cfg)
    nome = cfg.get("canal_nome") or nome_bonito(canal)
    jogo_nome, jogo_tag = (jogo if jogo else ("", ""))
    if nome and jogo_tag and slug_hashtag(nome) == jogo_tag:     # canal "gta_rp" + jogo GTA RP: não repete
        nome = ""
    if cfg.get("suavizar_palavrao", True):
        gancho = suavizar(gancho)
        textos = {k: suavizar(v) for k, v in textos.items()}
    tags_tipo = TIPOS.get(tipo, TIPOS["reacao"])[1]
    contexto = TIPOS.get(tipo, TIPOS["reacao"])[2]
    gancho = _limpa_emoji(gancho.strip().strip('"“”'), 1) or (f"{nome} ao vivo" if nome else "Corte da live")

    # ---- YouTube
    onde = (f"{nome} no {jogo_nome}" if jogo_nome else nome) if nome else jogo_nome
    sufixo_yt = (" " + t["sufixo"].strip()) if t["sufixo"].strip() else ""
    fim = f"{sufixo_yt} #shorts"
    pre = (t["prefixo"].strip() + " ") if t["prefixo"].strip() else ""
    corpo = f"\"{gancho}\"" + (f" - {onde}" if onde else "")
    espaco = YT_TITULO_MAX - len(pre) - len(fim)
    if len(corpo) > espaco:                       # primeiro sai o "no jogo", depois encurta a frase
        corpo = f"\"{gancho}\"" + (f" - {nome}" if nome else "")
    if len(corpo) > espaco:
        g = _corta(gancho, max(10, espaco - (len(nome) + 5 if nome else 2)))
        corpo = f"\"{g}\"" + (f" - {nome}" if nome and len(g) + len(nome) + 5 <= espaco else "")
    yt_titulo = (pre + corpo + fim)[:YT_TITULO_MAX]

    base_tags = [canal, nome, jogo_tag, "cortes", f"cortes {nome}" if nome else "", "live"] + tags_tipo + t["fixas"]
    yt_hash = _hashtags([nome or canal, jogo_tag, "cortes"] + t["fixas"], 4)
    desc = textos.get("descricao") or (
        f"{onde or 'Ao vivo'}: \"{gancho}\".\n\nCorte da live" + (f" do {nome}" if nome else "") +
        f" ({contexto}).")
    desc = desc.strip()
    if t["rodape"]:
        desc += "\n\n" + t["rodape"].strip()
    desc = (desc + "\n\n" + " ".join(yt_hash)).strip()[:YT_DESC_MAX]
    tags, soma = [], 0
    for tg in base_tags:
        tg = re.sub(r"\s+", " ", str(tg).lstrip("#")).strip().lower()
        if tg and tg not in tags and soma + len(tg) + 1 <= YT_TAGS_MAX:
            tags.append(tg)
            soma += len(tg) + 1

    # ---- TikTok: legenda curta + hashtags
    tk_texto = _corta(textos.get("tiktok") or gancho, TIKTOK_TEXTO_MAX)
    tk_hash = _hashtags([nome or canal, jogo_tag, "cortes"] + tags_tipo + t["fixas"] + ["fyp"], TIKTOK_HASHTAGS_MAX)
    tiktok = (tk_texto + " " + " ".join(tk_hash)).strip()[:TIKTOK_MAX]

    # ---- Instagram: legenda + até 5 hashtags
    ig_texto = textos.get("instagram") or (f"{gancho}\n\n{onde}, corte da live." if onde else gancho)
    ig_texto = _limpa_emoji(ig_texto, 2).strip()
    ig_hash = _hashtags([nome or canal, jogo_tag, "cortes"] + tags_tipo + t["fixas"], IG_HASHTAGS_MAX)
    instagram = (ig_texto + "\n\n" + " ".join(ig_hash)).strip()[:IG_MAX]

    return {
        "gancho": gancho,
        "youtube": {"titulo": yt_titulo, "descricao": desc, "tags": tags},
        "tiktok": {"titulo": tiktok, "legenda": tiktok},
        "instagram": {"titulo": _corta(ig_texto.split("\n")[0], 100), "legenda": instagram},
    }


# ------------------------------------------------------------------ Ollama
PROMPT_OLLAMA = """Você escreve a legenda de cortes de live (Shorts, Reels, TikTok) para um canal brasileiro de cortes.
Streamer: {nome}. Jogo: {jogo}. Tipo do momento: {tipo}.
Fala transcrita do corte (pode ter erro de transcrição):
\"\"\"{texto}\"\"\"
Frase mais forte detectada: "{frase}"

Regras:
- Português do Brasil falado, como gente que posta corte de live escreve. Nada de tom de propaganda.
- O gancho é curto (até 60 caracteres) e usa o que o streamer DISSE ou o que de fato acontece na fala acima.
- Não invente acontecimento que não está na fala. Não prometa final, reviravolta ou "você não vai acreditar".
- Proibido: incrível, épico, insano, surreal, chocante, confira, neste vídeo, até o final, prepare-se.
- No máximo 1 emoji no total. Sem hashtags (elas são adicionadas depois).

Responda só JSON:
{{"gancho": "...", "descricao": "1 ou 2 frases para o YouTube", "tiktok": "legenda de até 120 caracteres", "instagram": "legenda de 1 a 2 frases"}}"""


def ollama_ativo(url: str, timeout=2.0) -> str | None:
    """-> nome do modelo a usar, ou None se o Ollama não estiver rodando/sem modelo."""
    try:
        import requests
        r = requests.get(url.rstrip("/") + "/api/tags", timeout=timeout)
        nomes = [m["name"] for m in r.json().get("models", [])]
        return nomes[0] if nomes else None
    except Exception:
        return None


def _via_ollama(cfg, nome, jogo_nome, tipo, texto, frase):
    url = str(cfg.get("ollama_url", "http://127.0.0.1:11434")).rstrip("/")
    modelo = cfg.get("ollama_modelo") or ollama_ativo(url)
    if not modelo:
        return None, "Ollama não encontrado"
    import requests
    prompt = PROMPT_OLLAMA.format(nome=nome or "streamer", jogo=jogo_nome or "não informado", tipo=tipo,
                                  texto=(texto or "")[:1800], frase=frase or "")
    try:
        r = requests.post(url + "/api/generate", timeout=float(cfg.get("autopost_ollama_timeout_s", 60)), json={
            "model": modelo, "prompt": prompt, "format": "json", "stream": False,
            "options": {"temperature": 0.5}})
        d = json.loads(r.json()["response"])
    except Exception as e:
        return None, f"Ollama falhou ({str(e)[:120]})"
    vocab = _palavras_conteudo(texto or "")
    out = {}
    for k in ("gancho", "descricao", "tiktok", "instagram"):
        v = _limpa_emoji(re.sub(r"#\w+", "", str(d.get(k, "") or "")).strip(), 1)
        v = re.sub(r"\s+", " ", v).strip(' "“”')
        if not v or _tem_cara_de_ia(v):
            continue
        out[k] = v
    g = out.get("gancho", "")
    # gancho precisa ter pé na fala (senão é invenção) e caber
    if not g or len(g) > 80 or (vocab and not (_palavras_conteudo(g) & vocab)):
        out.pop("gancho", None)
    if not out.get("gancho"):
        return None, f"Ollama ({modelo}) deu gancho fora da fala ou com cara de IA; usei a heurística"
    return {**out, "_modelo": modelo}, None


# ------------------------------------------------------------------ entrada principal
def gerar(transcricao: str, canal: str, cfg: dict | None = None, motivos=(), palavras=None, t_evento=None,
          jogo: str = "", usar_ollama: bool | None = None, frase_sugerida: str = "") -> dict:
    """-> {gancho, youtube{titulo,descricao,tags}, tiktok{titulo,legenda}, instagram{titulo,legenda},
           fonte, tipo, jogo, obs}"""
    cfg = dict(cfg or {})
    texto = (transcricao or "").strip()
    jg = adivinhar_jogo(canal, texto, jogo or cfg.get("jogo", ""))
    tipo = tipo_do_momento(texto, motivos)
    # frase do pico: com as palavras e o tempo (sessão) acha sozinha; sem elas, usa a que o corte marcou
    if palavras or not frase_sugerida:
        frase = frase_forte(texto, motivos, palavras, t_evento, cfg.get("palavras_quentes", ()))
    else:
        frase = _arruma_frase(re.sub(r"\s+", " ", frase_sugerida).strip(" -\"")) if frase_sugerida != "Momento da live" else ""
    nome = cfg.get("canal_nome") or nome_bonito(canal)
    if usar_ollama is None:
        usar_ollama = bool(cfg.get("autopost_ollama", True))
    fonte, obs, textos = "heuristica", "", {}
    if usar_ollama:
        d, err = _via_ollama(cfg, nome, jg[0] if jg else "", tipo, texto, frase)
        if d:
            fonte = f"ollama:{d.pop('_modelo')}"
            textos = d
        else:
            obs = err or ""
    gancho = textos.get("gancho") or frase or (f"{nome} reagindo ao vivo" if nome else "Corte da live")
    if fonte == "heuristica":
        textos = {}
    r = montar(gancho, canal, jg, tipo, cfg, textos)
    r.update({"fonte": fonte, "tipo": tipo, "jogo": jg[0] if jg else None, "obs": obs})
    return r


def aplicar_edicao(meta: dict, titulo=None, descricao=None, tiktok=None, instagram=None, tags=None) -> dict:
    """Edição manual do painel: o texto do usuário vale como está, só respeitando os limites."""
    m = json.loads(json.dumps(meta))
    if titulo is not None:
        t = titulo.strip()
        if "#shorts" not in t.lower():
            t = _corta(t, YT_TITULO_MAX - 8) + " #shorts"
        m["youtube"]["titulo"] = t[:YT_TITULO_MAX]
    if descricao is not None:
        m["youtube"]["descricao"] = descricao.strip()[:YT_DESC_MAX]
    if tags is not None:
        m["youtube"]["tags"] = [x.strip() for x in tags if x.strip()]
    if tiktok is not None:
        m["tiktok"]["legenda"] = m["tiktok"]["titulo"] = tiktok.strip()[:TIKTOK_MAX]
    if instagram is not None:
        m["instagram"]["legenda"] = instagram.strip()[:IG_MAX]
        m["instagram"]["titulo"] = _corta(instagram.strip().split("\n")[0], 100)
    m["editado"] = True
    return m
