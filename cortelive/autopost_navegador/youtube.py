"""YouTube Shorts pelo YouTube Studio (vídeo vertical de até 3 min vira Short sozinho)."""
from __future__ import annotations

import re
import time

from .base import ErroPostagem, ParouAntesDePublicar, SemSessao, limpar_e_digitar, pausa_humana, tentar

URL_LOGIN = "https://studio.youtube.com/"
URL_UPLOAD = "https://www.youtube.com/upload"

SEL_ARQUIVO = 'ytcp-uploads-file-picker input[type="file"], input[type="file"][name="Filedata"]'
SEL_TITULO = "#title-textarea #textbox"
SEL_DESCRICAO = "#description-textarea #textbox"
SEL_NAO_CRIANCA = 'tp-yt-paper-radio-button[name="VIDEO_MADE_FOR_KIDS_NOT_MFK"]'
SEL_PROXIMO = "#next-button"
SEL_PUBLICO = 'tp-yt-paper-radio-button[name="PUBLIC"]'
SEL_PRIVADO = 'tp-yt-paper-radio-button[name="PRIVATE"]'
SEL_CONCLUIR = "#done-button"
SEL_LINK = "span.video-url-fadeable a, a.ytcp-video-info"
SEL_PROGRESSO = "ytcp-video-upload-progress .progress-label, ytcp-video-upload-progress"
RX_ENVIANDO = re.compile(r"(upload|envi|carreg)\w*\D{0,12}(\d{1,3})\s?%", re.I)


def logado(page) -> bool:
    page.goto(URL_LOGIN, wait_until="domcontentloaded")
    page.wait_for_load_state("load")
    return "accounts.google.com" not in page.url and "ServiceLogin" not in page.url


def _limpar_texto(t: str, limite: int) -> str:
    return t.replace("<", "").replace(">", "").strip()[:limite]   # o Studio recusa < e >


def enviar(s, video: str, titulo: str, descricao: str, privado: bool, ate_antes: bool) -> str:
    page = s.page
    s.emitir("abrindo", 2)
    if not logado(page):
        s.print("sem_sessao")
        raise SemSessao("YouTube sem login neste perfil: rode `conectar youtube`.")
    pausa_humana()
    page.goto(URL_UPLOAD, wait_until="domcontentloaded")
    entrada = page.locator(SEL_ARQUIVO).first
    try:
        entrada.wait_for(state="attached", timeout=45_000)
    except Exception as e:
        s.print("sem_tela_upload")
        if page.get_by_text(re.compile("criar (um )?canal|create (a )?channel", re.I)).count():
            raise ErroPostagem("a conta Google ainda não tem canal no YouTube: crie o canal uma vez") from e
        raise ErroPostagem("a tela de envio do YouTube Studio não abriu") from e
    pausa_humana()
    entrada.set_input_files(video)
    s.emitir("enviando", 10)

    titulo_box = page.locator(SEL_TITULO).first
    titulo_box.wait_for(state="visible", timeout=60_000)
    pausa_humana()
    tentar(lambda: limpar_e_digitar(page, titulo_box, _limpar_texto(titulo, 100)), nome="título")
    if descricao.strip():
        pausa_humana()
        tentar(lambda: limpar_e_digitar(page, page.locator(SEL_DESCRICAO).first,
                                         _limpar_texto(descricao, 4900)), nome="descrição")
    pausa_humana()
    nao_crianca = page.locator(SEL_NAO_CRIANCA).first
    nao_crianca.scroll_into_view_if_needed()
    tentar(lambda: nao_crianca.click(), nome="marcar 'não é para crianças'")
    s.print("detalhes")

    for _ in range(3):                       # Detalhes -> Elementos -> Verificações -> Visibilidade
        pausa_humana()
        tentar(lambda: page.locator(SEL_PROXIMO).first.click(), nome="botão Próximo")
    radio = page.locator(SEL_PRIVADO if privado else SEL_PUBLICO).first
    radio.wait_for(state="visible", timeout=30_000)
    pausa_humana()
    tentar(lambda: radio.click(), nome="visibilidade")

    link = ""
    try:
        a = page.locator(SEL_LINK).first
        a.wait_for(state="visible", timeout=30_000)
        link = (a.get_attribute("href") or a.inner_text() or "").strip()
    except Exception:
        pass

    # espera o envio terminar lendo a barra de progresso do próprio Studio (sem tempo fixo)
    limite = time.time() + 20 * 60
    ultimo_pct = 10
    while time.time() < limite:
        try:
            txt = page.locator(SEL_PROGRESSO).first.inner_text(timeout=5_000)
        except Exception:
            txt = ""
        m = RX_ENVIANDO.search(txt)
        if not m:
            break
        pct = 10 + int(int(m.group(2)) * 0.8)
        if pct != ultimo_pct:
            s.emitir("enviando", pct)
            ultimo_pct = pct
        page.wait_for_timeout(2_000)
    else:
        raise ErroPostagem("o envio ao YouTube passou de 20 min sem terminar")
    s.emitir("pronto_para_publicar", 92, **({"link": link} if link else {}))
    prova = s.print("antes_de_publicar")

    if ate_antes:
        raise ParouAntesDePublicar(
            {"link": link, "print": prova,
             "aviso": "o YouTube já guardou o vídeo como RASCUNHO PRIVADO no canal; apague no Studio se quiser"})

    pausa_humana()
    tentar(lambda: page.locator(SEL_CONCLUIR).first.click(), nome="botão Publicar")
    try:
        page.get_by_text(re.compile("v[ií]deo publicado|video published|v[ií]deo salvo|video saved", re.I)) \
            .first.wait_for(state="visible", timeout=60_000)
    except Exception:
        s.print("sem_confirmacao")
        raise ErroPostagem("cliquei em Publicar mas o YouTube não confirmou; confira no Studio")
    s.print("publicado")
    if not link:
        raise ErroPostagem("publicado, mas não consegui ler o link; confira no Studio")
    return link
