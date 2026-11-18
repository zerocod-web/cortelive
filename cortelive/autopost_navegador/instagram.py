"""Instagram Reels pelo instagram.com (vídeo enviado pelo site vira Reel)."""
from __future__ import annotations

import re

from .base import (ErroPostagem, ParouAntesDePublicar, SemSessao, limpar_e_digitar, pausa_humana, rx,
                   tentar)

URL_LOGIN = "https://www.instagram.com/"

SEL_CRIAR = ('svg[aria-label="New post"], svg[aria-label="Nova publicação"], '
             'svg[aria-label="Criar"], svg[aria-label="Create"]')
SEL_ARQUIVO = 'div[role="dialog"] input[type="file"], form[enctype="multipart/form-data"] input[type="file"]'
SEL_CORTE = 'svg[aria-label="Select crop"], svg[aria-label="Selecionar corte"]'
SEL_LEGENDA = 'div[role="dialog"] div[contenteditable="true"]'


def logado(page) -> bool:
    page.goto(URL_LOGIN, wait_until="domcontentloaded")
    page.wait_for_load_state("load")
    if "/accounts/login" in page.url:
        return False
    try:
        page.locator(SEL_CRIAR).first.wait_for(state="attached", timeout=15_000)
        return True
    except Exception:
        return False


def _opcional(locator, timeout=4_000) -> bool:
    try:
        locator.first.wait_for(state="visible", timeout=timeout)
        pausa_humana(0.3, 0.8)
        locator.first.click()
        return True
    except Exception:
        return False


def _botao(page, *nomes):
    return page.locator('div[role="dialog"]').get_by_role("button", name=re.compile(
        "^(" + "|".join(nomes) + ")$", re.I))


def _usuario(page) -> str:
    try:
        href = page.get_by_role("link", name=rx("^perfil$", "^profile$")).first.get_attribute("href", timeout=8_000)
        return (href or "").strip("/").split("/")[0]
    except Exception:
        return ""


def enviar(s, video: str, titulo: str, descricao: str, privado: bool, ate_antes: bool) -> str:
    page = s.page
    s.emitir("abrindo", 2)
    if not logado(page):
        s.print("sem_sessao")
        raise SemSessao("Instagram sem login neste perfil: rode `conectar instagram`.")
    if privado:
        s.emitir("aviso", 3, aviso="o Instagram não tem Reel privado pelo site; vai publicar normal")
    _opcional(page.get_by_role("button", name=rx("^agora não$", "^not now$")), 3_000)
    usuario = _usuario(page)

    pausa_humana()
    tentar(lambda: page.locator(SEL_CRIAR).first.click(), nome="botão Criar")
    _opcional(page.get_by_role("link", name=rx("^post$", "^publicação$", "^postar$")), 3_000)
    entrada = page.locator(SEL_ARQUIVO).first
    entrada.wait_for(state="attached", timeout=30_000)
    pausa_humana()
    entrada.set_input_files(video)
    s.emitir("enviando", 15)

    # aviso "vídeos agora são compartilhados como reels"
    _opcional(page.get_by_role("button", name=rx("^ok$")), 8_000)
    # mantém o 9:16 do corte (o padrão do site é cortar para quadrado)
    if _opcional(page.locator(SEL_CORTE), 15_000):
        _opcional(page.get_by_text(re.compile("^original$", re.I)), 5_000)
    s.print("recorte")

    for etapa in ("recorte", "edição"):
        pausa_humana()
        tentar(lambda: _botao(page, "Next", "Avançar").first.click(), nome=f"Avançar ({etapa})")

    legenda = page.locator(SEL_LEGENDA).first
    legenda.wait_for(state="visible", timeout=30_000)
    texto = descricao.strip() or titulo.strip()
    if titulo.strip() and titulo.strip() not in texto:
        texto = f"{titulo.strip()}\n\n{texto}"
    pausa_humana()
    tentar(lambda: limpar_e_digitar(page, legenda, texto[:2200]), nome="legenda")
    compartilhar = _botao(page, "Share", "Compartilhar").first
    compartilhar.wait_for(state="visible", timeout=30_000)
    s.emitir("pronto_para_publicar", 90)
    prova = s.print("antes_de_publicar")

    if ate_antes:
        raise ParouAntesDePublicar({"print": prova, "aviso": "nada foi publicado no Instagram"})

    pausa_humana()
    tentar(lambda: compartilhar.click(), nome="botão Compartilhar")
    s.emitir("publicando", 93)
    try:
        page.get_by_text(rx("reel foi compartilhado", "reel has been shared", "foi compartilhad",
                            "has been shared")).first.wait_for(state="visible", timeout=10 * 60_000)
    except Exception:
        s.print("sem_confirmacao")
        raise ErroPostagem("cliquei em Compartilhar mas o Instagram não confirmou; confira no perfil")
    s.print("publicado")

    if not usuario:
        raise ErroPostagem("publicado, mas não achei o nome do perfil para pegar o link")
    page.goto(f"https://www.instagram.com/{usuario}/reels/", wait_until="domcontentloaded")
    try:
        a = page.locator('a[href*="/reel/"]').first
        a.wait_for(state="attached", timeout=30_000)
        return "https://www.instagram.com" + a.get_attribute("href").split("?")[0]
    except Exception:
        return f"https://www.instagram.com/{usuario}/reels/"
