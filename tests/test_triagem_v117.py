"""v1.0.17: aprender com o "Não quero", cortes repetidos, miniatura/primeira frase e espaço em disco."""
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from cortelive import aprendizado, miniatura, triagem


def _rejeitar(pasta, canal, nota, n, origem="nao_quero"):
    with open(pasta / "rejeitados.jsonl", "a", encoding="utf-8") as f:
        for i in range(n):
            f.write(json.dumps({"canal": canal, "nota_juiz": nota, "juiz": "chatgpt", "origem": origem,
                                "arquivo": f"rej_{canal}_{nota}_{i}.mp4"}) + "\n")


def _manter(pasta, canal, nota, n, idade_h=5):
    sessao = pasta / f"2026-11-19 10h00 - {canal}"
    (sessao / "_interno").mkdir(parents=True, exist_ok=True)
    for i in range(n):
        nome = f"mantido_{nota}_{i}"
        (sessao / f"{nome}.mp4").write_bytes(b"x")
        meta = sessao / "_interno" / f"{nome}.json"
        meta.write_text(json.dumps({"arquivo": f"{nome}.mp4", "nota_juiz": nota, "juiz": "chatgpt",
                                    "sinais": {"nota_gpt": nota}}), encoding="utf-8")
        t = time.time() - idade_h * 3600
        os.utime(meta, (t, t))


def test_aprendizado_so_ajusta_com_15_rejeitados(tmp_path):
    _rejeitar(tmp_path, "coringa", 5, 14)
    _manter(tmp_path, "coringa", 8, 14)
    b = aprendizado.Base.carregar(tmp_path)
    assert b.ajuste("coringa", 5) == (0, None)            # 14 rejeitados: ainda não aprende
    _rejeitar(tmp_path, "coringa", 5, 1)
    delta, porque = aprendizado.Base.carregar(tmp_path).ajuste("coringa", 5)
    assert delta == -2 and "coringa" in porque            # nota 4-6 sempre rejeitada: perde 2 (teto)
    assert aprendizado.Base.carregar(tmp_path).ajuste("coringa", 8)[0] == 2   # nota 8 sempre mantida: ganha 2


def test_aprendizado_nunca_passa_de_2_e_guarda_a_original(tmp_path):
    _rejeitar(tmp_path, "x", 3, 40)
    _manter(tmp_path, "x", 9, 40)
    b = aprendizado.Base.carregar(tmp_path)
    s = b.aplicar("x", {"nota_gpt": 3, "juiz": "chatgpt"})
    assert s["nota_gpt"] == 1 and s["nota_gpt_original"] == 3 and s["ajuste_aprendizado"]["delta"] == -2
    assert b.aplicar("x", s) is s                          # não reaplica em cima de nota já ajustada
    assert b.aplicar("x", {"juiz": "heurística"}) == {"juiz": "heurística"}


def test_aprendizado_ignora_mantidos_recentes_e_zerar_guarda_o_historico(tmp_path):
    _rejeitar(tmp_path, "y", 5, 20)
    _manter(tmp_path, "y", 5, 20, idade_h=0.5)            # cortes de agora: ele ainda não olhou
    b = aprendizado.Base.carregar(tmp_path)
    assert sum(1 for e in b.exemplos if not e[3]) == 0
    guardado = aprendizado.zerar(tmp_path)
    assert guardado and (tmp_path / guardado).is_file() and not (tmp_path / "rejeitados.jsonl").exists()
    assert aprendizado.status(tmp_path)["rejeitados"] == 0


def test_aprendizado_usa_o_canal_quando_tem_exemplos_e_o_geral_senao(tmp_path):
    _rejeitar(tmp_path, "a", 6, 16)          # canal a: rejeita os 6
    _manter(tmp_path, "b", 6, 16)            # canal b: mantém os 6
    _manter(tmp_path, "a", 9, 16)
    b = aprendizado.Base.carregar(tmp_path)
    assert b.ajuste("a", 6)[0] < 0
    d, porque = b.ajuste("c", 6)            # canal sem rejeições próprias: usa todos
    assert "todos os canais" in (porque or "todos os canais")


def test_sobreposicao_e_repetido():
    assert triagem.sobreposicao(0, 60, 30, 90) == 0.5
    assert triagem.sobreposicao(0, 60, 70, 90) == 0
    assert triagem.sobreposicao(10, 40, 0, 100) == 1.0
    novo = {"ini": 100, "fim": 160, "nota": 6, "sinais": {"nota_gpt": 7}}
    outros = [{"ini": 0, "fim": 60, "sinais": {"nota_gpt": 9}},
              {"inicio_na_live": 120, "fim_na_live": 190, "nota_juiz": 5}]
    r = triagem.repetido(novo, outros)
    assert r is outros[1] and triagem.valor(novo) > triagem.valor(r)
    assert triagem.repetido({"ini": 100, "fim": 160}, [{"ini": 150, "fim": 220}]) is None   # só 10 s em comum


def test_primeira_frase():
    from painel.motor_painel import primeira_frase
    assert primeira_frase("Eu falei pra ele não fazer isso. Aí ele fez.") == "Eu falei pra ele não fazer isso."
    longa = "palavra " * 40
    assert primeira_frase(longa).endswith("…") and len(primeira_frase(longa)) <= 121
    assert primeira_frase("") is None and primeira_frase(None) is None


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="sem ffmpeg")
def test_miniatura_do_meio_e_tira_da_previa(tmp_path):
    video = tmp_path / "corte.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=1080x1920:rate=30:duration=6",
                    "-c:v", "libx264", "-preset", "ultrafast", "-g", "15", str(video)], check=True)
    feito = miniatura.gerar(video, tmp_path, "corte")
    import cv2
    jpg, prev = cv2.imread(str(feito["jpg"])), cv2.imread(str(feito["previa"]))
    assert jpg.shape[1] == miniatura.LARGURA
    assert prev.shape[1] == miniatura.LARGURA_PREVIA * miniatura.QUADROS_PREVIA
    assert feito["jpg"].stat().st_size < 60_000


def test_espaco_originais_antigos_ou_sem_corte(tmp_path):
    from painel import espaco
    s = tmp_path / "2026-11-10 10h00 - x" / "_interno"
    s.mkdir(parents=True)
    (s.parent / "novo.mp4").write_bytes(b"x")
    (s.parent / "velho.mp4").write_bytes(b"x")
    for nome in ("novo", "velho", "orfao"):
        (s / f"{nome}.mkv").write_bytes(b"0" * 100)
    t = time.time() - 9 * 86400
    os.utime(s / "velho.mkv", (t, t))
    achados = {p.name for p in espaco.originais(tmp_path, 7)}
    assert achados == {"velho.mkv", "orfao.mkv"}


def test_rotas_aprendizado_e_espaco(tmp_path, monkeypatch):
    from painel import espaco, motor_painel
    api = motor_painel.ApiPainel(Path(__file__).resolve().parents[1])
    monkeypatch.setattr(api, "pasta_cortes", lambda: tmp_path)
    monkeypatch.setattr(espaco, "_lixeira", lambda pasta, apagar=False: [{"nome": "a.mp4", "bytes": 1024 ** 3}])
    st, d = api.responder("GET", "/api/aprendizado", {})
    assert st == 200 and d["rejeitados"] == 0 and d["minimo"] == 15
    st, d = api.responder("GET", "/api/espaco", {})
    assert st == 200 and d["lixeira"] == {"itens": 1, "bytes": 1024 ** 3} and d["nivel"] in ("ok", "aviso", "critico")
    st, d = api.responder("POST", "/api/aprendizado/zerar", {})
    assert st == 200


def test_interruptor_desligado_nao_ajusta_mas_continua_contando(tmp_path):
    _rejeitar(tmp_path, "x", 3, 40)
    _manter(tmp_path, "x", 9, 40)
    assert aprendizado.ligado(tmp_path)                    # padrão: ligado
    aprendizado.ligar(tmp_path, False)
    b = aprendizado.Base.carregar(tmp_path)
    s = {"nota_gpt": 3, "juiz": "chatgpt"}
    assert b.aplicar("x", s) is s                          # nota como veio
    st = aprendizado.status(tmp_path)
    assert st["ligado"] is False and st["rejeitados"] == 40
    aprendizado.ligar(tmp_path, True)
    assert b.aplicar("x", s)["nota_gpt"] == 1              # religado: vale na hora (sem recarregar)


def test_canal_legivel_troca_codigo_do_video(tmp_path):
    from cortelive import captura, pastas
    assert captura.canal_da_url("https://kick.com/gabepeixe/videos/01a1f40d-9408-7e8c-adea-818726f2907b") == "gabepeixe"
    assert captura.canal_da_url("https://www.twitch.tv/videos/2932200023") == "2932200023"
    assert captura.canal_da_url("https://www.twitch.tv/alanzoka") == "alanzoka"

    def sessao(nome, dados):
        p = tmp_path / f"2026-11-19 19h11 - {nome}"
        (p / "_interno").mkdir(parents=True)
        if dados is not None:
            (p / "_interno" / "sessao.json").write_text(json.dumps(dados), encoding="utf-8")
        return p

    assert pastas.canal_legivel(sessao("coringa", None)) == "coringa"
    uuid = "01a1f579-4968-758e-9a7d-85ed05f6c56f"
    assert pastas.canal_legivel(sessao(uuid, {"fonte": f"https://kick.com/bkinho/videos/{uuid}"})) == "bkinho"
    assert pastas.canal_legivel(sessao("2932200023", {"fonte": "https://www.twitch.tv/videos/2932200023"})) == "Vídeo da Twitch"
    assert pastas.canal_legivel(sessao("youtube_czLjal_UA9Q", {"fonte": "https://youtube.com/watch?v=czLjal_UA9Q",
                                                                "titulo": "GTA RP com a galera"})) == "GTA RP com a galera"
    assert pastas.canal_legivel(sessao("01a1f4b1-55e8-7f55-b5c0-d46938964856", {"fonte": r"C:\videos\a.mkv"})) == "Vídeo local"
    assert pastas.canal_legivel(sessao("01a1f4b1-55e8-7f55-b5c0-d46938964857", None)) == "Vídeo gravado"
