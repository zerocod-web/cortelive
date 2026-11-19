import os
from urllib.error import HTTPError

import pytest

from cortelive import gpt, momento


def escolher(monkeypatch, avaliar):
    seletor = momento.Seletor({})
    monkeypatch.setattr(seletor, "planejar", lambda *a: ({"fim": 18}, "", False))
    decisoes = []
    notas = {s: (5.0 if 8 <= s <= 12 else 0, [], {}) for s in range(40)}
    seletor.passo(notas, [{"w": "gol!", "s": 9, "e": 10}], 40, 39, 39, 4,
                  [], lambda d: decisoes.append(d) or True, final=True, juiz=avaliar)
    return decisoes


@pytest.mark.parametrize("valor,aceito", [(10, True), (0, False)])
def test_gpt_muda_escolha(monkeypatch, valor, aceito):
    monkeypatch.setattr(gpt, "ler", lambda: {"modo": "chatgpt"})
    monkeypatch.setattr(gpt, "pontuar", lambda conf, fala: valor if fala == "gol!" else None)
    decisoes = escolher(monkeypatch, gpt.Juiz(print).avaliar)
    assert bool(decisoes) is aceito
    if aceito:
        assert decisoes[0]["nota"] > 5
        assert decisoes[0]["sinais"] == {"nota_gpt": 10, "juiz": "chatgpt"}


@pytest.mark.parametrize("resposta", [429, 503, "vazia"])
def test_falha_retomada_local(monkeypatch, resposta):
    monkeypatch.setattr(gpt, "ler", lambda: {"modo": "api", "chave": "simulada"})
    chamadas = []
    def indisponivel(req, timeout):
        chamadas.append(1)
        if isinstance(resposta, int):
            raise HTTPError(req.full_url, resposta, "simulado", {}, None)
        from io import BytesIO
        return BytesIO(b'{"output":[]}')
    monkeypatch.setattr(gpt, "urlopen", indisponivel)
    avisos = []
    juiz = gpt.Juiz(avisos.append)
    for _ in range(2):
        decisoes = escolher(monkeypatch, juiz.avaliar)
        assert decisoes[0]["nota"] == 5 and decisoes[0]["sinais"] == {}
    assert len(chamadas) == len(avisos) == 1


@pytest.mark.skipif(os.name != "nt", reason="DPAPI")
def test_api_chave_e_ranking(monkeypatch, tmp_path):
    from io import BytesIO
    import json
    chave = "sk-" + "teste-local-sem-valor"
    monkeypatch.setattr(gpt, "arquivo", lambda: tmp_path / "credencial.bin")
    chamadas = []
    def simular(req, timeout):
        assert req.get_header("Authorization") == "Bearer " + chave
        chamadas.append(req.full_url)
        if req.data is None:
            return BytesIO(b'{"id":"teste"}')
        assert json.loads(req.data)["input"] == "gol!"
        return BytesIO(b'{"output":[{"content":[{"type":"output_text","text":"9"}]}]}')
    monkeypatch.setattr(gpt, "urlopen", simular)
    status = gpt.conectar("conectar", {"modo": "api", "chave": chave}, {})
    assert status == {"modo": "api", "estado": "salva"}
    assert chave.encode() not in gpt.arquivo().read_bytes()
    assert gpt.conectar("status", {}, {}) == status
    decisao, = escolher(monkeypatch, gpt.Juiz(print).avaliar)
    assert decisao["sinais"]["nota_gpt"] == 9
    assert len(chamadas) == 2



@pytest.mark.parametrize("juiz,nota,mostrada", [
    ("chatgpt", 0, 0), ("api", 8, 8), ("heurística", None, None),
    ("heurística", 8, None), ("chatgpt", "8", None), ("api", 11, None),
])
def test_lista_cortes_exibe_somente_nota_gpt_valida(tmp_path, juiz, nota, mostrada):
    import json
    from painel.motor_painel import ApiPainel

    pasta = tmp_path / "cortes" / "sessao"
    (pasta / "_interno").mkdir(parents=True)
    (pasta / "corte.mp4").touch()
    meta = {"arquivo": "corte.mp4", "juiz": juiz, "nota_juiz": nota}
    (pasta / "_interno" / "corte.json").write_text(json.dumps(meta), encoding="utf-8")
    corte, = ApiPainel(tmp_path).listar_cortes()
    assert corte["nota_juiz"] == mostrada



def test_gpt_escolhe_o_trecho_sem_notas_do_seletor_local(monkeypatch):
    monkeypatch.setattr(gpt, "ler", lambda: {"modo": "chatgpt"})
    palavras = [{"w": "gancho" if i == 0 else "fala.", "s": float(i), "e": i + 0.4} for i in range(80)]
    pedidos = []

    def responder(atual, instrucao, contexto):
        pedidos.append(contexto)
        return '{"primeiro":0,"ultimo":15,"momento":8,"nota":9}'

    monkeypatch.setattr(gpt, "_consultar", responder, raising=False)
    juiz = gpt.Juiz(print)
    modo, corte = juiz.selecionar(palavras, 66, 0, 61, 90)
    assert modo == "gpt"
    assert corte["sinais"] == {"nota_gpt": 9, "juiz": "chatgpt"}
    assert corte["janela"]["fim"] - corte["janela"]["ini"] >= 61
    assert "gancho" in pedidos[0]
    assert juiz.selecionar(palavras, 70, 0, 61, 90) == ("gpt", None)


def _fala(frases, inicio=0.0, pausa=0.7):
    """Frases (texto) -> palavras com tempo: 0,4 s por palavra, 0,1 s entre palavras, 'pausa' entre frases."""
    palavras, t = [], inicio
    for frase in frases:
        for w in frase.split():
            palavras.append({"w": w, "s": round(t, 2), "e": round(t + 0.4, 2)})
            t += 0.5
        t += pausa
    return palavras


def _juiz(monkeypatch, resposta, pedidos=None, **kw):
    monkeypatch.setattr(gpt, "ler", lambda: {"modo": "chatgpt"})

    def responder(atual, instrucao, contexto):
        if pedidos is not None:
            pedidos.append(contexto)
        return resposta(contexto) if callable(resposta) else resposta

    monkeypatch.setattr(gpt, "_consultar", responder)
    return gpt.Juiz(kw.pop("aviso", print), **kw)


def _linha(contexto, trecho):
    """ID da linha da transcrição enviada ao GPT que contém 'trecho'."""
    return next(int(l.split()[0]) for l in contexto.splitlines() if trecho in l)


def test_gpt_recebe_frases_e_o_corte_nao_comeca_no_meio_da_fala(monkeypatch):
    # 'mas aí quando eu cheguei,' termina em vírgula: a frase só fecha em 'cara.'
    frases = [f"assunto antigo número {i} encerrado." for i in range(8)] + [
        "mas aí quando eu cheguei,", "o carro tinha sumido da garagem cara.", "Eu fiquei maluco!",
        "Liguei pra polícia na hora.", "E o policial riu da minha cara!", "Que dia mano."] + \
        [f"outra coisa qualquer número {i} aqui." for i in range(10)]
    palavras = _fala(frases)
    escolha = {}

    def responder(contexto):
        escolha["ctx"] = contexto
        p = _linha(contexto, "o carro tinha sumido")
        return f'{{"primeiro":{p},"ultimo":{_linha(contexto, "Que dia mano")},"momento":{p + 3},"nota":8}}'

    juiz = _juiz(monkeypatch, responder)
    modo, corte = juiz.selecionar(palavras, palavras[-1]["e"] + 1, 0, 6, 60, alvo_min=10)
    assert modo == "gpt" and corte
    inicio = next(w for w in palavras if w["w"] == "mas")
    assert abs(corte["janela"]["ini"] - inicio["s"]) < 0.3        # recuou para o começo da frase
    fim = next(w for w in palavras if w["w"] == "mano.")
    assert abs(corte["janela"]["fim"] - fim["e"]) < 0.5
    assert corte["janela"]["lim_ini"] and corte["janela"]["lim_fim"]   # o script corta no silêncio
    # cada linha enviada é uma frase inteira, não um bloco de 4 s
    assert any(l.endswith("o carro tinha sumido da garagem cara.") for l in escolha["ctx"].splitlines())


def test_corte_curto_puxa_o_contexto_de_antes(monkeypatch):
    frases = [f"contexto importante parte {i} da história." for i in range(12)] + \
             ["E aí explodiu tudo!", "Kkkkk que isso."] + [f"depois veio {i} coisa." for i in range(6)]
    palavras = _fala(frases)

    def responder(contexto):
        p = _linha(contexto, "E aí explodiu")
        return f'{{"primeiro":{p},"ultimo":{p + 1},"momento":{p},"nota":9}}'

    juiz = _juiz(monkeypatch, responder)
    modo, corte = juiz.selecionar(palavras, palavras[-1]["e"] + 1, 0, 20, 60, alvo_min=30)
    ini, fim = corte["janela"]["ini"], corte["janela"]["fim"]
    assert fim - ini >= 30
    fim_esperado = next(w for w in palavras if w["w"] == "isso.")["e"]
    assert abs(fim - fim_esperado) < 0.5          # o fim continua na reação
    assert ini < next(w for w in palavras if w["w"] == "explodiu")["s"] - 20


def test_resposta_lenta_do_gpt_e_aproveitada_sem_cair_no_local(monkeypatch):
    import threading
    liberar = threading.Event()
    palavras = _fala([f"frase completa número {i} aqui." for i in range(40)])

    def responder(contexto):
        liberar.wait(5)
        return f'{{"primeiro":{_linha(contexto, "número 5 ")},"ultimo":{_linha(contexto, "número 20 ")},"momento":10,"nota":7}}'

    avisos = []
    juiz = _juiz(monkeypatch, responder, aviso=avisos.append, prazo=0.05)
    disp = palavras[-1]["e"] + 1
    assert juiz.selecionar(palavras, disp, 0, 20, 90) == ("gpt", None)    # ainda pensando: não decide nada
    liberar.set()
    import time
    time.sleep(0.2)
    modo, corte = juiz.selecionar(palavras, disp + 1, 0, 20, 90)
    assert modo == "gpt" and corte and not avisos


def test_resposta_com_cerca_de_codigo_e_texto_vira_corte(monkeypatch):
    palavras = _fala([f"frase completa número {i} aqui." for i in range(40)])
    juiz = _juiz(monkeypatch, lambda c: 'Claro:\n```json\n{"primeiro":2,"ultimo":12,"momento":5,"nota":7}\n```')
    modo, corte = juiz.selecionar(palavras, palavras[-1]["e"] + 1, 0, 20, 90)
    assert modo == "gpt" and corte


def test_resposta_ilegivel_nao_desliga_o_gpt(monkeypatch):
    palavras = _fala([f"frase completa número {i} aqui." for i in range(40)])
    avisos = []
    juiz = _juiz(monkeypatch, "não sei", aviso=avisos.append)
    assert juiz.selecionar(palavras, palavras[-1]["e"] + 1, 0, 20, 90) == ("gpt", None)
    assert not avisos and juiz.retomar == 0.0


def test_marcas_de_risada_e_grito_vao_para_o_gpt(monkeypatch):
    palavras = _fala([f"frase completa número {i} aqui." for i in range(40)])
    pedidos = []
    juiz = _juiz(monkeypatch, "null", pedidos)
    alvo = int(palavras[50]["s"])
    juiz.selecionar(palavras, palavras[-1]["e"] + 1, 0, 20, 90,
                    marcas=lambda a, b: {alvo: ["risada", "volume alto (2.0x acima do normal)"]})
    linha = next(l for l in pedidos[0].splitlines() if "[" in l.split("]", 1)[1])
    assert "risada" in linha and "grito" in linha


def test_corte_que_termina_na_borda_da_transcricao_espera_o_resto(monkeypatch):
    palavras = _fala([f"frase completa número {i} aqui." for i in range(40)])

    def responder(contexto):
        ult = max(int(l.split()[0]) for l in contexto.splitlines())
        return f'{{"primeiro":{ult - 10},"ultimo":{ult},"momento":{ult - 2},"nota":8}}'

    juiz = _juiz(monkeypatch, responder)
    assert juiz.selecionar(palavras, palavras[-1]["e"] + 0.5, 0, 20, 90) == ("gpt", None)
    modo, corte = _juiz(monkeypatch, responder).selecionar(palavras, palavras[-1]["e"] + 0.5, 0, 20, 90, final=True)
    assert corte is not None


def test_sem_espaco_para_recuar_comeca_na_proxima_frase_que_abre(monkeypatch):
    # o corte anterior termina no meio de 'e eu ia falando disso.': recuar até onde a frase abre é proibido
    frases = [f"assunto velho número {i} encerrado." for i in range(6)] + ["e eu ia falando disso."] + \
             [f"Agora sim história nova {i} completa." for i in range(20)]
    palavras = _fala(frases)
    meio = next(w for w in palavras if w["w"] == "ia")

    def responder(contexto):
        p = _linha(contexto, "ia falando")
        return f'{{"primeiro":{p},"ultimo":{p + 14},"momento":{p + 3},"nota":7}}'

    juiz = _juiz(monkeypatch, responder)
    modo, corte = juiz.selecionar(palavras, palavras[-1]["e"] + 1, meio["s"], 20, 90, alvo_min=20)
    primeira = next(w for w in palavras if w["w"] == "Agora")
    assert abs(corte["janela"]["ini"] - primeira["s"]) < 0.3
