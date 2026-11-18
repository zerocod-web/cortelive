"""Render: trecho da live -> MP4 vertical 1080x1920 sem legenda -> .ass -> MP4 legendado (libass).

Modelos de corte e de legenda são arquivos JSON em presets\\modelo e presets\\legenda (editáveis;
um 4º estilo = copiar um arquivo). A ideia de agrupar palavras e destacar a ativa em ASS vem do
subtitles.py do OpenShorts (MIT); o código aqui é próprio.
"""
import json
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


def filtro_modelo(m, sw, sh):
    W, H = m["largura"], m["altura"]
    t = m["tipo"]
    if t == "cam-em-cima":
        c, j = m["cam"], m["jogo"]
        hc = m.get("altura_cam", 640)
        hj = H - hc
        cx, cy, cw, ch = _px(c["x"], sw), _px(c["y"], sh), _px(c["w"], sw), _px(c["h"], sh)
        # cam: sub-recorte com a proporção do painel (W:hc), centrado no rosto quando há rosto
        alvo_c = W / hc
        q = m.get("cam_quadro")  # (x, y, w, h) relativo ao recorte da cam, calculado por enquadrar_cam
        pad = 0
        bx, by, bw, bh = cx, cy, cw, ch  # cam inteira (fundo desfocado quando precisa encolher)
        if q:
            cx, cy, cw, ch = cx + q[0], cy + q[1], q[2], q[3]
            pad = q[4] if len(q) > 4 else 0
        if pad:
            ih = hc - pad
            cam_f = (f"split=2[ca][cb];[ca]crop={bw}:{bh}:{bx}:{by},scale={W}:{hc}:force_original_aspect_ratio=increase,"
                     f"crop={W}:{hc},boxblur=20:2,eq=brightness=-0.15[cf];"
                     f"[cb]crop={cw}:{ch}:{cx}:{cy},scale=-2:{ih}[ct];[cf][ct]overlay=(W-w)/2:{pad}")
        else:
            cam_f = f"crop={cw}:{ch}:{cx}:{cy},scale={W}:{hc}:force_original_aspect_ratio=increase,crop={W}:{hc}"
        # jogo: janela mais larga que o painel (mostra mais da ação), encaixada pela largura
        # sobre a mesma janela desfocada preenchendo o painel (sem faixa preta)
        jx, jy, jw, jh = _px(j["x"], sw), _px(j["y"], sh), _px(j["w"], sw), _px(j["h"], sh)
        vista = m.get("jogo_vista", 0.0)  # opcional: alarga a janela do jogo além do retângulo
        nw = min(sw, max(jw, _px(vista, sw)))
        nh = jh
        if nw / nh < W / hj:  # janela já mais estreita que o painel: crop puro
            nw = _px(nh * W / hj / sw, sw)
        jx = min(max(0, jx + (jw - nw) // 2), sw - nw) // 2 * 2
        jy = min(max(0, jy + (jh - nh) // 2), sh - nh) // 2 * 2
        return (f"[0:v]split=3[a][b][c];"
                f"[a]{cam_f}[cam];"
                f"[b]crop={nw}:{nh}:{jx}:{jy},scale={W}:{hj}:force_original_aspect_ratio=increase,crop={W}:{hj},"
                f"boxblur=24:2,eq=brightness=-0.12[jf];"
                f"[c]crop={nw}:{nh}:{jx}:{jy},scale={W}:-2:force_original_aspect_ratio=decrease[jt];"
                f"[jf][jt]overlay=(W-w)/2:(H-h)/2[jogo];"
                f"[cam][jogo]vstack=2,setsar=1,format=yuv420p[v]")
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
        pos = f"{{\\an5\\pos({W // 2},{modelo.get('altura_cam', 640) + modelo.get('legenda_desvio_emenda', 40)})}}"
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
ENC_CORTE = {
    "h264_nvenc": ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "19", "-b:v", "0"],
    "h264_amf": ["-c:v", "h264_amf", "-quality", "quality", "-rc", "cqp", "-qp_i", "19", "-qp_p", "21"],
    "h264_qsv": ["-c:v", "h264_qsv", "-global_quality", "20"],
    "libx264": ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"],
}
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
    return int(out[0]), int(out[1]), (60 if fps >= 50 else 30), tem_audio


def _modelo_efetivo(nome, cam, sw, sh):
    """cam-em-cima sem cam cai para o próximo modelo (jogo-fundo-desfocado)."""
    m = carregar("modelo", nome)
    if m["tipo"] == "cam-em-cima":
        if cam is None and not m.get("cam_fixa", False):
            return carregar("modelo", m.get("sem_cam", "jogo-fundo-desfocado")), True
        if cam is not None:
            x, y, w, h = cam
            m = dict(m, cam={"x": x / sw, "y": y / sh, "w": w / sw, "h": h / sh})
    return m, False


def enquadrar_cam(video, inicio, fim, m, sw, sh):
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
    h = h_max
    for z in (m.get("cam_zoom_max", 1.3), 1.2, 1.1, 1.0):
        hz = h_max / z
        if topo - folga * hz >= 0 and min(b[1] + b[3] for b in caras) < hz * 0.85:
            h = hz
            break
    w = h * A
    y0 = max(0.0, topo - folga * h)
    y0 = min(y0, ch - h)
    x0 = min(max(0, ccx - w / 2), cw - w)
    # sem altura para a folga: diminui a cam no painel e desce ela; o resto é a cam desfocada
    pad = 0
    if topo - folga * h < 0:
        hc = m.get("altura_cam", 640)
        pad = int(round((folga * h - topo) / h * hc))
    return tuple(int(v) // 2 * 2 for v in (x0, y0, w, h)) + (pad // 2 * 2,)


def render_corte(video: str, inicio: float, fim: float, palavras: list, modelo: str, legenda: str,
                 cam: tuple | None, saida_dir: str, nome: str, encoder: str | None = None) -> dict:
    """Corte preciso [inicio, fim] do vídeo -> vertical 1080x1920 com e sem legenda, num passe só.
    palavras: [{w, start, end}] em tempo absoluto do vídeo. cam: (x, y, w, h) em px do vídeo fonte."""
    out = Path(saida_dir); out.mkdir(parents=True, exist_ok=True)
    sw, sh, fps, tem_audio = _probe(video)
    m, caiu = _modelo_efetivo(modelo, cam, sw, sh)
    if m["tipo"] == "cam-em-cima" and m.get("enquadrar_rosto", True):
        q = enquadrar_cam(video, inicio, fim, m, sw, sh)
        if q:
            m = dict(m, cam_quadro=q)
    est = carregar("legenda", legenda)
    ass = out / f"{nome}.ass"
    pal = [{"w": p["w"], "s": float(p["start"]), "e": float(p["end"])} for p in palavras]
    gerar_ass(pal, inicio, fim, est, m, ass)
    mp4, cru = out / f"{nome}.mp4", out / f"{nome}_sem_legenda.mp4"
    enc = encoder_corte(encoder)
    fc = (filtro_modelo(m, sw, sh)[:-3] + f",fps={fps}[v];[v]split=2[v1][v2];"
          f"[v2]subtitles=filename='{_esc(ass)}':fontsdir='{_esc(FONTES)}'[vl]")
    aud = ["-map", "0:a:0", "-c:a", "aac", "-b:a", "160k", "-ar", "48000"] if tem_audio else []
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
           "-ss", f"{inicio:.3f}", "-t", f"{fim - inicio:.3f}", "-i", str(video), "-filter_complex", fc]
    base = cmd
    t0 = time.time()
    # A GPU é dividida com outros processos (transcrição etc.): o NVENC às vezes sai sem mensagem.
    # Tenta de novo uma vez e, se falhar, faz o corte no CPU em vez de perder o corte.
    tentativas = [enc, enc] + (["libx264"] if enc != "libx264" else [])
    for i, e_ in enumerate(tentativas):
        cmd = list(base)
        for rot, arq in (("[vl]", mp4), ("[v1]", cru)):
            cmd += ["-map", rot, *ENC_CORTE[e_], "-pix_fmt", "yuv420p", *aud, "-movflags", "+faststart", str(arq)]
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
        if r.returncode == 0:
            enc = e_
            break
        if i == len(tentativas) - 1:
            raise RuntimeError(f"ffmpeg (render_corte, {e_}, código {r.returncode}): " + r.stderr[-800:])
    return {"mp4": str(mp4), "mp4_sem_legenda": str(cru), "ass": str(ass), "encoder": enc,
            "modelo": m["tipo"], "caiu_sem_cam": caiu, "segundos": round(time.time() - t0, 1)}
