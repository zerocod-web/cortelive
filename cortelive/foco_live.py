"""Foco da live inteira, conferido no começo (pedido do Gabriel, 19/11/2026).

Quando a live começa, a sessão grava um quadro dela com a CÂMERA e o JOGO já marcados (pelo foco salvo do canal ou
pelo automático) em _interno/foco_inicio.json + foco_inicio.jpg. O painel mostra esse quadro no editor de foco; a
pessoa confirma ou arrasta para corrigir e a marcação vale para os cortes seguintes da live. Sem resposta, segue no
automático (ou no foco salvo do canal): a live nunca espera. A câmera continua conferida por rosto em cada corte
(render.rever_cam): se a cam sair do lugar marcado, aquele corte volta ao automático.

"Usar nas próximas lives deste canal" guarda a marcação em %LOCALAPPDATA%\\cortelive\\focos.json (por canal); a
próxima live do canal já começa com ela. Esquecer: Configurações › Foco salvo por canal.
"""
import json
import os
import subprocess
import time
from pathlib import Path

ARQ_CANAIS = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "cortelive" / "focos.json"
ARQ = "foco_inicio.json"
QUADRO = "foco_inicio.jpg"
VALIDADE_PENDENTE_S = 3 * 3600      # pedido de conferência mais velho que isso não aparece mais no painel


def _ler(p):
    try:
        d = json.loads(Path(p).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _gravar(p, dados):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)


def _caixa(c):
    return isinstance(c, (list, tuple)) and len(c) == 4 and all(
        isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0 for v in c) and c[2] > 0 and c[3] > 0


def foco_ok(foco):
    return isinstance(foco, dict) and _caixa(foco.get("cam")) and _caixa(foco.get("jogo"))


def _limpo(foco):
    return {k: [int(round(float(v))) for v in foco[k]] for k in ("cam", "jogo")}


# ------------------------------------------------------------------ memória por canal
def canais_salvos():
    d = _ler(ARQ_CANAIS)
    return [{"canal": k, "quando": v.get("quando"), "tamanho": v.get("tamanho")}
            for k, v in sorted(d.items()) if isinstance(v, dict) and foco_ok(v.get("foco"))]


def salvo_do_canal(canal, tamanho=None):
    """Foco salvo do canal, ou None. Com 'tamanho' (largura, altura da live), só vale se a live tem o mesmo tamanho."""
    m = _ler(ARQ_CANAIS).get(str(canal).lower())
    if not isinstance(m, dict) or not foco_ok(m.get("foco")):
        return None
    if tamanho and m.get("tamanho") and list(m["tamanho"]) != list(tamanho):
        return None
    return m


def lembrar(canal, foco, tamanho):
    d = _ler(ARQ_CANAIS)
    d[str(canal).lower()] = {"foco": _limpo(foco), "tamanho": list(tamanho) if tamanho else None,
                             "quando": time.strftime("%Y-%m-%d %H:%M")}
    _gravar(ARQ_CANAIS, d)


def esquecer(canal):
    d = _ler(ARQ_CANAIS)
    existia = d.pop(str(canal).lower(), None) is not None
    _gravar(ARQ_CANAIS, d)
    return existia


# ------------------------------------------------------------------ conferência da live
def ler(dint):
    return _ler(Path(dint) / ARQ)


def foco_ativo(dint):
    """Foco que os cortes desta live usam agora (confirmado/corrigido, ou o salvo do canal); None = automático."""
    d = ler(dint)
    if d.get("estado") in ("confirmado", "salvo") and foco_ok(d.get("foco")):
        return _limpo(d["foco"])
    return None


def _tamanho(arq):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                          "-of", "csv=p=0", str(arq)], capture_output=True, text=True, timeout=15,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    # pedaço .ts da captura: o ffprobe repete "1920,1080" (programa e stream); vale a primeira linha
    linha = next(l for l in out.splitlines() if l.strip())
    w, h = (int(v) for v in linha.strip().split(",")[:2])
    return w, h


def propor(dint, canal, segmento, cam=None, modelo="cam-em-cima", jogo_auto=True):
    """Grava o quadro da live e a proposta de foco para o painel conferir. Foco salvo do canal (mesmo tamanho de
    tela) vem marcado e já vale; sem ele, a câmera achada pelo automático e o jogo pela janela do jogo."""
    dint, segmento = Path(dint), Path(segmento)
    tamanho = _tamanho(segmento)
    p = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(segmento), "-frames:v", "1", "-q:v", "3",
                        str(dint / QUADRO)], capture_output=True, timeout=30, stdin=subprocess.DEVNULL,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if p.returncode or not (dint / QUADRO).is_file():
        raise RuntimeError("não consegui tirar o quadro da live")
    salvo = salvo_do_canal(canal, tamanho)
    if salvo:
        proposta, origem, estado = _limpo(salvo["foco"]), "salvo do canal", "salvo"
    else:
        jogo = None
        if jogo_auto:
            try:
                from . import render
                jogo = render.janela_do_jogo(str(segmento), 0.0, 3.5, tamanho[0], tamanho[1], cam)
            except Exception:
                jogo = None
        proposta = {"cam": [int(v) for v in cam] if cam else None, "jogo": [int(v) for v in jogo] if jogo else None}
        origem, estado = "automático", "pendente"
    dados = {"canal": canal, "estado": estado, "origem": origem, "proposta": proposta, "tamanho": list(tamanho),
             "modelo": modelo, "t": time.time(), "foco": proposta if estado == "salvo" else None}
    _gravar(dint / ARQ, dados)
    return dados


def responder(dint, foco=None, lembrar_canal=True):
    """Resposta do painel: foco (confirmado ou corrigido) vale para o resto da live; None = deixar no automático."""
    dint = Path(dint)
    d = ler(dint)
    if not d:
        raise ValueError("Esta live não está esperando a conferência do foco.")
    if foco is not None:
        if not foco_ok(foco):
            raise ValueError("Marque a área da câmera e a da gameplay.")
        from . import render
        foco = render.foco_valido(_limpo(foco), *d["tamanho"]) if d.get("tamanho") else _limpo(foco)
        if "cam" not in foco:
            raise ValueError("Marque a área da câmera.")
        d.update(estado="confirmado", foco=foco)
        if lembrar_canal and d.get("canal"):
            lembrar(d["canal"], foco, d.get("tamanho"))
    else:
        d.update(estado="automatico", foco=None)
    d["respondido_em"] = time.strftime("%Y-%m-%d %H:%M:%S")
    d["lembrar"] = bool(lembrar_canal)
    _gravar(dint / ARQ, d)
    return d


def pendentes(pasta_cortes):
    """Lives com o foco esperando conferência (inclusive as que começaram no foco salvo do canal)."""
    out = []
    agora = time.time()
    for arq in Path(pasta_cortes).glob(f"*/_interno/{ARQ}"):
        d = _ler(arq)
        if d.get("estado") in ("pendente", "salvo") and agora - float(d.get("t", 0)) < VALIDADE_PENDENTE_S \
                and (arq.parent / QUADRO).is_file():
            out.append({"sessao": arq.parent.parent.name, "canal": d.get("canal"), "origem": d.get("origem"),
                        "proposta": d.get("proposta"), "tamanho": d.get("tamanho"), "t": d.get("t")})
    return sorted(out, key=lambda x: x["t"] or 0)
