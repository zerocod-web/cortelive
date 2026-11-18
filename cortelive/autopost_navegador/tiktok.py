"""TikTok pelo TikTok Studio (tiktok.com/tiktokstudio/upload)."""
from __future__ import annotations

import re
import time

from .base import (ErroPostagem, ParouAntesDePublicar, SemSessao, limpar_e_digitar, pausa_humana, rx,
                   tentar)

URL_LOGIN = "https://www.tiktok.com/login"
URL_UPLOAD = "https://www.tiktok.com/tiktokstudio/upload?from=webapp"
URL_CONTEUDO = "https://www.tiktok.com/tiktokstudio/content"

SEL_ARQUIVO = 'input[type="file"][accept*="video"]'
SEL_LEGENDA = 'div.public-DraftEditor-content[contenteditable="true"], div[contenteditable="true"]'
SEL_POSTAR = 'button[data-e2e="post_video_button"]'


def logado(page) -> bool:
    page.goto(URL_UPLOAD, wait_until="domcontentloaded")
    page.wait_for_load_state("load")
    if "/login" in page.url:
        return False
    try:
        page.locator(SEL_ARQUIVO).first.wait_for(state="attached", timeout=20_000)
        return True
    except Exception:
        return "/login" not in page.url and page.locator('a[href*="/login"]').count() == 0


def _habilitado(loc) -> bool:
    try:
        return loc.is_enabled() and (loc.get_attribute("aria-disabled") or "false") != "true" \
            and (loc.get_attribute("data-disabled") or "false") != "true"
    except Exception:
        return False


def _somente_eu(s, page) -> None:
    """Troca 'Quem pode assistir' para 'Só você'. Se não conseguir, ABORTA (nunca publica público
    por engano quando o pedido era privado)."""
    def trocar():
        page.get_by_text(rx("^todos$", "^everyone$", "^público$", "^public$")).first.click()
        pausa_humana(0.3, 0.8)
        page.get_by_text(rx("^somente (você|eu)$", "^só você$", "^only (you|me)$")).first.click()
    try:
        tentar(trocar, vezes=2, nome="visibilidade 'Só você'")
    except ErroPostagem as e:
        s.print("sem_privado")
        raise ErroPostagem("não consegui marcar 'Só você' no TikTok; parei sem publicar") from e


def enviar(s, video: str, titulo: str, descricao: str, privado: bool, ate_antes: bool) -> str:
    page = s.page
    s.emitir("abrindo", 2)
    if not logado(page):
        s.print("sem_sessao")
        raise SemSessao("TikTok sem login neste perfil: rode `conectar tiktok`.")
    entrada = page.locator(SEL_ARQUIVO).first
    entrada.wait_for(state="attached", timeout=30_000)
    pausa_humana()
    entrada.set_input_files(video)
    s.emitir("enviando", 10)

    legenda = page.locator(SEL_LEGENDA).first
    legenda.wait_for(state="visible", timeout=90_000)
    texto = descricao.strip() or titulo.strip()
    pausa_humana()
    tentar(lambda: limpar_e_digitar(page, legenda, texto[:2200]), nome="legenda")
    page.keyboard.press("Escape")            # fecha a sugestão de hashtag/menção, se abriu
    if privado:
        _somente_eu(s, page)

    postar = page.locator(SEL_POSTAR).first
    postar.wait_for(state="visible", timeout=60_000)
    limite, pct = time.time() + 20 * 60, 10
    while not _habilitado(postar):           # o botão só libera quando o envio termina
        if time.time() > limite:
            raise ErroPostagem("o envio ao TikTok passou de 20 min sem terminar")
        pct = min(pct + 4, 85)
        s.emitir("enviando", pct)
        page.wait_for_timeout(2_500)
    s.emitir("pronto_para_publicar", 90)
    prova = s.print("antes_de_publicar")

    if ate_antes:
        raise ParouAntesDePublicar({"print": prova, "aviso": "nada foi publicado no TikTok"})

    pausa_humana()
    postar.scroll_into_view_if_needed()
    tentar(lambda: postar.click(), nome="botão Publicar")
    # aviso de verificação de conteúdo: "Continuar a publicar?" -> "Publicar agora"
    try:
        b = page.get_by_role("button", name=rx("^publicar agora$", "^post now$"))
        b.first.wait_for(state="visible", timeout=6_000)
        pausa_humana()
        b.first.click()
    except Exception:
        pass
    s.emitir("publicando", 93)
    try:
        page.wait_for_url(re.compile(r"tiktokstudio/content|/manage"), timeout=3 * 60_000)
    except Exception:
        s.print("sem_confirmacao")
        raise ErroPostagem("cliquei em Publicar mas o TikTok não confirmou; confira no app")
    s.print("publicado")

    page.goto(URL_CONTEUDO, wait_until="domcontentloaded")
    try:
        a = page.locator('a[href*="/video/"]').first
        a.wait_for(state="attached", timeout=60_000)
        href = a.get_attribute("href") or ""
        return href if href.startswith("http") else "https://www.tiktok.com" + href
    except Exception:
        raise ErroPostagem("publicado, mas não achei o link na lista de conteúdos")
