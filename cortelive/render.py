"""Render: trecho da live -> MP4 vertical 1080x1920 sem legenda -> .ass -> MP4 legendado (libass).

Modelos de corte e de legenda são arquivos JSON em presets\\modelo e presets\\legenda (editáveis;
um 4º estilo = copiar um arquivo). A ideia de agrupar palavras e destacar a ativa em ASS vem do
subtitles.py do OpenShorts (MIT); o código aqui é próprio.
"""
import json
import re
import subprocess
from pathlib import Path

from . import hw

PRESETS = Path(__file__).resolve().parent / "presets"
FONTES = Path(__file__).resolve().parent / "fontes"


def listar(tipo):
    return sorted(p.stem for p in (PRESETS / tipo).glob("*.json"))


def carregar(tipo, nome, pasta_usuario: Path | None = None):
    for base in ([pasta_usuario / "presets" / tipo] if pasta_usuario else []) + [PRESETS / tipo]:
        p = base / f"{nome}.json"
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8-sig"))
    raise SystemExit(f"Modelo '{nome}' não existe. Opções: {', '.join(listar(tipo))}")


def _px(frac, total):
    return int(round(frac * total / 2) * 2)


# Escala da cam: lanczos (mais nítido que o bicubic padrão ao ampliar 1,8-3x) e um unsharp leve só
# na cam nítida (o fundo desfocado e o jogo ficam como estão).
ESCALA_CAM = "flags=lanczos"
NITIDEZ_CAM = "unsharp=5:5:0.5:5:5:0.0"

# --- faixa atrás da legenda (cam-em-cima) ------------------------------------------------------
# PNG RGBA 1080x220 em presets\faixas; o papel é opaco das linhas ~36 a ~178 (medido nos 4 arquivos),
# o resto é a borda rasgada e transparência. Sem o PNG, cai numa faixa lisa da mesma cor.
FAIXAS = PRESETS / "faixas"
FAIXA_ESCALA = 0.6    # v1.0.11: a faixa ocupa 60% da altura anterior (o PNG é esticado para essa altura)
FAIXA_ALT, FAIXA_OPACO = round(220 * FAIXA_ESCALA), (round(36 * FAIXA_ESCALA), round(178 * FAIXA_ESCALA))
FAIXA_COR_LISA = {"vermelha": "0xBA0000", "creme": "0xEFE6D2"}
FAIXA_FOLGA = 8  # papel opaco passa N px além do blur em cima e embaixo


def _jogo_janela(m, sw, sh):
    """Janela do jogo (nw, nh, jx, jy) e a altura que ela ocupa no painel depois de escalada."""
    W, H = m["largura"], m["altura"]
    hj = H - m.get("altura_cam", 640)
    j = m["jogo"]
    jx, jy, jw, jh = _px(j["x"], sw), _px(j["y"], sh), _px(j["w"], sw), _px(j["h"], sh)
    vista = m.get("jogo_vista", 0.0)  # opcional: alarga a janela do jogo além do retângulo
    nw = min(sw, max(jw, _px(vista, sw)))
    nh = jh
    if nw / nh < W / hj:  # janela já mais estreita que o painel: crop puro
        nw = _px(nh * W / hj / sw, sw)
    elif m.get("_jogo_auto"):
        # v1.0.12: jogo 4:3 (faixas pretas) encolheria no meio do painel: corta até 22% das laterais (zoom) e o resto
        # fica no fundo desfocado do jogo, em vez de uma barra preta grande
        nw = min(nw, max(int(nw * 0.78), _px(nh * W / hj / sw, sw)) // 2 * 2)
    jx = min(max(0, jx + (jw - nw) // 2), sw - nw) // 2 * 2
    jy = min(max(0, jy + (jh - nh) // 2), sh - nh) // 2 * 2
    hg = min(hj, int(round(nh * W / nw / 2)) * 2)
    return nw, nh, jx, jy, hg


def janela_do_jogo(video, inicio, fim, sw, sh, cam=None):
    """Onde está a imagem do jogo quando a live tem faixas pretas nas laterais (jogo 4:3 em tela 16:9, CS 1.6).
    Mede, em 6 quadros do trecho, quanto de cada coluna tem imagem (fora da cam); a maior corrida contínua de
    colunas com imagem é o jogo. Assim o corte mostra o jogo INTEIRO e não pega o que fica nas faixas pretas
    (logo/overlay da live). Jogo em tela cheia (corrida >= 92% da largura) ou medida incerta: None (usa o modelo).
    Devolve (x, y, w, h) em px do vídeo, ou None."""
    try:
        import cv2
        import numpy as np
    except Exception:
        return None
    cap = cv2.VideoCapture(str(video))
    esc = 480 / sw
    acc = []
    for k in range(6):
        cap.set(cv2.CAP_PROP_POS_MSEC, (inicio + (fim - inicio) * (k + 0.5) / 6) * 1000)
        ok, f = cap.read()
        if not ok:
            continue
        g = cv2.cvtColor(cv2.resize(f, (480, int(sh * esc))), cv2.COLOR_BGR2GRAY).astype(np.float32)
        viva = (cv2.GaussianBlur(g, (3, 3), 0) > 22).astype(np.float32)
        if cam:
            x, y, w, h = cam
            viva[int(y * esc):int((y + h) * esc) + 2, int(x * esc):int((x + w) * esc) + 2] = np.nan
        acc.append(viva)
    cap.release()
    if len(acc) < 3:
        return None
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        col = np.nanmean(np.stack(acc), (0, 1)) if False else np.nanmean(np.nanmean(np.stack(acc), 0), 0)
    col = np.nan_to_num(col)
    ok = col > 0.30
    melhor, ini = (0, 0), None
    for i, v in enumerate(list(ok) + [False]):
        if v and ini is None:
            ini = i
        elif not v and ini is not None:
            if i - ini > melhor[1] - melhor[0]:
                melhor = (ini, i)
            ini = None
    a, b = melhor
    W = len(col)
    if b - a < W * 0.35 or (b - a) >= W * 0.92:
        return None
    a, b = a + 1, b - 1                      # 1 px de folga para dentro: tira o fio da borda
    x0, x1 = int(a / esc) // 2 * 2, int(b / esc) // 2 * 2
    # v1.0.12: faixas pretas também em cima/embaixo (CS 4:3 dentro de 16:9): corta para as linhas com imagem,
    # senão o painel ganha barra preta onde o jogo não existe
    y0, y1 = 0, sh
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            lin = np.nan_to_num(np.nanmean(np.nanmean(np.stack(acc)[:, :, a:b], 0), 1))
        okl = lin > 0.12
        mel, ini = (0, 0), None
        for i, v in enumerate(list(okl) + [False]):
            if v and ini is None:
                ini = i
            elif not v and ini is not None:
                if i - ini > mel[1] - mel[0]:
                    mel = (ini, i)
                ini = None
        if (mel[1] - mel[0]) >= len(lin) * 0.5 and (mel[1] - mel[0]) < len(lin) * 0.97:
            y0, y1 = int((mel[0] + 1) / esc) // 2 * 2, int((mel[1] - 1) / esc) // 2 * 2
    except Exception:
        y0, y1 = 0, sh
    return (x0, y0, x1 - x0, y1 - y0)


def janela_jogo_cheia(sw, sh, W, hj, cam=None):
    """v1.0.11: jogo em tela cheia (16:9 sem faixas pretas) preenche TODO o painel abaixo da faixa (sem barra preta):
    recorta uma janela com a proporção do painel (W:hj), na altura inteira, e a posiciona para FORA da caixa da cam
    (que já aparece em cima), o mais perto possível do centro do jogo. Se nenhuma posição exclui a cam sem perder o
    centro, devolve a posição central e cobrir=True (o filtro cobre a cam com o jogo ao lado).
    Devolve (x, y, w, h, cobrir) em px do vídeo."""
    ww = min(sw, int(sh * W / hj) // 2 * 2)       # arredonda para baixo: o jogo escalado nunca fica mais baixo que o painel
    pref = (sw - ww) // 2
    if not cam:
        return (pref // 2 * 2, 0, ww, sh, False)
    cx, cy, cw, ch = cam
    mg = max(32, int(0.11 * sw))     # a caixa medida é menor que a moldura real da cam (máscara/borda): margem larga
    c0, c1 = cx - mg, cx + cw + mg
    if pref + ww <= c0 or pref >= c1:             # já não toca a cam
        return (pref // 2 * 2, 0, ww, sh, False)
    ops = []
    if c1 + ww <= sw:
        ops.append(c1)                            # janela à direita da cam
    if c0 - ww >= 0:
        ops.append(c0 - ww)                       # janela à esquerda da cam
    if ops:
        x = min(ops, key=lambda v: abs(v - pref))
        if abs((x + ww / 2) - sw / 2) <= 0.30 * sw:   # não perde o centro do jogo
            return (int(x) // 2 * 2, 0, ww, sh, False)
    return (pref // 2 * 2, 0, ww, sh, True)


def faixa_arquivo(cor="vermelha", variacao=0):
    """PNG da faixa na cor pedida; as variações de rasgo (_v2, _v3...) alternam pelo número do corte."""
    arqs = sorted(FAIXAS.glob(f"faixa_rasgada_{cor}*.png"))
    if not arqs:
        return None
    return arqs[int(variacao or 0) % len(arqs)]


def geometria_faixa(m, sw, sh, compacta=False):
    """Onde a faixa fica no quadro final: cobre o blur entre a cam e o jogo (com folga), nunca
    menor que o PNG original. Devolve {y, h, centro, blur} (centro = onde a legenda fica)."""
    hc = m.get("altura_cam", 640)
    hj = m["altura"] - hc
    hg = _jogo_janela(m, sw, sh)[4]
    blur = max(0, (hj - hg) // 2)
    centro = hc + blur / 2
    a, b = FAIXA_OPACO
    if compacta and blur >= 120:
        # v1.0.7: jogo 4:3 (faixas pretas) deixa um vão grande preto. A faixa ESTICADA para cobrir o vão comia
        # o fundo da cam (o rosto, que costuma ficar colado embaixo, ficava sob o vermelho). Aqui ela fica no
        # tamanho original, colada logo abaixo da cam, e o resto do vão fica preto liso.
        y = hc - 4
        return {"y": y, "h": FAIXA_ALT, "centro": int(round(y + (a + b) / 2)), "blur": blur}
    h = max(FAIXA_ALT, -(-(blur + 2 * FAIXA_FOLGA) * FAIXA_ALT // (b - a)))
    h += h % 2
    y = max(0, int(round(centro - h * (a + b) / 2 / FAIXA_ALT)) - (10 if blur >= 60 else 0))  # rasgo não come o topo do jogo
    return {"y": y, "h": h, "centro": int(round(centro)), "blur": blur}


def _tapa_cam(m, bx, by, bw, bh, nw, nh, jx, jy):
    """Quando a janela do jogo foi achada pela imagem (jogo 4:3 com faixas pretas) e a cam da live fica por cima
    do canto dela, tapa de preto esse pedaço (mais a faixa de nome em cima da cam): no vídeo original ele já
    escondia o jogo, e no corte vertical a cam aparece em cima, então não deve repetir embaixo.
    v1.0.7: em vez de um bloco preto (aparecia como um quadradinho preto no canto do jogo), preenche com o próprio
    jogo em volta (delogo). A cam recebida já vem recortada para dentro da janela; o pedaço tapado cresce 8% para os
    lados/baixo e 22% para cima (faixa de nome) para cobrir a janela inteira da live."""
    if not (m.get("_jogo_auto") or m.get("_cobrir_cam")):
        return ""
    mx, mb = int(bw * 0.08), int(bh * 0.08)
    x0, y0 = max(bx - mx, jx), max(by - int(bh * 0.22), jy)
    x1, y1 = min(bx + bw + mx, jx + nw), min(by + bh + mb, jy + nh)
    if x1 - x0 < 4 or y1 - y0 < 4:
        return ""
    # v1.0.7: o delogo deixava uma mancha escura. Cobre o pedaço com a faixa de jogo logo ao lado (mesma altura),
    # desfocada de leve na emenda; se não houver espaço à direita, usa o lado esquerdo.
    pw, ph = x1 - x0, y1 - y0
    sx = x1 if x1 + pw <= jx + nw else max(jx, x0 - pw)
    if sx + pw > jx + nw or sx < jx:
        return ""
    return (f",split=2[g1][g2];[g2]crop={pw}:{ph}:{sx - jx}:{y0 - jy},boxblur=3:1[rem];"
            f"[g1][rem]overlay={x0 - jx}:{y0 - jy}")


def filtro_modelo(m, sw, sh):
    """Filtro do quadro vertical. Com m['_faixa'] = {modo, y, h, arquivo, cor}: 'rasgada' sobrepõe o
    PNG (entrada [1:v] do ffmpeg; sem arquivo, faixa lisa), 'nenhuma' tira o blur atrás do jogo."""
    W, H = m["largura"], m["altura"]
    t = m["tipo"]
    if t == "cam-em-cima":
        c = m["cam"]
        hc = m.get("altura_cam", 640)
        hj = H - hc
        fx = m.get("_faixa") or {}
        # com faixa (rasgada/nenhuma) as sobras em volta da cam e do jogo ficam pretas lisas;
        # só o modo "blur" (o antigo) mantém o fundo desfocado
        preto = fx.get("modo") in ("rasgada", "nenhuma")
        fundo_cam = "drawbox=c=black:t=fill" if preto else "boxblur=20:2,eq=brightness=-0.15"
        cx, cy, cw, ch = _px(c["x"], sw), _px(c["y"], sh), _px(c["w"], sw), _px(c["h"], sh)
        # cam: sub-recorte com a proporção do painel (W:hc), centrado no rosto quando há rosto
        q = m.get("cam_quadro")  # (x, y, w, h) relativo ao recorte da cam, calculado por enquadrar_cam
        pad = 0
        bx, by, bw, bh = cx, cy, cw, ch  # cam inteira (fundo desfocado quando precisa encolher)
        if q:
            cx, cy, cw, ch = cx + q[0], cy + q[1], q[2], q[3]
            pad = q[4] if len(q) > 4 else 0
        if pad:
            # pad > 0: cam menor e descida (folga acima da cabeça); pad < 0: cam menor colada em cima (rosto
            # ficaria sob a faixa da legenda)
            ih = hc - abs(pad)
            cam_f = (f"split=2[ca][cb];[ca]crop={bw}:{bh}:{bx}:{by},scale={W}:{hc}:force_original_aspect_ratio=increase,"
                     f"crop={W}:{hc},{fundo_cam}[cf];"
                     f"[cb]crop={cw}:{ch}:{cx}:{cy},scale=-2:{ih}:{ESCALA_CAM},{NITIDEZ_CAM}[ct];"
                     f"[cf][ct]overlay=(W-w)/2:{max(0, pad)}")
        elif not q:
            # Sem rosto detectado, encaixa a janela toda para preservar o topo da cabeça.
            cam_f = (f"crop={cw}:{ch}:{cx}:{cy},scale={W}:{hc}:force_original_aspect_ratio=decrease:"
                     f"force_divisible_by=2:{ESCALA_CAM},pad={W}:{hc}:(ow-iw)/2:(oh-ih)/2:black,{NITIDEZ_CAM}")
        else:
            cam_f = (f"crop={cw}:{ch}:{cx}:{cy},scale={W}:{hc}:force_original_aspect_ratio=increase:{ESCALA_CAM},"
                     f"crop={W}:{hc},{NITIDEZ_CAM}")
        # jogo: janela mais larga que o painel (mostra mais da ação), encaixada pela largura
        # sobre a mesma janela desfocada preenchendo o painel (sem faixa preta)
        nw, nh, jx, jy, _hg = _jogo_janela(m, sw, sh)
        # jogo 4:3 (faixas pretas achadas) não enche o painel: o resto vira o fundo desfocado do próprio jogo
        fundo = "drawbox=c=black:t=fill" if (preto and not m.get("_jogo_auto")) else "boxblur=24:2,eq=brightness=-0.12"
        base = (f"[0:v]split=3[a][b][c];"
                f"[a]{cam_f}[cam];"
                f"[b]crop={nw}:{nh}:{jx}:{jy},scale={W}:{hj}:force_original_aspect_ratio=increase,crop={W}:{hj},"
                f"{fundo}[jf];"
                f"[c]crop={nw}:{nh}:{jx}:{jy}{_tapa_cam(m, bx, by, bw, bh, nw, nh, jx, jy)},"
                f"scale={W}:-2:force_original_aspect_ratio=decrease[jt];"
                f"[jf][jt]overlay=(W-w)/2:(H-h)/2[jogo];")
        if fx.get("modo") == "rasgada" and fx.get("arquivo"):
            return (base + f"[cam][jogo]vstack=2[pilha];[1:v]scale={W}:{fx['h']}:flags=lanczos,format=rgba[fx];"
                    f"[pilha][fx]overlay=0:{fx['y']},setsar=1,format=yuv420p[v]")
        if fx.get("modo") == "rasgada":  # PNG faltando: faixa lisa na mesma posição (o corte não para)
            a, b = FAIXA_OPACO
            y, h = fx["y"] + fx["h"] * a // FAIXA_ALT, fx["h"] * (b - a) // FAIXA_ALT
            cor = FAIXA_COR_LISA.get(fx.get("cor"), FAIXA_COR_LISA["vermelha"])
            return base + f"[cam][jogo]vstack=2,drawbox=x=0:y={y}:w={W}:h={h}:c={cor}:t=fill,setsar=1,format=yuv420p[v]"
        return base + "[cam][jogo]vstack=2,setsar=1,format=yuv420p[v]"
    if t == "fundo-desfocado":
        z = m.get("zoom", 1.0)
        fw = _px(z, W)
        return (f"[0:v]split=2[a][b];"
                f"[a]scale=-2:{H},crop={W}:{H},boxblur={m.get('desfoque', 30)}:2,"
                f"eq=brightness={-(1 - m.get('escurecer', 0.55)) * 0.5:.3f}[fundo];"
                f"[b]scale={fw}:-2[fr];"
                f"[fundo][fr]overlay=(W-w)/2:(H-h)/2,setsar=1,format=yuv420p[v]")
    if t == "crop":
        cw = _px(sh * W / H / sw, sw)
        cx = min(max(0, int(m.get("centro_x", 0.5) * sw - cw / 2)), sw - cw) // 2 * 2
        return f"[0:v]crop={cw}:{sh}:{cx}:0,scale={W}:{H},setsar=1,format=yuv420p[v]"
    raise SystemExit(f"tipo de modelo desconhecido: {t}")


def tamanho(arquivo):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height", "-of", "csv=p=0", str(arquivo)],
                         capture_output=True, text=True).stdout
    nums = [int(x) for x in out.replace(",", " ").split() if x.isdigit()]
    return nums[0], nums[1]


def render_cru(segs, ini, fim, modelo, saida: Path, modo: hw.Modo):
    """Junta os .ts do trecho, recorta [ini, fim] e monta o vertical. Sem legenda."""
    entrada = "concat:" + "|".join(str(s.arquivo) for s in segs)
    sw, sh = tamanho(segs[0].arquivo)
    off = max(0.0, ini - segs[0].ini)
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
           "-ss", f"{off:.3f}", "-i", entrada, "-t", f"{fim - ini:.3f}",
           "-filter_complex", filtro_modelo(modelo, sw, sh), "-map", "[v]", "-map", "0:a:0",
           "-r", "30", *modo.args_encoder(), "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
           "-af", "aresample=async=1", "-movflags", "+faststart", str(saida)]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
    if r.returncode != 0:
        raise RuntimeError("ffmpeg (corte): " + r.stderr[-600:])


# --- legenda ---------------------------------------------------------------------------------
def _cor(hexa, alfa=0):
    h = hexa.lstrip("#")
    return f"&H{alfa:02X}{h[4:6]}{h[2:4]}{h[0:2]}".upper()


def _tempo(s):
    s = max(0.0, s)
    cs = int(round(s * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def _limpa(t, maius):
    t = t.replace("{", "").replace("}", "").replace("\\", "")
    return t.upper() if maius else t


SOLTAS = set("o a os as um uma uns umas de do da dos das no na nos nas em e ou que pra pro "
              "pras pros pelo pela por com sem se me te lhe eu tu ele ela é mas nem ao aos".split())


def grupos(palavras, n, max_chars=18):
    """Até n palavras e max_chars por bloco; quebra em pausa > 0,35 s e fim de frase; nunca
    fecha bloco em artigo/preposição solta (ela vai junto com a palavra seguinte)."""
    out, g = [], []
    chars = lambda xs: sum(len(x["w"]) for x in xs) + max(0, len(xs) - 1)
    for w in palavras:
        if g:
            pausa = w["s"] - g[-1]["e"] > 0.35 or g[-1]["w"][-1:] in ".?!"
            cheio = len(g) >= n or chars(g + [w]) > max_chars
            solta = g[-1]["w"].lower().strip(",.?!") in SOLTAS
            if (pausa or cheio) and not solta:
                out.append(g); g = []
            elif (pausa or cheio) and solta and len(g) > 1:
                k = len(g)  # leva as palavras soltas do fim (ex.: "e o") para o próximo bloco
                while k > 1 and g[k - 1]["w"].lower().strip(",.?!") in SOLTAS:
                    k -= 1
                out.append(g[:k]); g = g[k:]
        g.append(w)
    if g:
        out.append(g)
    return out


def gerar_ass(palavras, ini, fim, estilo, modelo, saida: Path):
    W, H = modelo["largura"], modelo["altura"]
    e = estilo
    ws = [dict(w, s=max(0.0, w["s"] - ini), e=min(fim - ini, w["e"] - ini)) for w in palavras
          if w["e"] > ini and w["s"] < fim]
    ws = [w for w in ws if w["e"] > w["s"]]
    cab = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Base,{e['fonte']},{e['tamanho']},{_cor(e['cor'])},{_cor(e['cor_destaque'])},{_cor(e['cor_contorno'])},{_cor('#000000', 0x60)},0,0,0,0,100,100,0,0,1,{e['contorno']},{e['sombra']},{e['alinhamento']},{modelo.get('legenda_margem_esq', 90)},{modelo.get('legenda_margem_dir', 160)},{modelo.get('legenda_margem_baixo', 300)},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    linhas = []
    pos = ""
    if modelo.get("tipo") == "cam-em-cima" and modelo.get("legenda_na_emenda", True):
        y = (modelo["_faixa"]["centro"] if (modelo.get("_faixa") or {}).get("modo") == "rasgada"
             else modelo.get("altura_cam", 640) + modelo.get("legenda_desvio_emenda", 40))
        pos = f"{{\\an5\\pos({W // 2},{y})}}"
    gs = grupos(ws, int(e.get("palavras_por_vez", 3)), int(e.get("max_caracteres", 18)))
    for k, g in enumerate(gs):
        g_ini = g[0]["s"]
        g_fim = gs[k + 1][0]["s"] if k + 1 < len(gs) and gs[k + 1][0]["s"] - g[-1]["e"] < 0.6 else g[-1]["e"] + 0.25
        g_fim = min(g_fim, fim - ini)
        txts = [_limpa(w["w"], e.get("maiusculas")) for w in g]
        if e["efeito"] == "karaoke":
            partes = []
            for i, w in enumerate(g):
                dur = int(round(((g[i + 1]["s"] if i + 1 < len(g) else w["e"]) - w["s"]) * 100))
                partes.append(f"{{\\k{max(1, dur)}}}{txts[i]}")
            # \k troca a palavra INTEIRA de cor no instante em que é falada (2c -> 1c), numa linha só.
            # O \kf antigo varria a cor por dentro da letra; com contorno grosso, a parte verde e a
            # branca pareciam duas camadas de texto desalinhadas ("VOO|U").
            linhas.append(f"Dialogue: 0,{_tempo(g_ini)},{_tempo(g_fim)},Base,,0,0,0,,{pos}"
                          f"{{\\1c{_cor(e['cor_destaque'])}\\2c{_cor(e['cor'])}}}" + " ".join(partes))
        elif e["efeito"] == "destaque":
            for i, w in enumerate(g):
                a = w["s"]
                b = g[i + 1]["s"] if i + 1 < len(g) else g_fim
                if b <= a:
                    continue
                pal = []
                for j, t in enumerate(txts):
                    if j == i:
                        sc = e.get("escala_destaque", 100)
                        pal.append(f"{{\\1c{_cor(e['cor_destaque'])}\\fscx{sc}\\fscy{sc}}}{t}"
                                   f"{{\\1c{_cor(e['cor'])}\\fscx100\\fscy100}}")
                    else:
                        pal.append(t)
                linhas.append(f"Dialogue: 0,{_tempo(a)},{_tempo(b)},Base,,0,0,0,,{pos}{' '.join(pal)}")
        else:
            linhas.append(f"Dialogue: 0,{_tempo(g_ini)},{_tempo(g_fim)},Base,,0,0,0,,{pos}{' '.join(txts)}")
    saida.write_text(cab + "\n".join(linhas) + "\n", encoding="utf-8")
    return len(ws)


def _esc(p: Path):
    return str(p).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def queimar(video: Path, ass: Path, saida: Path, modo: hw.Modo):
    vf = f"subtitles=filename='{_esc(ass)}':fontsdir='{_esc(FONTES)}'"
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", str(video), "-vf", vf,
           *modo.args_encoder(), "-c:a", "copy", "-movflags", "+faststart", str(saida)]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
    if r.returncode != 0:
        raise RuntimeError("ffmpeg (legenda): " + r.stderr[-600:])


# --- interface combinada: render_corte (um passe de ffmpeg por corte) ---------------------------
import os
import time

# Parâmetros pedidos por placa (testados de verdade com 1 s de encode antes de usar).
# Arquivo leve para baixar no celular e postar: H.264 High, 30 fps, ~6 Mbps (teto 6-6,5 Mbps), AAC 128k.
# Medido em 17/11/2026 contra o corte antigo (cq 19 sem teto, 60 fps, 22,7 Mbps, 91 MB em 32 s):
#   NVENC cq23 teto 6M      -> 6,1 Mbps, SSIM 0,991 (alanzoka) / 0,996 (GTA)
#   NVENC cq23 teto 8M 60fps-> 8,0 Mbps, SSIM 0,990: 60 fps no mesmo bitrate fica MENOS nítido -> 30 fps
#   x264 medium crf21 6,5M  -> 3,4-6,1 Mbps, SSIM 0,992 / 0,995 (CPU, para quem não tem placa)
TETO_FPS = 30
ENC_CORTE = {
    "h264_nvenc": ["-c:v", "h264_nvenc", "-preset", "p5", "-tune", "hq", "-profile:v", "high", "-rc", "vbr",
                   "-cq", "23", "-b:v", "5M", "-maxrate", "6M", "-bufsize", "12M", "-spatial-aq", "1",
                   "-temporal-aq", "1", "-rc-lookahead", "20", "-bf", "3"],
    # AMD/Intel: mesmo teto de bitrate (não medidos aqui por falta da placa; se o teste de 1 s falhar, cai no x264)
    "h264_amf": ["-c:v", "h264_amf", "-quality", "quality", "-profile:v", "high", "-rc", "vbr_peak",
                 "-b:v", "5M", "-maxrate", "6M", "-bufsize", "12M"],
    "h264_qsv": ["-c:v", "h264_qsv", "-profile:v", "high", "-global_quality", "23", "-b:v", "5M",
                 "-maxrate", "6M", "-bufsize", "12M"],
    "libx264": ["-c:v", "libx264", "-preset", "medium", "-profile:v", "high", "-crf", "21",
                "-maxrate", "6500k", "-bufsize", "13M"],
}
AUDIO_CORTE = ["-c:a", "aac", "-b:a", "128k", "-ar", "48000"]
_CACHE_ENC = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "cortelive" / "encoder.json"
_enc_memo = None


def _testa_enc(enc):
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-f", "lavfi", "-i",
           "testsrc2=s=1080x1920:r=30:d=1", *ENC_CORTE[enc], "-pix_fmt", "yuv420p", "-f", "null", "-"]
    try:
        return subprocess.run(cmd, capture_output=True, timeout=60).returncode == 0
    except Exception:
        return False


def encoder_corte(forcar: str | None = None):
    """Escolhe o encoder: CORTELIVE_ENCODER/forcar > cache > teste NVIDIA, AMD, Intel, CPU."""
    global _enc_memo
    forcar = forcar or os.environ.get("CORTELIVE_ENCODER")
    if forcar:
        if forcar not in ENC_CORTE:
            raise SystemExit(f"Encoder desconhecido: {forcar}. Opções: {', '.join(ENC_CORTE)}")
        return forcar
    if _enc_memo:
        return _enc_memo
    try:
        c = json.loads(_CACHE_ENC.read_text(encoding="utf-8"))
        if c.get("encoder") in ENC_CORTE and c.get("ffmpeg") == hw.ffmpeg_bin():
            _enc_memo = c["encoder"]
            return _enc_memo
    except Exception:
        pass
    vend = {v for v in (hw.vendor_de(p) for p in hw.placas()) if v}
    ordem = [e for v, e in (("nvidia", "h264_nvenc"), ("amd", "h264_amf"), ("intel", "h264_qsv")) if v in vend]
    ordem += [e for e in ("h264_nvenc", "h264_amf", "h264_qsv") if e not in ordem]  # placa não reconhecida pelo nome
    _enc_memo = next((e for e in ordem if _testa_enc(e)), "libx264")
    try:
        _CACHE_ENC.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_ENC.write_text(json.dumps({"encoder": _enc_memo, "ffmpeg": hw.ffmpeg_bin(),
                                          "quando": time.strftime("%Y-%m-%d %H:%M")}), encoding="utf-8")
    except Exception:
        pass
    return _enc_memo


def _probe(video):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height,r_frame_rate", "-of", "csv=p=0", str(video)],
                         capture_output=True, text=True).stdout.strip().split(",")
    n, d = (out[2].split("/") + ["1"])[:2]
    fps = float(n) / float(d or 1)
    tem_audio = bool(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
                                     "stream=index", "-of", "csv=p=0", str(video)],
                                    capture_output=True, text=True).stdout.strip())
    # fps da origem com teto de 30 (29,97/25/24 ficam como estão; 60 vira 30)
    fps_s = f"{n}/{d or 1}" if 0 < fps <= TETO_FPS else str(TETO_FPS)
    return int(out[0]), int(out[1]), fps_s, tem_audio


RECUO_PADRAO = ["jogo-fundo-desfocado", "jogo-tela-cheia-crop"]   # Fundo desfocado, depois Tela cheia


def _modelo_efetivo(nome, cam, sw, sh, recuo=None):
    """Cam-em-cima sem cam achada NUNCA sai: troca para o 1º modelo da lista `recuo` (config.toml `recuo_sem_cam`;
    padrão desfocado, depois tela cheia). Devolve (modelo, motivo_do_recuo ou False)."""
    m = carregar("modelo", nome)
    if m["tipo"] == "cam-em-cima":
        if cam is not None:
            x, y, w, h = cam
            m = dict(m, cam={"x": x / sw, "y": y / sh, "w": w / sw, "h": h / sh})
        else:
            for alt in (recuo or RECUO_PADRAO):
                try:
                    ma = carregar("modelo", alt)
                except Exception:
                    continue
                if ma.get("tipo") != "cam-em-cima":
                    return ma, f"sem câmera identificada: '{nome}' trocado por '{alt}'"
            raise RuntimeError("cam-em-cima sem câmera e nenhum modelo de recuo válido em recuo_sem_cam")
    return m, False


def enquadrar_cam(video, inicio, fim, m, sw, sh, visivel=None):
    """Acha o rosto (OpenCV Haar) em 5 quadros do trecho dentro do recorte da cam e devolve
    (x, y, w, h) relativo à cam: proporção do painel, rosto no terço superior com folga acima
    da cabeça, zoom no máximo 'cam_zoom_max' (padrão 1,3x) para não pixelar. Sem rosto: None."""
    try:
        import cv2
    except Exception:
        return None
    c = m["cam"]
    cx, cy, cw, ch = _px(c["x"], sw), _px(c["y"], sh), _px(c["w"], sw), _px(c["h"], sh)
    det = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    cap = cv2.VideoCapture(str(video))
    caras = []
    N = 12  # vários quadros: o streamer se mexe; o quadro final é fixo no corte todo (não treme)
    for k in range(N):
        cap.set(cv2.CAP_PROP_POS_MSEC, (inicio + (fim - inicio) * (k + 0.5) / N) * 1000)
        ok, f = cap.read()
        if not ok:
            continue
        g = cv2.cvtColor(f[cy:cy + ch, cx:cx + cw], cv2.COLOR_BGR2GRAY)
        r = det.detectMultiScale(g, 1.1, 5, minSize=(max(24, ch // 8),) * 2)
        if len(r):
            caras.append(max(r, key=lambda b: b[2] * b[3]))
    cap.release()
    if len(caras) < 3:  # rosto em menos de 3 de 12 quadros: falso positivo/sem streamer
        return None
    med = lambda xs: sorted(xs)[len(xs) // 2]
    fw = med([b[2] for b in caras])
    ccx = med([b[0] + b[2] / 2 for b in caras])
    # topo da cabeça (cabelo/fone) ~0,6 rosto acima da caixa do Haar; usa o ponto MAIS ALTO visto
    topo = max(0.0, min(b[1] - 0.6 * b[3] for b in caras))
    folga = m.get("cam_folga", 0.10)  # fração da altura do painel acima da cabeça
    A = m["largura"] / m.get("altura_cam", 640)
    h_max = min(ch, cw / A)
    # maior zoom (menor h) que ainda deixa a folga com y0 >= 0: y0 = topo - folga*h
    # queixo: a caixa do Haar acaba na boca/queixo; +10% de folga. Tem de ficar acima de 'visivel' (fração do
    # painel da cam que a faixa da legenda não cobre). Cam com o rosto colado embaixo (Gaules) caía sob a faixa.
    queixo = med([b[1] + b[3] * 1.05 for b in caras])
    vis = (visivel + 0.04) if visivel else 1.0   # o rasgo da faixa é meio transparente: tolera 4%
    y0_de = lambda hh: min(max(0.0, topo - folga * hh), ch - hh)
    h = h_max
    for z in (m.get("cam_zoom_max", 1.3), 1.2, 1.1, 1.0):
        hz = h_max / z
        if (topo - folga * hz >= 0 and min(b[1] + b[3] for b in caras) < hz * 0.85
                and queixo - y0_de(hz) <= vis * hz):
            h = hz
            break
    w = h * A
    y0 = y0_de(h)
    x0 = min(max(0, ccx - w / 2), cw - w)
    # sem altura para a folga: diminui a cam no painel e desce ela; o resto é a cam desfocada
    pad = 0
    hc = m.get("altura_cam", 640)
    if topo - folga * h < 0:
        pad = int(round((folga * h - topo) / h * hc))
    elif queixo - y0 > vis * h:
        # rosto ficaria sob a faixa mesmo sem zoom: diminui a cam no painel, colada em cima (pad negativo)
        ih = vis * hc * h / (queixo - y0)
        pad = -int(round(hc - ih))
    return tuple(int(v) // 2 * 2 for v in (x0, y0, w, h)) + (int(pad / 2) * 2,)


def render_corte(video: str, inicio: float, fim: float, palavras: list, modelo: str, legenda: str,
                 cam: tuple | None, saida_dir: str, nome: str, encoder: str | None = None,
                 faixa: str | None = None, faixa_cor: str | None = None, faixa_variacao: int | None = None, recuo_sem_cam: list | None = None) -> dict:
    """Corte preciso [inicio, fim] do vídeo -> vertical 1080x1920 com e sem legenda, num passe só.
    palavras: [{w, start, end}] em tempo absoluto do vídeo. cam: (x, y, w, h) em px do vídeo fonte.
    faixa: 'rasgada' (padrão) | 'blur' | 'nenhuma' — o que fica atrás da legenda no cam-em-cima.
    faixa_variacao: qual rasgo usar; sem valor, alterna pelo número do corte no nome (corte_003 -> 3)."""
    out = Path(saida_dir); out.mkdir(parents=True, exist_ok=True)
    sw, sh, fps, tem_audio = _probe(video)
    m, caiu = _modelo_efetivo(modelo, cam, sw, sh, recuo_sem_cam)
    if m["tipo"] == "cam-em-cima" and m.get("jogo_auto", True):
        try:
            jw_ = janela_do_jogo(video, inicio, fim, sw, sh, cam)
        except Exception:
            jw_ = None
        if jw_:
            m = dict(m, jogo={"x": jw_[0] / sw, "y": jw_[1] / sh, "w": jw_[2] / sw, "h": jw_[3] / sh}, jogo_vista=0.0, _jogo_auto=True)
    if m["tipo"] == "cam-em-cima" and not m.get("_jogo_auto") and cam is not None:
        W_, hj_ = m["largura"], m["altura"] - m.get("altura_cam", 640)
        jx_, jy_, jw2, jh2, cobrir = janela_jogo_cheia(sw, sh, W_, hj_, cam)
        m = dict(m, jogo={"x": jx_ / sw, "y": 0.0, "w": jw2 / sw, "h": 1.0}, jogo_vista=0.0, _jogo_cheio=True,
                 _cobrir_cam=cobrir)
    if m["tipo"] == "cam-em-cima" and m.get("enquadrar_rosto", True):
        visivel = None
        if (faixa or "rasgada").lower() not in ("blur", "nenhuma"):   # só a rasgada cobre a cam
            try:   # fração do painel da cam acima do topo da faixa (o rasgo branco também cobre), com 6 px de folga
                g = geometria_faixa(m, sw, sh)
                visivel = min(1.0, (g["y"] - 6) / m.get("altura_cam", 640))
            except Exception:
                visivel = None
        q = enquadrar_cam(video, inicio, fim, m, sw, sh, visivel)
        if q and len(q) > 4 and q[4] < 0 and visivel is not None and geometria_faixa(m, sw, sh)["blur"] >= 120:
            # rosto colado embaixo da cam ficaria sob a faixa esticada: tenta com a faixa compacta (cam na largura toda)
            vis2 = min(1.0, (geometria_faixa(m, sw, sh, True)["y"] - 6) / m.get("altura_cam", 640))
            q2 = enquadrar_cam(video, inicio, fim, m, sw, sh, vis2)
            if q2 and (len(q2) < 5 or q2[4] >= 0 or abs(q2[4]) < abs(q[4])):
                q, m = q2, dict(m, _faixa_compacta=True)
        if q:
            m = dict(m, cam_quadro=q)
    faixa = (faixa or "rasgada").lower()
    if faixa not in ("rasgada", "blur", "nenhuma"):
        faixa = "rasgada"
    arq_faixa = None
    if m["tipo"] == "cam-em-cima" and faixa != "blur":
        cor = (faixa_cor or "vermelha").lower()
        if faixa_variacao is None:
            n = re.search(r"\d+", nome)
            faixa_variacao = int(n.group()) - 1 if n else 0
        arq_faixa = faixa_arquivo(cor, faixa_variacao) if faixa == "rasgada" else None
        m = dict(m, _faixa={"modo": faixa, "cor": cor, "arquivo": str(arq_faixa) if arq_faixa else None,
                            **geometria_faixa(m, sw, sh, bool(m.get("_faixa_compacta")))})
    est = carregar("legenda", legenda)
    ass = out / f"{nome}.ass"
    pal = [{"w": p["w"], "s": float(p["start"]), "e": float(p["end"])} for p in palavras]
    mp4 = out / f"{nome}.mp4"
    aviso = None
    try:
        gerar_ass(pal, inicio, fim, est, m, ass)
    except Exception as e:
        aviso = f"A legenda deste corte não pôde ser gerada ({e}); o vídeo foi salvo sem legenda."
    enc = encoder_corte(encoder)
    base_fc = filtro_modelo(m, sw, sh)[:-3] + f",fps={fps}"
    aud = ["-map", "0:a:0", *AUDIO_CORTE] if tem_audio else []

    def _ffmpeg(fc, e_):
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
               "-ss", f"{inicio:.3f}", "-t", f"{fim - inicio:.3f}", "-i", str(video),
               *(["-i", str(arq_faixa)] if arq_faixa else []), "-filter_complex", fc,
               "-map", "[v]", *ENC_CORTE[e_], "-pix_fmt", "yuv420p", *aud, "-movflags", "+faststart", str(mp4)]
        return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")

    t0 = time.time()
    # A GPU é dividida com outros processos (transcrição etc.): o NVENC às vezes sai sem mensagem.
    # Tenta de novo uma vez e, se falhar, faz o corte no CPU em vez de perder o corte.
    # Sai UM arquivo por corte, já com a legenda. Se a legenda falhar, o mesmo arquivo sai sem
    # legenda (e o aviso explica), para não perder o corte.
    tentativas = [enc, enc] + (["libx264"] if enc != "libx264" else [])
    etapas = []
    if aviso is None:
        etapas.append((base_fc + f",subtitles=filename='{_esc(ass)}':fontsdir='{_esc(FONTES)}'[v]", True))
    etapas.append((base_fc + "[v]", False))
    erro = ""
    for fc, com_legenda in etapas:
        for e_ in tentativas:
            r = _ffmpeg(fc, e_)
            if r.returncode == 0:
                enc = e_
                break
            erro = f"ffmpeg (render_corte, {e_}, código {r.returncode}): " + r.stderr[-800:]
        else:
            if com_legenda:
                aviso = "A legenda não pôde ser aplicada neste corte; o vídeo foi salvo sem legenda."
            continue
        break
    else:
        raise RuntimeError(erro)
    if aviso:
        ass.unlink(missing_ok=True)
    return {"mp4": str(mp4), "mp4_sem_legenda": None, "ass": None if aviso else str(ass), "encoder": enc,
            "legenda_ok": aviso is None, "aviso": aviso,
            "modelo": m["tipo"], "caiu_sem_cam": caiu,
            "faixa": (m.get("_faixa") or {}).get("modo", "blur" if m["tipo"] == "cam-em-cima" else None),
            "faixa_arquivo": arq_faixa.name if arq_faixa else None, "segundos": round(time.time() - t0, 1)}
