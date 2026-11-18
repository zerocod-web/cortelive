"""Título, legenda e hashtags de cada corte, por rede (YouTube Shorts, TikTok, Instagram Reels).

A geração usa a transcrição no próprio computador:
  heurística local = a fala exata do pico (limpa, curta, sem repetição colada) como gancho, o tipo do
  momento (risada, susto, briga, polícia, clutch, ação, reação) escolhendo um modelo de título curto e
  variado, canal e jogo só com evidência, e nunca o mesmo título duas vezes (olha a fila já gerada).
"""
from __future__ import annotations

import json
import re
import unicodedata
import zlib
from pathlib import Path

# limites das redes (deixamos folga)
YT_TITULO_MAX = 100          # YouTube: 100 caracteres no título
YT_DESC_MAX = 5000
YT_TAGS_MAX = 500            # soma dos caracteres das tags
TIKTOK_MAX = 2200            # TikTok: 2200 na legenda; "curta" = até 150 de texto
TIKTOK_TEXTO_MAX = 150
TIKTOK_HASHTAGS_MAX = 6
IG_MAX = 2200
IG_HASHTAGS_MAX = 5

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

TIPOS = {  # tipo -> (palavras na fala/motivos, hashtags do tipo, contexto neutro p/ descrição)
    "risada": (["kkk", "haha", "hahaha", "risada", "rindo", "rachei", "morri de rir"], ["humor", "engracado"],
               "momento de risada"),
    "susto": (["socorro", "susto", "assustei", "meu deus", "ai meu deus", "mds", "que medo", "jumpscare"],
              ["susto"], "momento de susto"),
    "policia": (["polícia", "viatura", "preso", "prender", "prendeu", "algema", "fuga", "fugir",
                 "perseguição", "dp", "tático"], ["policia"], "cena com a polícia"),
    "briga": (["filho da puta", "filha da puta", "tóxico", "merda", "vai se fuder", "cala a boca", "lixo", "burro",
               "idiota", "otário", "briga", "treta", "soco", "porrada", "não faz nada", "arrombado",
               "desgraçado", "ridículo"], ["treta"], "momento de treta"),
    "clutch": (["clutch", "ace", "1v1", "1v2", "1v3", "1x1", "1x2", "1x3", "sozinho", "último vivo", "plantar",
                "plantou", "defusar", "defusou", "bomba"], ["clutch"], "lance de clutch"),
    "acao": (["morreu", "matou", "matei", "morri", "tiro", "bala", "headshot", "explodiu", "atira", "atirou",
              "mata", "abateu"], ["gameplay"], "lance da partida"),
    "reacao": (["não acredito", "que isso", "olha isso", "caralho", "pqp", "nossa", "vish",
                "eita", "rapaz", "misericórdia", "que loucura"], [], "reação ao vivo"),
}

# modelos de título do YouTube por tipo: {g} = fala exata, {n} = canal, {o} = "Canal no Jogo".
# Curtos, de gente que posta corte; só afirmam o que o tipo já provou (2+ evidências na fala).
MODELOS_YT = {
    "risada": ['"{g}" kkkk - {o}', '{n} rindo: "{g}"', '"{g}" - {o}'],
    "susto": ['"{g}" - {n} no susto', '{n} assustou: "{g}"', '"{g}" - {o}'],
    "policia": ['"{g}" - {n} x polícia', '{n} e a polícia: "{g}"', '"{g}" - {o}'],
    "briga": ['{n} pistola: "{g}"', '"{g}" - {n} na treta', '"{g}" - {o}'],
    "clutch": ['"{g}" - {n} no clutch', '{n} no clutch: "{g}"', '"{g}" - {o}'],
    "acao": ['"{g}" - {o}', '{n} na partida: "{g}"', '{n}: "{g}"'],
    "reacao": ['"{g}" - {o}', '{n}: "{g}"', '{n} ao vivo: "{g}"'],
}
MODELOS_IG = ["{o}, corte da live.", "Direto da live: {o}.", "{o} ao vivo."]


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
    """Tipo só com evidência: risada escrita/detectada, ou 2+ palavras DIFERENTES do tipo na fala
    (uma palavra solta, tipo "polícia" numa conversa, não vira "fuga da polícia")."""
    # dos motivos só entram a risada e o que foi FALADO ("falou 'meu deus'"); "chat explodiu" não é explosão
    mot = " ".join(m for m in (motivos or []) if "risada" in str(m).lower())
    mot += " " + " ".join(re.findall(r"'([^']+)'", " ".join(str(m) for m in (motivos or []))))
    cru = sem_acento((texto or "").lower()) + " " + sem_acento(mot.lower())
    alvo = " " + re.sub(r"\s+", " ", re.sub(r"[^\w ]", " ", cru)) + " "
    if "risada" in alvo or re.search(r"\b(k{3,}|(ha){2,}h?)\b", alvo):
        return "risada"
    pontos = {tipo: sum(1 for p in {sem_acento(p) for p in pal} if f" {p} " in alvo)
              for tipo, (pal, _, _) in TIPOS.items()}
    melhor = max(pontos, key=pontos.get)
    return melhor if pontos[melhor] >= 2 else "reacao"


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


_NAO_FECHA = set("a o as os um uma e de do da dos das em no na nos nas pra pro para por com sem que".split())


def _n(w: str) -> str:
    return re.sub(r"[^\w]", "", sem_acento(w.lower()))


def _arruma_frase(f: str) -> str:
    ws = f.split()
    # repetição colada de 1 a 4 palavras ("vou entrar escada vou entrar escada" -> uma vez);
    # se só a segunda cópia fecha a frase, é ela que fica ("escada, vou entrar escada." -> "escada.")
    for n in (4, 3, 2, 1):
        i = 0
        while i + 2 * n <= len(ws):
            if [_n(w) for w in ws[i:i + n]] == [_n(w) for w in ws[i + n:i + 2 * n]]:
                if ws[i + 2 * n - 1][-1:] in ".!?" and ws[i + n - 1][-1:] not in ".!?":
                    del ws[i:i + n]
                else:
                    del ws[i + n:i + 2 * n]
            else:
                i += 1
    # duas frases emendadas sem ponto ("A coisa tá feíssima Seu soco"): fica a primeira, se tiver corpo.
    # Nome próprio depois de preposição ou vírgula ("pro Bahia", "Caralho, O cara") não é frase nova.
    for k in range(2, len(ws)):
        w, ant = ws[k], ws[k - 1]
        if ant.endswith(","):
            continue
        if w[:1].isupper() and w not in ("Eu", "Deus") and not w.isupper() and len(ws[:k]) >= 3 \
                and _n(ant) not in _NAO_FECHA:
            ws = ws[:k]
            break
    for k in range(1, len(ws)):                      # "Caralho, O cara" -> "Caralho, o cara"
        if ws[k - 1].endswith(",") and len(_n(ws[k])) <= 2 and ws[k][:1].isupper() and _n(ws[k]) != "eu":
            ws[k] = ws[k].lower()
    t = " ".join(ws).strip(" ,;:.-—")
    return (t[:1].upper() + t[1:]) if t else ""


def _fala_exata(frase: str, texto: str, max_palavras: int = 11) -> str:
    """Acha a frase na transcrição e devolve a fala como foi dita (com vírgula, ! e ?), completando o
    começo/fim da frase quando falta pouco ("Pro Bahia filha da puta!" -> "E você foi pro Bahia, filha
    da puta!"). Não achou: devolve a frase como veio."""
    def sem_rep(seq):
        out = []
        for x in seq:
            if x and (not out or out[-1] != x):
                out.append(x)
        return out
    alvo = sem_rep([_n(w) for w in (frase or "").split()])
    orig = (texto or "").split()
    idx = []
    for i, w in enumerate(orig):
        n = _n(w)
        if n and not (idx and _n(orig[idx[-1]]) == n):
            idx.append(i)
    seq = [_n(orig[i]) for i in idx]
    j = next((j for j in range(len(seq) - len(alvo) + 1) if seq[j:j + len(alvo)] == alvo), None) if alvo else None
    if j is None:
        return frase
    a, b = idx[j], idx[j + len(alvo) - 1]
    fim = ".!?…"
    if not orig[a][:1].isupper() and not (a > 0 and orig[a - 1][-1:] in fim):   # começou no meio da frase
        k = a
        for _ in range(4):
            if k == 0 or orig[k - 1][-1:] in fim:
                break
            k -= 1
            if orig[k][:1].isupper() or k == 0 or orig[k - 1][-1:] in fim:
                if b - k + 1 <= max_palavras:
                    a = k
                break
    e = b
    for _ in range(2):                                    # fecha a frase se faltam 1-2 palavras
        if orig[e][-1:] in fim or e + 1 >= len(orig):
            break
        e += 1
    if orig[e][-1:] in fim and e - a + 1 <= max_palavras:
        b = e
    return " ".join(orig[a:b + 1])


def _outras_frases(texto: str, fora: str) -> list[str]:
    """Outras falas do corte que servem de gancho (para não repetir título): 3 a 9 palavras, com conteúdo."""
    out = []
    for f in re.split(r"(?<=[.!?…])\s+", texto or ""):
        f = _arruma_frase(f)
        if 3 <= len(f.split()) <= 9 and len(_palavras_conteudo(f)) >= 2 and _n(f) != _n(fora) and f not in out:
            out.append(f)
    return sorted(out, key=lambda f: (("!" in f) + ("?" in f), len(_palavras_conteudo(f))), reverse=True)


def _chave(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", sem_acento(str(t).lower())).strip()


def titulos_da_fila(cfg: dict) -> set[str]:
    """Títulos do YouTube já gerados nesta pasta de cortes — para nunca repetir."""
    pasta = cfg.get("pasta_saida")
    if not pasta:
        return set()
    try:
        d = json.loads((Path(pasta) / "_autopost" / "fila.json").read_text(encoding="utf-8"))
        itens = d.get("itens", d) if isinstance(d, dict) else {}
        out = set()
        for it in (itens.values() if isinstance(itens, dict) else itens):
            out.add(_chave(((it.get("metadados") or {}).get("youtube") or {}).get("titulo", "")))
        return out - {""}
    except Exception:
        return set()


# ------------------------------------------------------------------ montagem por rede
def _template(cfg: dict) -> dict:
    return {
        "prefixo": str(cfg.get("titulo_prefixo", "") or ""),
        "sufixo": str(cfg.get("titulo_sufixo", "") or ""),
        "fixas": list(cfg.get("hashtags_fixas", []) or []),
        "rodape": str(cfg.get("descricao_rodape", "") or ""),
    }


def _titulo_yt(gancho, nome, jogo_nome, tipo, pre, fim, usados):
    """Título do YouTube: modelo do tipo (variado pelo próprio gancho), dentro do limite e ainda não usado.
    -> título, ou None se todas as variações já foram usadas."""
    onde = (f"{nome} no {jogo_nome}" if jogo_nome else nome) if nome else jogo_nome
    espaco = YT_TITULO_MAX - len(pre) - len(fim)
    modelos = list(MODELOS_YT.get(tipo, MODELOS_YT["reacao"]))
    if modelos:
        k = zlib.crc32(_chave(gancho).encode()) % len(modelos)
        modelos = modelos[k:] + modelos[:k]
    modelos += ['"{g}" - {o}', '"{g}" - {n}', '"{g}"']
    cands = []
    for m in modelos:
        if ("{n}" in m and not nome) or ("{o}" in m and not onde):
            continue
        corpo = m.format(g=gancho, n=nome, o=onde)
        if len(corpo) <= espaco and corpo not in cands:
            cands.append(corpo)
    if not cands:
        cands = [f'"{_corta(gancho, max(10, espaco - 2))}"']
    for corpo in cands:
        t = (pre + corpo + fim)[:YT_TITULO_MAX]
        if _chave(t) not in usados:
            return t
    return None


def montar(gancho: str, canal: str, jogo, tipo: str, cfg: dict, usados=(), alternativas=()) -> dict:
    """Aplica o modelo de título, os limites e as hashtags de cada rede.

    Usa outras falas do mesmo corte quando o título já existe em usados.
    """
    usados = {_chave(u) for u in (usados or ())}
    t = _template(cfg)
    nome = cfg.get("canal_nome") or nome_bonito(canal)
    jogo_nome, jogo_tag = (jogo if jogo else ("", ""))
    if nome and jogo_tag and slug_hashtag(nome) == jogo_tag:     # canal "gta_rp" + jogo GTA RP: não repete
        nome = ""
    suave = suavizar if cfg.get("suavizar_palavrao", True) else (lambda x: x)
    tags_tipo = TIPOS.get(tipo, TIPOS["reacao"])[1]
    contexto = TIPOS.get(tipo, TIPOS["reacao"])[2]

    def limpo(g):
        return _limpa_emoji(suave(g).strip().strip('"“”'), 1)
    gancho = limpo(gancho) or (f"{nome} ao vivo" if nome else "Corte da live")

    # ---- YouTube
    onde = (f"{nome} no {jogo_nome}" if jogo_nome else nome) if nome else jogo_nome
    sufixo_yt = (" " + t["sufixo"].strip()) if t["sufixo"].strip() else ""
    fim = f"{sufixo_yt} #shorts"
    pre = (t["prefixo"].strip() + " ") if t["prefixo"].strip() else ""
    yt_titulo = None
    for g in [gancho] + [limpo(x) for x in alternativas]:
        yt_titulo = _titulo_yt(g, nome, jogo_nome, tipo, pre, fim, usados)
        if yt_titulo:
            gancho = g
            break
    n = 2
    while not yt_titulo:                             # último recurso: numera ("... (2)")
        yt_titulo = _titulo_yt(f"{_corta(gancho, 60)} ({n})", nome, jogo_nome, tipo, pre, fim, usados)
        n += 1

    base_tags = [canal, nome, jogo_tag, "cortes", f"cortes {nome}" if nome else "", "live"] + tags_tipo + t["fixas"]
    yt_hash = _hashtags([nome or canal, jogo_tag, "cortes"] + t["fixas"] + tags_tipo[:1] + ["shorts"], 6)
    desc = (
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
    tk_texto = _corta(gancho, TIKTOK_TEXTO_MAX)
    tk_hash = _hashtags([nome or canal, jogo_tag, "cortes"] + tags_tipo + t["fixas"] + ["fyp"], TIKTOK_HASHTAGS_MAX)
    tiktok = (tk_texto + " " + " ".join(tk_hash)).strip()[:TIKTOK_MAX]

    # ---- Instagram: legenda + até 5 hashtags
    rodape_ig = MODELOS_IG[zlib.crc32(_chave(gancho).encode()) % len(MODELOS_IG)].format(o=onde)
    ig_texto = f"{gancho}\n\n{rodape_ig}" if onde else gancho
    ig_texto = _limpa_emoji(ig_texto, 2).strip()
    ig_hash = _hashtags([nome or canal, jogo_tag, "cortes"] + tags_tipo + t["fixas"], IG_HASHTAGS_MAX)
    instagram = (ig_texto + "\n\n" + " ".join(ig_hash)).strip()[:IG_MAX]

    return {
        "gancho": gancho,
        "youtube": {"titulo": yt_titulo, "descricao": desc, "tags": tags},
        "tiktok": {"titulo": tiktok, "legenda": tiktok},
        "instagram": {"titulo": _corta(ig_texto.split("\n")[0], 100), "legenda": instagram},
    }


# ------------------------------------------------------------------ entrada principal
def gerar(transcricao: str, canal: str, cfg: dict | None = None, motivos=(), palavras=None, t_evento=None,
          jogo: str = "", frase_sugerida: str = "", usados=None) -> dict:
    """-> {gancho, youtube{titulo,descricao,tags}, tiktok{titulo,legenda}, instagram{titulo,legenda},
           fonte, tipo, jogo, obs}
    `usados`: títulos já gerados; None = lê a fila da pasta de cortes (cfg["pasta_saida"])."""
    cfg = dict(cfg or {})
    texto = (transcricao or "").strip()
    jg = adivinhar_jogo(canal, texto, jogo or cfg.get("jogo", ""))
    tipo = tipo_do_momento(texto, motivos)
    # frase do pico: com as palavras e o tempo (sessão) acha sozinha; sem elas, usa a que o corte marcou
    if palavras or not frase_sugerida:
        crua = frase_forte(texto, motivos, palavras, t_evento, cfg.get("palavras_quentes", ()))
    else:
        crua = re.sub(r"\s+", " ", frase_sugerida).strip(" -\"") if frase_sugerida != "Momento da live" else ""
    # a fala como foi dita (pontuação e começo/fim da frase), limpa e curta
    frase = _corta(_arruma_frase(_fala_exata(crua, texto)), 70) if crua else ""
    while frase and _n(frase.split()[-1]) in _NAO_FECHA and len(frase.split()) > 2:
        frase = frase.rsplit(" ", 1)[0].rstrip(" ,;:")
    if len(frase.split()) < 2:
        frase = _arruma_frase(crua) if crua else ""
    nome = cfg.get("canal_nome") or nome_bonito(canal)
    gancho = frase or (f"{nome} reagindo ao vivo" if nome else "Corte da live")
    if usados is None:
        usados = titulos_da_fila(cfg)
    r = montar(gancho, canal, jg, tipo, cfg, usados, _outras_frases(texto, gancho))
    r.update({"fonte": "heuristica", "tipo": tipo, "jogo": jg[0] if jg else None, "obs": ""})
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
