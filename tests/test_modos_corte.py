"""v1.0.17: modo de cada corte (Câmera + jogo, Tela cheia, Fundo desfocado), troca automática sem gameplay separada
e original leve por corte (refazer depois que a live acabou)."""
import json
import os
import shutil
import subprocess
import time

import pytest

from cortelive import original, render
from painel import foco, motor_painel

W, H = 1920, 1080


# --- regra automática ------------------------------------------------------------------------------------------------
def test_live_so_de_camera_nao_repete_a_cara():
    # o detector de 'só cam' devolve a tela toda como câmera
    assert render.sem_jogo_separado((0, 0, W, H), (420, 0, 1080, H), W, H) == "live só de câmera"


def test_cam_de_canto_com_jogo_ao_lado_fica_camera_mais_jogo():
    assert render.sem_jogo_separado((1498, 772, 400, 238), (300, 0, 1080, H), W, H) is None


def test_jogo_dentro_da_cam_e_a_mesma_regiao():
    # cam grande (35% da tela) e a janela do jogo quase toda dentro dela
    assert render.sem_jogo_separado((700, 100, 1100, 650), (800, 100, 900, 600), W, H) == \
        "câmera e jogo mostram a mesma região"


def test_sem_cam_nao_troca():
    assert render.sem_jogo_separado(None, (0, 0, W, H), W, H) is None


# --- legenda ao trocar de modo ---------------------------------------------------------------------------------------
ASS = """[Script Info]
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Style: Base,Anton,96,&H00FFFFFF,&H0088FF39,&H00101010,&H60000000,0,0,0,0,100,100,0,0,1,6,0,2,90,160,560,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:01.00,Base,,0,0,0,,{\\an5\\pos(540,700)}{\\1c&H0088FF39&}OI GENTE
Dialogue: 0,0:00:01.00,0:00:02.00,Base,,0,0,0,,{\\an5\\pos(540,700)}TUDO BEM
"""


def test_legenda_sai_da_emenda_no_fundo_desfocado():
    m = render.carregar("modelo", "jogo-fundo-desfocado")
    est = render.carregar("legenda", "opus")
    txt = render.ass_para_modelo(ASS, m, est)
    assert "\\pos(" not in txt
    assert txt.count("Dialogue:") == 2 and "OI GENTE" in txt
    assert f",{m['legenda_margem_baixo']},1" in txt          # margem do modo novo


def test_legenda_volta_para_a_faixa_no_camera_mais_jogo():
    m = dict(render.carregar("modelo", "cam-em-cima"), _faixa={"modo": "rasgada", "centro": 760})
    sem_pos = render.ass_para_modelo(ASS, render.carregar("modelo", "jogo-fundo-desfocado"))
    txt = render.ass_para_modelo(sem_pos, m)
    assert txt.count("{\\an5\\pos(540,760)}") == 2


# --- original do corte -----------------------------------------------------------------------------------------------
def test_janela_do_original_tem_folga_e_nao_passa_do_arquivo():
    assert original.janela(10, 40, 60) == (8.0, 42.0)
    assert original.janela(1, 40, 41) == (0.0, 41.0)


def test_dias_config():
    assert original.dias_config({}) == 7
    assert original.dias_config({"originais_dias": 0}) == 0
    assert original.dias_config({"originais_dias": 5}) == 7
    assert original.dias_config({"originais_dias": "lixo"}) == 7


def _corte_falso(pasta, nome, idade_dias):
    dint = pasta / "canal" / "_interno"
    dint.mkdir(parents=True, exist_ok=True)
    mkv = dint / f"{nome}.mkv"
    mkv.write_bytes(b"x" * 100)
    t = time.time() - idade_dias * 86400
    os.utime(mkv, (t, t))
    (dint / f"{nome}.json").write_text(json.dumps({"arquivo": f"{nome}.mp4", "refazer": {"video": f"{nome}.mkv"}}),
                                       encoding="utf-8")
    return mkv


def test_limpa_so_originais_vencidos(tmp_path):
    velho, novo = _corte_falso(tmp_path, "velho", 8), _corte_falso(tmp_path, "novo", 2)
    solto = tmp_path / "canal" / "_interno" / "solto.mkv"      # não citado por nenhum corte: não é tocado
    solto.write_bytes(b"y")
    os.utime(solto, (0, 0))
    assert original.limpar_vencidos(tmp_path, 7) == (1, 100)
    assert not velho.exists() and novo.exists() and solto.exists()
    assert original.limpar_vencidos(tmp_path, 0) == (0, 0)     # 0 = nunca apagar
    assert novo.exists()


# --- painel ----------------------------------------------------------------------------------------------------------
def test_config_de_dias_valida(tmp_path, monkeypatch):
    api = motor_painel.ApiPainel(tmp_path)
    monkeypatch.setattr(api, "limpar_originais", lambda forcar=False: (0, 0))
    assert motor_painel.config_postagem(tmp_path)["originais_dias"] == 7
    st, d = api.responder("POST", "/api/config-postagem", {"originais_dias": 3})
    assert st == 200 and d["originais_dias"] == 3
    st, d = api.responder("POST", "/api/config-postagem", {"originais_dias": 5})
    assert st == 400
    st, d = api.responder("POST", "/api/config-postagem", {"originais_dias": True})
    assert st == 400


def test_rota_de_modo_recusa_modo_desconhecido(tmp_path):
    api = motor_painel.ApiPainel(tmp_path)
    st, d = api.responder("POST", "/api/foco/modo", {"caminho": "nao_existe.mp4", "modo": "camera-jogo"})
    assert st == 404                     # rota existe; o corte é que não
    st, d = api.responder("GET", "/api/foco/modo", {})
    assert st == 405


def test_modo_atual_de_corte_antigo():
    assert foco.modo_atual({"modo_corte": "tela-cheia"}) == "tela-cheia"
    assert foco.modo_atual({"modelo_usado": "fundo-desfocado"}) == "fundo-desfocado"
    assert foco.modo_atual({"modelo_usado": "cam-em-cima"}) == "camera-jogo"
    assert foco.modo_atual({}) is None


# --- render de verdade (vídeo sintético curto) -----------------------------------------------------------------------
@pytest.fixture(scope="module")
def video(tmp_path_factory):
    if not shutil.which("ffmpeg"):
        pytest.skip("sem ffmpeg")
    arq = tmp_path_factory.mktemp("modos") / "fonte.mkv"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=1280x720:r=60:d=8",
                    "-f", "lavfi", "-i", "sine=f=440:d=8", "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "aac", "-shortest", str(arq)], check=True)
    return arq


@pytest.mark.parametrize("modo,tipo", [("fundo-desfocado", "fundo-desfocado"), ("tela-cheia", "crop")])
def test_modo_escolhido_vale_exatamente(video, tmp_path, modo, tipo):
    pal = [{"w": "teste", "start": 3.0, "end": 3.5}]
    r = render.render_corte(str(video), 2.0, 5.0, pal, "cam-em-cima", "opus", (0, 0, 1280, 720), str(tmp_path),
                            "corte_001", modo=modo)
    assert r["modelo"] == tipo and r["modo"] == modo and r["legenda_ok"]
    assert render.tamanho(r["mp4"]) == (1080, 1920)


def test_auto_com_live_so_de_camera_vira_fundo_desfocado(video, tmp_path):
    r = render.render_corte(str(video), 2.0, 5.0, [], "cam-em-cima", "opus", (0, 0, 1280, 720), str(tmp_path),
                            "corte_002")
    assert r["modo"] == "fundo-desfocado" and r["modo_motivo"] == "live só de câmera"


def test_original_guarda_so_o_trecho_com_folga(video, tmp_path):
    o = original.guardar(str(video), 3.0, 5.0, tmp_path / "o.mkv")
    assert o["deslocamento"] == 1.0 and o["inicio"] == 2.0 and o["fim"] == 4.0
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                o["arquivo"]], capture_output=True, text=True).stdout)
    assert 5.8 <= dur <= 6.3
    assert render._probe(o["arquivo"])[:2] == (1280, 720) and eval(render._probe(o["arquivo"])[2]) == 30


def test_auto_vale_mesmo_quando_a_conferencia_larga_a_cam(video, tmp_path, monkeypatch):
    # live só de câmera com a pessoa fora do quadro: rever_cam devolve None; a caixa da live ainda manda
    monkeypatch.setattr(render, "rever_cam", lambda *a, **k: (None, "cam sem rosto"))
    r = render.render_corte(str(video), 2.0, 5.0, [], "cam-em-cima", "opus", (0, 0, 1280, 720), str(tmp_path),
                            "corte_003", recuo_sem_cam=[], conferir_cam=True)
    assert r["modo"] == "fundo-desfocado" and r["modo_motivo"] == "live só de câmera"
