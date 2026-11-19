"""Editor de foco do corte (v1.0.16) e troca de modo do corte (v1.0.17): o painel mostra um quadro do corte, a pessoa marca a área da CÂMERA e a da
GAMEPLAY e o corte é refeito com essa marcação, fixa. "Voltar ao automático" refaz sem a marcação.

O render roda num processo à parte (python -m painel.foco <mp4> <acao>): o painel consulta o andamento pelo arquivo
<stem>.foco.json ao lado do .json do corte. A fonte do corte é o trecho bruto guardado pela sessão (<stem>.mkv,
cortes da 1.0.16 em diante; da 1.0.17 em diante é o original leve, só o corte com 2 s de folga); corte antigo de VOD (Kick/Twitch/YouTube) baixa só o trecho do corte pela URL da live.
"""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

FOLGA_VOD = 2.0          # segundos baixados antes e depois do corte quando a fonte é o VOD
MODOS = ("camera-jogo", "tela-cheia", "fundo-desfocado")
NOMES_MODO = {"camera-jogo": "Câmera + jogo", "tela-cheia": "Tela cheia", "fundo-desfocado": "Fundo desfocado"}
MODO_DO_TIPO = {"cam-em-cima": "camera-jogo", "crop": "tela-cheia", "fundo-desfocado": "fundo-desfocado"}


def modo_atual(d):
    """Modo do corte pelo .json: o gravado (1.0.17+) ou deduzido do modelo usado (cortes antigos)."""
    if d.get("modo_corte") in MODOS:
        return d["modo_corte"]
    return MODO_DO_TIPO.get(d.get("modelo_usado") or "") or ("camera-jogo" if d.get("cam") else None)


class ErroFoco(Exception):
    pass


def _ler(p):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return {}


def _gravar(p, dados):
    tmp = Path(str(p) + ".tmp")
    tmp.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)


def _meta(video):
    from cortelive import pastas
    meta = pastas.meta_do_video(video)
    if not meta or not meta.is_file():
        raise ErroFoco("Este corte não tem os dados do CorteLive (foi feito por outra versão ou movido).")
    return meta, _ler(meta)


def _status_arq(meta):
    return meta.with_name(meta.stem + ".foco.json")


def fonte_local(video):
    """-> (arquivo, inicio, fim) do trecho bruto guardado, ou None."""
    meta, d = _meta(video)
    for chave in ("refazer", "foco_fonte"):
        r = d.get(chave) or {}
        if r.get("video") and (meta.parent / r["video"]).is_file():
            return meta.parent / r["video"], float(r["inicio"]), float(r["fim"])
    return None


def url_vod(video):
    from cortelive import pastas
    fonte = _ler(pastas.sessao_json_do_video(video)).get("fonte")
    if isinstance(fonte, str) and fonte.startswith(("http://", "https://")) and any(
            k in fonte for k in ("/videos/", "/video/", "youtube.com/watch", "youtu.be/")):
        return fonte
    return None


def _tamanho(arq):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                          "-of", "csv=p=0", str(arq)], capture_output=True, text=True, timeout=15,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    w, h = (int(v) for v in out.strip().split(",")[:2])
    return w, h


def proporcoes():
    from cortelive import render
    m = render.carregar("modelo", "cam-em-cima")
    W, H, hc = m["largura"], m["altura"], m.get("altura_cam", 640)
    return {"cam": W / hc, "jogo": W / (H - hc), "largura": W, "altura": H, "altura_cam": hc}


def estado(video):
    meta, d = _meta(video)
    st = _ler(_status_arq(meta))
    parado = time.time() - st.get("t", 0)
    if st.get("estado") in ("baixando", "renderizando") and (
            parado > 900 or (st.get("mensagem") == "Começando…" and parado > 30)):
        # o processo à parte não chegou a começar (30 s) ou morreu no meio (15 min)
        st = {"estado": "erro", "mensagem": "O trabalho anterior parou sem terminar. Tente de novo."}
    loc = fonte_local(video)
    out = {"foco": d.get("foco"), "cam_auto": d.get("cam"), "duracao": d.get("duracao"), "modo": modo_atual(d),
           "fonte_pronta": bool(loc), "pode_baixar": bool(url_vod(video)), "trabalho": st or None,
           "proporcoes": proporcoes(), "tamanho": None}
    if loc:
        try:
            out["tamanho"] = list(_tamanho(loc[0]))
        except Exception:
            out["fonte_pronta"] = False
    return out


def quadro(video, t=0.0, largura=960):
    """JPEG de um quadro do corte (t em segundos desde o início do corte), na largura pedida."""
    loc = fonte_local(video)
    if not loc:
        raise ErroFoco("O trecho deste corte ainda não está no computador.")
    arq, ini, fim = loc
    t = min(max(0.0, float(t)), max(0.0, fim - ini - 0.1))
    p = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{ini + t:.3f}", "-i", str(arq), "-frames:v", "1",
                        "-vf", f"scale={int(largura)}:-2", "-f", "image2pipe", "-vcodec", "mjpeg", "-q:v", "3",
                        "pipe:1"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                       timeout=12, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if p.returncode or not p.stdout:
        raise ErroFoco("Não consegui ler o quadro do corte.")
    return p.stdout


def iniciar(video, acao, foco=None, ambiente=None, raiz=None, python=None, modo=None):
    """Começa (em outro processo) 'baixar', 'refazer' (com foco), 'automatico' ou 'modo' (com modo). Volta na hora."""
    if acao not in ("baixar", "refazer", "automatico", "modo"):
        raise ErroFoco("Ação inválida.")
    if acao == "modo" and modo not in MODOS:
        raise ErroFoco("Escolha o modo do corte: Câmera + jogo, Tela cheia ou Fundo desfocado.")
    meta, d = _meta(video)
    st = _ler(_status_arq(meta))
    if st.get("estado") in ("baixando", "renderizando") and time.time() - st.get("t", 0) < 900:
        raise ErroFoco("Este corte já está sendo refeito. Espere terminar.")
    if acao == "refazer":
        if not isinstance(foco, dict) or not foco.get("cam") or not foco.get("jogo"):
            raise ErroFoco("Marque a área da câmera e a da gameplay antes de refazer.")
    if not fonte_local(video) and not url_vod(video):
        raise ErroFoco("O original deste corte não está mais no computador (foi apagado pelo prazo das "
                       "configurações ou o corte é de uma versão antiga) e a live não tem VOD para baixar.")
    _gravar(_status_arq(meta), {"estado": "baixando" if not fonte_local(video) else "renderizando",
                                "acao": acao, "t": time.time(), "mensagem": "Começando…"})
    if foco is not None or modo is not None:
        _gravar(_status_arq(meta).with_suffix(".pedido.json"), {"foco": foco, "modo": modo})
    raiz = Path(raiz or Path(__file__).resolve().parent.parent)
    fl = 0
    if os.name == "nt":
        fl = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    # saída do processo num log ao lado do corte: se ele morrer antes de gravar o estado, o motivo fica lá
    with open(meta.with_name(meta.stem + ".foco.log"), "ab") as log:
        subprocess.Popen([str(python or sys.executable), "-X", "utf8", "-m", "painel.foco", str(video), acao],
                         cwd=str(raiz), env=ambiente or os.environ.copy(), stdin=subprocess.DEVNULL, stdout=log,
                         stderr=log, creationflags=fl, close_fds=True)
    return {"mensagem": "Começou. O corte será refeito em alguns segundos."}


# ---------------------------------------------------------------- processo à parte -------------------------------

def _baixar_trecho(video, meta, d):
    url = url_vod(video)
    if not url:
        raise ErroFoco("A live não tem VOD para baixar o trecho.")
    ini = max(0.0, float(d["inicio_na_live"]) - FOLGA_VOD)
    dur = float(d["fim_na_live"]) - ini + FOLGA_VOD
    g = subprocess.run([sys.executable, "-m", "yt_dlp", "-g", "-f", "best[height<=1080]/best", url],
                       capture_output=True, text=True, timeout=120)
    direto = (g.stdout or "").strip().splitlines()
    if g.returncode or not direto:
        raise ErroFoco("Não consegui abrir o VOD da live para baixar o trecho.")
    destino = meta.with_suffix(".mkv")
    tmp = destino.with_name(destino.stem + ".baixando.mkv")
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{ini:.3f}", "-i", direto[0], "-t", f"{dur:.3f}",
                        "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy", str(tmp)],
                       capture_output=True, text=True, timeout=600)
    if r.returncode or not tmp.is_file():
        tmp.unlink(missing_ok=True)
        raise ErroFoco("O download do trecho do VOD falhou.")
    os.replace(tmp, destino)
    d = _ler(meta)
    d["foco_fonte"] = {"video": destino.name, "inicio": round(float(d["inicio_na_live"]) - ini, 3),
                       "fim": round(float(d["fim_na_live"]) - ini, 3)}
    _gravar(meta, d)
    return d


def _refazer(video, meta, d, foco, modo=None):
    from cortelive import render
    arq, ini, fim = fonte_local(video)
    r_ = d.get("refazer") or {}
    dint = meta.parent
    ass_antigo = dint / d["legenda"] if d.get("legenda") else None
    palavras = r_.get("palavras")
    nome = r_.get("nome") or d.get("nome_interno") or "corte_001"
    trab = dint / f".foco_{nome}"
    shutil.rmtree(trab, ignore_errors=True)
    trab.mkdir()
    try:
        res = render.render_corte(
            str(arq), ini, fim, palavras or [], r_.get("modelo") or d.get("modelo") or "cam-em-cima",
            r_.get("legenda") or d.get("estilo_legenda") or "opus",
            None if foco else (tuple(d["cam_auto_original"]) if d.get("cam_auto_original")
                               else (tuple(d["cam"]) if d.get("cam") else None)),
            str(trab), nome, faixa=r_.get("faixa_legenda"), faixa_cor=r_.get("faixa_cor"),
            recuo_sem_cam=r_.get("recuo_sem_cam"), conferir_cam=not foco, foco=foco,
            ass_pronto=None if palavras else (str(ass_antigo) if ass_antigo and ass_antigo.is_file() else None),
            modo="camera-jogo" if foco else (modo or "auto"))
        for tentativa in range(10):
            try:
                os.replace(res["mp4"], video)
                break
            except PermissionError:
                if tentativa == 9:
                    raise ErroFoco("O vídeo do corte está aberto em outro programa. Feche o player e tente de novo.")
                time.sleep(1)
        if res.get("ass") and ass_antigo:
            os.replace(res["ass"], ass_antigo)
    finally:
        shutil.rmtree(trab, ignore_errors=True)
    (dint / (Path(video).stem + ".jpg")).unlink(missing_ok=True)      # miniatura antiga
    d = _ler(meta)
    if "cam_auto_original" not in d:
        d["cam_auto_original"] = d.get("cam")
    if foco:
        d["foco"] = render.foco_valido(foco, *_tamanho(arq))
    else:
        d.pop("foco", None)
        d["cam"] = res.get("cam_usada")
        d["cam_revista"] = res.get("cam_revista")
    d["modelo_usado"] = res.get("modelo")
    d["modo_corte"] = res.get("modo")
    d["modo_motivo"] = res.get("modo_motivo")
    d["encoder"] = res.get("encoder")
    d["foco_refeito_em"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _gravar(meta, d)
    return res


def executar(video, acao):
    video = Path(video)
    meta, d = _meta(video)
    st_arq = _status_arq(meta)
    ped = st_arq.with_suffix(".pedido.json")
    pedido = _ler(ped)
    foco = pedido.get("foco") if acao == "refazer" else None
    modo = pedido.get("modo") if acao == "modo" else None
    ped.unlink(missing_ok=True)
    try:
        if not fonte_local(video):
            _gravar(st_arq, {"estado": "baixando", "acao": acao, "t": time.time(),
                             "mensagem": "Baixando o trecho do corte do VOD…"})
            d = _baixar_trecho(video, meta, d)
        if acao == "baixar":
            _gravar(st_arq, {"estado": "pronto", "acao": acao, "t": time.time(), "mensagem": "Trecho pronto."})
            return
        _gravar(st_arq, {"estado": "renderizando", "acao": acao, "t": time.time(), "modo": modo,
                         "mensagem": f"Refazendo em {NOMES_MODO[modo]}…" if modo else "Refazendo o corte…"})
        res = _refazer(video, meta, _ler(meta), foco, modo)
        if modo:
            msg = f"Corte refeito em {NOMES_MODO.get(res.get('modo'), NOMES_MODO[modo])}."
        elif foco:
            msg = "Corte refeito com o foco que você marcou."
        else:
            msg = "Corte refeito no automático." + (f" ({res['cam_revista']})" if res.get("cam_revista") else "")
            if res.get("modo_motivo"):
                msg += f" Sem gameplay separada: ficou em {NOMES_MODO.get(res.get('modo'), 'Fundo desfocado')}."
        _gravar(st_arq, {"estado": "pronto", "acao": acao, "t": time.time(), "mensagem": msg})
    except ErroFoco as e:
        _gravar(st_arq, {"estado": "erro", "acao": acao, "t": time.time(), "mensagem": str(e)})
    except Exception as e:
        _gravar(st_arq, {"estado": "erro", "acao": acao, "t": time.time(),
                         "mensagem": "Não consegui refazer o corte. " + str(e)[:300]})


if __name__ == "__main__":
    executar(sys.argv[1], sys.argv[2])
