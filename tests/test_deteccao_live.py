import subprocess
import sys

import pytest

from cortelive.captura import explicar_erro_fonte, normalizar_url


@pytest.mark.parametrize("url,esperado", [
    ("https://www.youtube.com/@LofiGirl", "https://www.youtube.com/@LofiGirl/live"),
    ("https://www.youtube.com/@LofiGirl/streams", "https://www.youtube.com/@LofiGirl/live"),
    ("https://www.youtube.com/channel/UCabc", "https://www.youtube.com/channel/UCabc/live"),
    ("https://www.youtube.com/watch?v=abc", "https://www.youtube.com/watch?v=abc"),
    ("https://www.youtube.com/@x/live", "https://www.youtube.com/@x/live"),
    ("https://kick.com/canal", "https://kick.com/canal"),
    ("https://www.twitch.tv/coconutb", "https://www.twitch.tv/coconutb"),
])
def test_normalizar_url(url, esperado):
    assert normalizar_url(url) == esperado


def test_offline_de_verdade_nao_vira_erro():
    off, msg, _ = explicar_erro_fonte("error: No playable streams found on this URL: https://www.twitch.tv/x")
    assert off and msg is None
    assert explicar_erro_fonte("")[0] is True


def test_erros_reais_aparecem_em_portugues():
    off, msg, trecho = explicar_erro_fonte("[plugins.youtube][error] Could not get video info - UNPLAYABLE")
    assert not off and "não está disponível" in msg and "UNPLAYABLE" in trecho
    assert "403" in explicar_erro_fonte("HTTP 403 Forbidden")[1]
    assert not explicar_erro_fonte("algo inesperado")[0]


def test_intermediario_repassa_stdout_ao_pipe():
    """Regressão v1.0.4: com CREATE_NO_WINDOW o filho perdia o stdout e nenhum vídeo chegava ao ffmpeg."""
    r = subprocess.run([sys.executable, "-m", "cortelive.processos", sys.executable, "-c", "print('ola')"],
                       capture_output=True, timeout=30)
    assert r.stdout.strip() == b"ola"


def test_linhas_do_motor_nao_se_misturam():
    import threading
    from cortelive import log
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        def f(n):
            for i in range(200):
                log.escrever_linha(f"@@STATUS@@{n}-{i}")
        ts = [threading.Thread(target=f, args=(n,)) for n in range(4)]
        [t.start() for t in ts]; [t.join() for t in ts]
    linhas = buf.getvalue().splitlines()
    assert len(linhas) == 800 and all(l.count("@@STATUS@@") == 1 for l in linhas)



@pytest.mark.parametrize("url,status,gravado", [
    ("https://www.youtube.com/watch?v=abc", "not_live", True),
    ("https://youtu.be/abc", "was_live", True),
    ("https://www.youtube.com/watch?v=abc", "is_live", False),
    ("https://www.youtube.com/watch?v=abc", "is_upcoming", False),
    ("https://www.twitch.tv/videos/123", None, True),
    ("https://kick.com/canal/videos/123", None, True),
    ("https://example.com/video.mp4?token=teste", None, True),
    ("https://www.twitch.tv/canal", None, False),
])
def test_classifica_video_gravado_sem_tratar_live_como_video(monkeypatch, url, status, gravado):
    from cortelive import captura

    monkeypatch.setattr(captura, "_status_youtube", lambda _: status)
    monkeypatch.setattr(captura, "_streamlink_aceita", lambda _: True)
    monkeypatch.setattr(captura, "_bin", lambda nome: nome)
    assert captura.classificar_link(url) == (gravado, None)


@pytest.mark.parametrize("fim_normal", [True, False])
def test_video_gravado_encerra_apos_uma_rodada(tmp_path, monkeypatch, fim_normal):
    from cortelive import captura

    cap = captura.Captura(tmp_path, url="https://example.com/video.mp4", video_gravado=True)
    rodadas = []

    def rodada(n):
        assert not rodadas
        rodadas.append(n)
        cap._n = int(fim_normal)
        cap.fim_live = 4.0 if fim_normal else 0.0
        return fim_normal

    monkeypatch.setattr(cap, "_rodada", rodada)
    cap._laco()
    assert rodadas == [1]
    assert cap.motivo_fim == ("vídeo terminou" if fim_normal else cap.erro_fatal)
    assert (cap.erro_fatal is None) is fim_normal



def test_youtube_com_video_e_audio_separados_pode_ser_capturado(tmp_path, monkeypatch):
    from cortelive import captura

    chamadas = []
    monkeypatch.setattr(captura, "_bin", lambda nome: nome)
    monkeypatch.setattr(captura.subprocess, "Popen", lambda cmd, **opts: chamadas.append(cmd))
    captura.Captura(tmp_path, url="https://youtu.be/x", video_gravado=True)._fonte()
    comando, = chamadas
    assert comando[comando.index("-f") + 1].endswith("/bv[height<=1080]+ba/bv+ba")


def test_replay_em_processamento_separa_leitores_dash(tmp_path, monkeypatch):
    from cortelive import captura

    chamadas = []
    monkeypatch.setattr(captura, "_bin", lambda nome: nome)
    monkeypatch.setattr(captura.subprocess, "Popen", lambda cmd, **opts: chamadas.append(cmd))
    captura.Captura(tmp_path, url="https://youtu.be/x", video_gravado=True, youtube_processando=True)._fonte()
    comando, = chamadas
    assert "cortelive.fonte_youtube" in comando
