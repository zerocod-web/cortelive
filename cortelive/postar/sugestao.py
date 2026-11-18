"""Sugestao local e gratuita de titulo, descricao e hashtags a partir da transcricao do corte."""
from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any

STOPWORDS_PT = {
    "a", "ao", "aos", "aquela", "aquele", "aqui", "as", "assim", "ate", "ai", "ali",
    "bem", "bom", "boa", "cara", "coisa", "com", "como", "da", "das", "de", "dela",
    "dele", "deles", "depois", "do", "dos", "e", "ela", "elas", "ele", "eles", "em",
    "entao", "era", "essa", "esse", "esta", "estao", "estar", "este", "eu", "fazer",
    "feito", "foi", "for", "isso", "isto", "ja", "la", "lhe", "mais", "mas", "me",
    "mesmo", "meu", "minha", "muito", "na", "nao", "nas", "nem", "no", "nos", "nossa",
    "nosso", "num", "numa", "o", "os", "ou", "para", "pela", "pelas", "pelo", "pelos",
    "pode", "por", "porque", "pra", "pro", "qual", "quando", "que", "quem", "se",
    "sem", "ser", "seu", "seus", "so", "sua", "suas", "ta", "tambem", "te", "tem",
    "tendo", "ter", "teu", "teve", "tinha", "to", "toda", "todas", "todo", "todos",
    "tu", "tudo", "um", "uma", "umas", "uns", "vai", "vamos", "veio", "ver", "vez",
    "voce", "voces", "vou", "ne", "tipo", "mano", "velho",
}

PALAVRAS_BLOQUEADAS_TAG = {
    "caralho", "porra", "puta", "pqp", "merda", "foda", "cacete", "arrombado",
}

TEMAS_HASHTAGS: dict[str, tuple[str, ...]] = {
    "gameplay": ("jogo", "jogando", "partida", "round", "boss", "mapa", "kill", "matei", "morreu", "player", "clutch"),
    "gtarp": ("policia", "viatura", "cidade", "fuga", "assalto", "rp", "gta"),
    "fps": ("tiro", "headshot", "mira", "granada", "bala", "arma", "sniper"),
    "engracado": ("risada", "kkk", "haha", "engracado", "rindo", "socorro", "impossivel"),
    "react": ("video", "assistindo", "reagindo", "olha", "chat"),
}

PALAVRAS_IMPACTO = {
    "impossivel", "acredito", "socorro", "olha", "morreu", "matei", "matou",
    "ganhei", "ganhamos", "perdi", "absurdo", "insano", "feissima", "mentira",
    "nossa", "eita", "corre", "pegou", "cuidado", "agora", "nunca",
}


def _sem_acentos(texto: str) -> str:
    norm = unicodedata.normalize("NFKD", texto.lower())
    return "".join(c for c in norm if not unicodedata.combining(c))


def _limpar_ass_linha(linha: str) -> str:
    sem_tags = re.sub(r"\{[^}]*\}", "", linha)
    sem_quebra = sem_tags.replace("\\N", " ").replace("\\n", " ")
    return re.sub(r"\s+", " ", sem_quebra).strip()


def extrair_transcricao_ass(caminho_ass: Path) -> str:
    """Extrai texto legivel de um arquivo .ass (deduplicando repeticoes de karaoke)."""
    if not caminho_ass.is_file():
        return ""
    frases: list[str] = []
    anterior_norm = ""
    for linha in caminho_ass.read_text(encoding="utf-8", errors="replace").splitlines():
        if not linha.startswith("Dialogue:"):
            continue
        partes = linha.split(",", 9)
        if len(partes) < 10:
            continue
        limpa = _limpar_ass_linha(partes[9])
        if not limpa:
            continue
        norm = _sem_acentos(limpa)
        if norm == anterior_norm:
            continue
        anterior_norm = norm
        frases.append(limpa.capitalize())
    return ". ".join(frases)


def ler_contexto_corte(caminho_video: Path | str) -> dict[str, Any]:
    """Localiza metadados do corte: <sessão>/_interno/<nome>.json/.ass e sessao.json (formato novo) ou ao
    lado do .mp4 (formato antigo corte_*.json). Ver cortelive/pastas.py."""
    from .. import pastas
    arq = Path(caminho_video).resolve()
    pasta = pastas.pasta_sessao_do_video(arq)

    corte_json = pastas.meta_do_video(arq)
    corte_ass = pastas.ass_do_video(arq)
    sessao_json = pastas.sessao_json_do_video(arq)

    dados_corte: dict[str, Any] = {}
    if corte_json is not None:
        try:
            dados_corte = json.loads(corte_json.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            dados_corte = {}

    dados_sessao: dict[str, Any] = {}
    if sessao_json.is_file():
        try:
            dados_sessao = json.loads(sessao_json.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            dados_sessao = {}

    transcricao = str(dados_corte.get("transcricao") or "").strip()
    if not transcricao and corte_ass is not None:
        transcricao = extrair_transcricao_ass(corte_ass)

    canal = str(dados_sessao.get("canal") or "").strip()
    if not canal:
        # Ex.: "2026-11-17 16h28 - alanzoka" ou 2026-11-17_1628_alanzoka -> alanzoka
        c = pastas.canal_da_pasta(pasta.name)
        if c != pasta.name:
            canal = c

    return {
        "video": str(arq),
        "transcricao": transcricao,
        "titulo_bruto": str(dados_corte.get("titulo_sugerido") or "").strip(),
        "motivos": list(dados_corte.get("motivos") or []),
        "duracao": dados_corte.get("duracao"),
        "canal": canal,
        "fonte": str(dados_sessao.get("fonte") or "").strip(),
    }


def _escolher_melhor_frase(transcricao: str) -> str:
    sentencas = [
        s.strip(" -–—,;:.")
        for s in re.split(r"(?<=[.!?])\s+|\n+", transcricao)
        if s.strip(" -–—,;:.")
    ]
    if not sentencas:
        return ""

    melhor = sentencas[0]
    melhor_nota = -1.0
    for idx, frase in enumerate(sentencas):
        palavras = re.findall(r"\w+", _sem_acentos(frase))
        if not palavras:
            continue
        nota = 0.0
        if 4 <= len(palavras) <= 13:
            nota += 2.5
        elif 2 <= len(palavras) <= 18:
            nota += 1.0
        if "!" in frase or "?" in frase:
            nota += 1.2
        nota += sum(1.1 for p in palavras if p in PALAVRAS_IMPACTO)
        # Leve preferencia pelas frases iniciais/centrais do clipe
        if idx <= 2:
            nota += 0.4
        if nota > melhor_nota:
            melhor_nota = nota
            melhor = frase

    return melhor


def _truncar_limpo(texto: str, limite: int) -> str:
    texto = re.sub(r"\s+", " ", texto).strip()
    if len(texto) <= limite:
        return texto
    cortado = texto[: max(1, limite - 1)].rsplit(" ", 1)[0].rstrip(" ,;:-")
    return (cortado or texto[: limite - 1]) + "…"


def gerar_hashtags(transcricao: str, canal: str = "", motivos: list[str] | None = None, max_tags: int = 7) -> list[str]:
    """Gera hashtags locais relevantes sem servicos externos."""
    tags: list[str] = ["#cortes", "#live", "#shorts"]
    vistas = {t.lower() for t in tags}

    def _add(tag_crua: str) -> None:
        limpa = re.sub(r"[^a-z0-9_]", "", _sem_acentos(tag_crua))
        if len(limpa) < 3 or limpa in PALAVRAS_BLOQUEADAS_TAG:
            return
        fmt = f"#{limpa}"
        if fmt not in vistas and len(tags) < max_tags:
            vistas.add(fmt)
            tags.append(fmt)

    if canal:
        _add(canal)

    texto_busca = _sem_acentos(transcricao)
    tokens = re.findall(r"[a-z0-9]{3,}", texto_busca)
    conjunto_tokens = set(tokens)

    for tema, gatilhos in TEMAS_HASHTAGS.items():
        if any(g in conjunto_tokens or g in texto_busca for g in gatilhos):
            _add(tema)

    contagem = Counter(
        t for t in tokens
        if len(t) >= 5 and t not in STOPWORDS_PT and t not in PALAVRAS_BLOQUEADAS_TAG and not t.isdigit()
    )
    for palavra, _freq in contagem.most_common(4):
        _add(palavra)

    return tags[:max_tags]


def sugerir_metadados(
    caminho_video: Path | str,
    transcricao_manual: str | None = None,
    canal_manual: str | None = None,
) -> dict[str, Any]:
    """Gera titulo, descricao e hashtags locais a partir da fala do corte."""
    ctx = ler_contexto_corte(caminho_video)
    transcricao = (transcricao_manual if transcricao_manual is not None else ctx["transcricao"]).strip()
    canal = (canal_manual if canal_manual is not None else ctx["canal"]).strip()

    frase = _escolher_melhor_frase(transcricao)
    if not frase and ctx["titulo_bruto"]:
        frase = ctx["titulo_bruto"]
    if not frase:
        nome_limpo = Path(caminho_video).stem.replace("_", " ").strip()
        frase = f"Momento da live ({nome_limpo})" if not canal else f"Momento da live de {canal}"

    frase = frase[0].upper() + frase[1:] if frase else "Momento da live"
    titulo = _truncar_limpo(frase, 88)

    hashtags = gerar_hashtags(transcricao, canal=canal, motivos=ctx["motivos"])
    linha_tags = " ".join(hashtags)

    linhas_desc: list[str] = []
    if transcricao:
        resumo = _truncar_limpo(transcricao, 220)
        linhas_desc.append(f"\"{resumo}\"")
    if canal:
        if ctx["fonte"].startswith("http"):
            linhas_desc.append(f"Clipe da live de {canal} ({ctx['fonte']}).")
        else:
            linhas_desc.append(f"Clipe da live de {canal}.")
    linhas_desc.append(linha_tags)
    descricao = "\n\n".join(linhas_desc).strip()

    return {
        "titulo": titulo,
        "descricao": descricao,
        "hashtags": hashtags,
        "canal": canal or None,
        "transcricao": transcricao,
    }
