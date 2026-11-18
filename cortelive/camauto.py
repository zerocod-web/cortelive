"""Câmera do streamer sem ninguém marcar.

Como acha a janelinha da cam (overlay):
1. Pega 6–8 quadros espalhados em 1–2 min da live.
2. Rosto: o mesmo rosto no mesmo lugar em pelo menos metade dos quadros (personagem do jogo se mexe).
3. Mapa de movimento: variação de cada pixel entre os quadros. O fundo do quarto do streamer quase não muda;
   o jogo muda muito (câmera andando). A janelinha é a região PARADA em volta do rosto.
4. A partir do rosto, anda para cada lado até a coluna/linha onde a faixa deixa de ser parada
   (ou até a borda da tela). Isso dá o retângulo do overlay.
5. Valida: tamanho plausível, rosto inteiro dentro e longe das bordas, retângulo estável (bordas nítidas).
   Confiança baixa -> None -> o render usa o layout sem cam (jogo-fundo-desfocado) em vez de um crop errado.
6. Ajusta o retângulo à proporção do painel da cam no vídeo final, centrado no rosto, para o rosto nunca
   ser cortado nas laterais e sobrar espaço acima da cabeça.

Guarda por canal em %LOCALAPPDATA%\\cortelive\\cams.json.
"""
import json
import os
import subprocess
from pathlib import Path

import numpy as np

ARQ = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "cortelive" / "cams.json"
ULTIMO = {}          # diagnóstico da última detecção (vai para o log)


def lidas():
    try:
        return json.loads(ARQ.read_text(encoding="utf-8"))
    except Exception:
        return {}


def salvar(canal, cam, origem):
    d = lidas()
    d[canal.lower()] = {"cam": list(cam) if cam else None, "origem": origem}
    ARQ.parent.mkdir(parents=True, exist_ok=True)
    ARQ.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


def da_memoria(canal):
    return lidas().get(canal.lower())


def _sem_opencl():
    """O OpenCV usa OpenCL na placa por padrão; com a GPU cheia (jogo + transcrição) isso derrubou o processo
    inteiro (crash nativo em nvopencl64.dll, 17/11/2026). Aqui só precisamos de CPU."""
    import cv2
    try:
        cv2.ocl.setUseOpenCL(False)
    except Exception:
        pass


def _quadro(arquivo):
    _sem_opencl()
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-nostdin", "-i", str(arquivo), "-frames:v", "1",
                          "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True, timeout=30).stdout
    import cv2
    return cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR) if raw else None


def rostos(img):
    import cv2
    _sem_opencl()
    casc = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    h = img.shape[0]
    return casc.detectMultiScale(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), 1.1, 6,
                                 minSize=(int(h * 0.04), int(h * 0.04)), maxSize=(int(h * 0.3), int(h * 0.3)))


def caixa_da_cam(face, w, h):
    """Caixa em volta do rosto (usada só no marcar-cam como sugestão)."""
    fx, fy, fw, fh = face
    cw, ch = int(fw * 6.3), int(fh * 3.8)
    x, y = max(0, fx + fw // 2 - cw // 2), max(0, fy + fh // 2 - ch // 2)
    return (x // 2 * 2, y // 2 * 2, min(cw, w - x) // 2 * 2, min(ch, h - y) // 2 * 2)


def _rosto_estavel(achados, w, h):
    """O rosto que aparece no mesmo ponto em >= metade dos quadros. -> (cx, cy, fw, fh, votos) ou None."""
    todos = [f for lista in achados for f in lista]
    melhor, votos = None, 0
    for f in todos:
        cx, cy = f[0] + f[2] / 2, f[1] + f[3] / 2
        v = sum(1 for lista in achados if any(abs(g[0] + g[2] / 2 - cx) < 0.03 * w and
                                               abs(g[1] + g[3] / 2 - cy) < 0.04 * h for g in lista))
        if v > votos:
            melhor, votos = f, v
    if not melhor or votos < max(2, len(achados) // 2):
        return None
    cx, cy = melhor[0] + melhor[2] / 2, melhor[1] + melhor[3] / 2
    mesmos = [g for lista in achados for g in lista
              if abs(g[0] + g[2] / 2 - cx) < 0.03 * w and abs(g[1] + g[3] / 2 - cy) < 0.04 * h]
    return (float(np.median([g[0] + g[2] / 2 for g in mesmos])), float(np.median([g[1] + g[3] / 2 for g in mesmos])),
            float(np.median([g[2] for g in mesmos])), float(np.median([g[3] for g in mesmos])), votos)


LARG_ANALISE = 640   # largura da análise de movimento (3 px de erro na borda numa live 1920)


def _borda(perfil, linha, ini, fim, passo, k):
    """Melhor posição da borda da janelinha andando do rosto (ini) para fora até 'fim' (borda da tela).
    perfil[i] = fração parada da faixa na posição i (nan = só corpo do streamer, ignora); linha[i] = moldura
    reta persistente. A borda é onde DENTRO (lado do rosto) é parado e FORA mexe: maior contraste vence.
    -> (primeira posição FORA da janela, achou, contraste)"""
    n = len(perfil)
    melhor, nota = None, -1.0
    i = ini
    while 0 <= i < n and (i != fim):
        dentro = [perfil[j] for j in range(i - passo * k, i, passo) if 0 <= j < n]
        fora = [perfil[j] for j in range(i, i + passo * k, passo) if 0 <= j < n]
        dentro = [v for v in dentro if v == v]
        fora_ok = [v for v in fora if v == v]
        if len(dentro) >= k // 2:
            md = float(np.mean(dentro))
            mf = float(np.mean(fora_ok)) if fora_ok else md
            # fora da tela conta como neutro (0,3): janela encostada na borda da tela ainda vence se for parada
            if len(fora) < k:
                mf = (mf * len(fora) + 0.3 * (k - len(fora))) / k
            s = md - mf + 0.4 * max(linha(i), linha(i - passo))
            if s > nota:
                melhor, nota = i, s
        i += passo
    # janela encostada na borda da tela: a "posição fora" é a própria borda
    borda_tela = n if passo > 0 else -1
    if melhor is None or nota < 0.3:
        return borda_tela, False, nota
    return melhor, True, nota


def _overlay(imgs, rosto):
    """Retângulo da janelinha da cam em volta do rosto (px da imagem original), achado por contraste
    parado (dentro) x em movimento (fora) em cada lado, mais as molduras retas que ficam em todos os quadros.
    O corpo do streamer (que se mexe) é ignorado, não contado como parado."""
    import cv2
    h, w = imgs[0].shape[:2]
    esc = LARG_ANALISE / w
    pq = [cv2.GaussianBlur(cv2.cvtColor(cv2.resize(i, (LARG_ANALISE, int(round(h * esc)))), cv2.COLOR_BGR2GRAY),
                           (5, 5), 0).astype(np.float32) for i in imgs]
    pilha = np.stack(pq)
    mov = np.median(np.abs(pilha - np.median(pilha, 0)), 0)
    parado = (mov < 6.0).astype(np.float32)
    H, W = parado.shape
    cx, cy, fw, fh, _ = rosto
    cx, cy, fw, fh = cx * esc, cy * esc, fw * esc, fh * esc
    y0f, y1f = int(max(0, cy - fh * 0.9)), int(min(H, cy + fh * 2.5))
    x0f, x1f = int(max(0, cx - fw * 1.1)), int(min(W, cx + fw * 1.1))
    parado[y0f:y1f, x0f:x1f] = np.nan          # corpo do streamer: nem parado nem em movimento
    gx = np.min(np.stack([np.abs(cv2.Sobel(p, cv2.CV_32F, 1, 0, ksize=3)) for p in pq]), 0) > 18
    gy = np.min(np.stack([np.abs(cv2.Sobel(p, cv2.CV_32F, 0, 1, ksize=3)) for p in pq]), 0) > 18

    def media(a, eixo):
        with np.errstate(all="ignore"):
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                return np.nanmean(a, eixo)

    # faixas de medida: só dentro da altura/largura do rosto (onde a janelinha com certeza está)
    ya, yb = int(max(0, cy - fh * 0.6)), int(min(H, cy + fh * 1.2))
    xa, xb = int(max(0, cx - fw * 1.5)), int(min(W, cx + fw * 1.5))
    pcol = media(parado[ya:yb, :], 0)
    plin = media(parado[:, xa:xb], 1)
    ya2, yb2 = int(max(0, cy - fh * 0.9)), int(min(H, cy + fh * 1.4))
    xa2, xb2 = int(max(0, cx - fw * 2.0)), int(min(W, cx + fw * 2.0))
    lcol = lambda x: float(gx[ya2:yb2, max(0, x - 1):x + 2].max(1).mean()) if 0 <= x < W else 0.0
    llin = lambda y: float(gy[max(0, y - 1):y + 2, xa2:xb2].max(0).mean()) if 0 <= y < H else 0.0
    k = max(4, int(fw * 0.35))
    esq, b1, n1 = _borda(pcol, lcol, int(cx - fw * 0.6), -1, -1, k)
    dire, b2, n2 = _borda(pcol, lcol, int(cx + fw * 0.6), W, 1, k)
    cima, b3, n3 = _borda(plin, llin, int(cy - fh * 0.7), -1, -1, k)
    baixo, b4, n4 = _borda(plin, llin, int(cy + fh * 0.7), H, 1, k)
    # lado sem borda por contraste (painel parado do jogo encostado na cam): usa a moldura reta mais forte perto do rosto
    if not b2 and dire >= W - 1:
        cand = [(lcol(x), x) for x in range(int(cx + fw * 0.7), min(W - 2, int(cx + fw * 2.4)))]
        if cand and max(cand)[0] >= 0.6:
            dire, b2 = max(cand)[1] + 1, True
    if not b1 and esq <= 0:
        cand = [(lcol(x), x) for x in range(max(1, int(cx - fw * 2.4)), int(cx - fw * 0.7))]
        if cand and max(cand)[0] >= 0.6:
            esq, b1 = max(cand)[1], True
    x0, x1 = (esq + 1) / esc, dire / esc
    y0, y1 = (cima + 1) / esc, baixo / esc
    x0, y0 = (0.0 if x0 < 3 / esc else x0), (0.0 if y0 < 3 / esc else y0)
    x1, y1 = min(w, x1), min(h, y1)
    ULTIMO["contraste"] = [round(v, 2) for v in (n1, n2, n3, n4)]
    return (int(x0), int(y0), int(x1 - x0), int(y1 - y0)), [b1, b2, b3, b4], mov


def _overlay_antigo(imgs, rosto):
    """Versão anterior (caminhada gulosa). Mantida só para comparação nos testes de QA."""
    import cv2
    h, w = imgs[0].shape[:2]
    esc = 480 / w
    pq = [cv2.GaussianBlur(cv2.cvtColor(cv2.resize(i, (480, int(h * esc))), cv2.COLOR_BGR2GRAY), (5, 5), 0)
          .astype(np.float32) for i in imgs]
    pilha = np.stack(pq)
    mov = np.median(np.abs(pilha - np.median(pilha, 0)), 0)     # quanto cada pixel muda entre os quadros
    parado = mov < 6.0
    H, W = parado.shape
    cx, cy, fw, fh, _ = rosto
    cx, cy, fw, fh = cx * esc, cy * esc, fw * esc, fh * esc
    # o rosto/corpo se mexe: não conta como "não parado"
    y0f, y1f = int(max(0, cy - fh * 0.9)), int(min(H, cy + fh * 2.5))
    x0f, x1f = int(max(0, cx - fw * 1.2)), int(min(W, cx + fw * 1.2))
    parado[y0f:y1f, x0f:x1f] = True

    # linha reta persistente (moldura da janelinha): borda presente em TODOS os quadros, na faixa inteira
    gx = np.min(np.stack([np.abs(cv2.Sobel(p, cv2.CV_32F, 1, 0, ksize=3)) for p in pq]), 0) > 18
    gy = np.min(np.stack([np.abs(cv2.Sobel(p, cv2.CV_32F, 0, 1, ksize=3)) for p in pq]), 0) > 18

    def andar(perfil, ini, passo, fim, linha=None):
        """Anda a partir do rosto até a faixa ficar 'em movimento' por 3 posições seguidas
        ou até cruzar uma linha reta persistente (moldura)."""
        ruins, i, ultimo_bom = 0, ini, ini
        while 0 <= i < fim if passo > 0 else i >= 0:
            if linha is not None and linha(i) >= 0.85:
                return i, True
            if perfil(i) < 0.55:
                ruins += 1
                if ruins >= 3:
                    return ultimo_bom, True
            else:
                ruins, ultimo_bom = 0, i
            i += passo
        return (fim if passo > 0 else 0), False      # chegou na borda da tela

    ya, yb = int(max(0, cy - fh)), int(min(H, cy + fh * 1.5))   # faixa vertical para medir colunas
    xa, xb = int(max(0, cx - fw * 1.5)), int(min(W, cx + fw * 1.5))
    col = lambda x: float(parado[ya:yb, x].mean())
    lin = lambda y: float(parado[y, xa:xb].mean())
    # a moldura atravessa a faixa inteira: mede a linha vertical ao longo de toda a altura provável da janela
    ya2, yb2 = int(max(0, cy - fh * 1.2)), int(min(H, cy + fh * 1.6))
    xa2, xb2 = int(max(0, cx - fw * 2.5)), int(min(W, cx + fw * 2.5))
    lcol = lambda x: float(gx[ya2:yb2, max(0, x - 1):x + 2].max(1).mean())
    llin = lambda y: float(gy[max(0, y - 1):y + 2, xa2:xb2].max(0).mean())
    esq, b1 = andar(col, int(cx - fw), -1, W, lcol)
    dire, b2 = andar(col, int(cx + fw), 1, W, lcol)
    cima, b3 = andar(lin, int(cy - fh), -1, H, llin)
    baixo, b4 = andar(lin, int(cy + fh), 1, H, llin)
    # nitidez da borda: logo fora do retângulo tem de mexer bem mais que logo dentro
    nit = []
    for lado, achou in ((esq, b1), (dire, b2), (cima, b3), (baixo, b4)):
        nit.append(achou)
    x0, x1, y0, y1 = esq / esc, (dire + 1) / esc, cima / esc, (baixo + 1) / esc
    return (int(x0), int(y0), int(min(w, x1) - x0), int(min(h, y1) - y0)), nit, mov


def _cluster_parado(imgs, rosto):
    """Bordas que existem em todos os quadros, fechadas e ligadas ao rosto -> retângulo envolvente."""
    import cv2
    h, w = imgs[0].shape[:2]
    est = None
    for img in imgs:
        e = cv2.Canny(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), 60, 160) > 0
        est = e if est is None else (est & e)
    cx, cy, fw, fh = rosto[:4]
    k = max(5, int(fw) // 8) | 1
    mapa = cv2.morphologyEx(est.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((k, k), np.uint8))
    fx, fy = int(cx - fw / 2), int(cy - fh / 2)
    cv2.rectangle(mapa, (fx, fy), (fx + int(fw), fy + int(fh)), 1, -1)
    n, rot, st, _ = cv2.connectedComponentsWithStats(mapa, 8)
    r = rot[int(cy), int(cx)]
    if r == 0:
        return None
    x, y, cw, ch = (int(v) for v in st[r][:4])
    if x < fw * 0.6:
        cw, x = cw + x, 0
    if y < fh * 0.6:
        ch, y = ch + y, 0
    return (x, y, cw, ch)


def ajustar_proporcao(cam, rosto, w, h, prop, bordas=None):
    """Recorta o retângulo da cam na proporção do painel final (largura/altura = prop), centrado no rosto
    na horizontal e com o rosto a ~48% da altura (espaço acima da cabeça). Nunca corta o rosto."""
    x, y, cw, ch = cam
    # margem para dentro da borda detectada (~2,5%): a borda medida pode pegar 1-2 linhas do jogo
    y_orig, ch_orig = y, ch
    mx, my = cw * 0.025, ch * 0.025
    topo = ch * 0.14          # faixa de nome/relógio que algumas lives põem colada em cima da cam
    x, y, cw, ch = x + mx, y + topo, cw - 2 * mx, ch - topo - my
    cx, cy, fw, fh = rosto[:4]
    if cw / ch > prop:            # largo demais: corta nas laterais, centrado no rosto
        nw = ch * prop
        nx = min(max(cx - nw / 2, x), x + cw - nw)
        x, cw = nx, nw
    else:                         # alto demais: corta em cima/embaixo, rosto a 40% da altura
        nh = cw / prop
        ny = min(max(cy - nh * 0.48, y), y + ch - nh)
        if bordas:   # só um lado achado (o outro é chute): encosta no lado medido
            cima_ok, baixo_ok = bordas[2] or y_orig <= 4, bordas[3]   # chegar no fim da tela por baixo = não achou
            if cima_ok and not baixo_ok:
                ny = y
            elif baixo_ok and not cima_ok:
                ny = y + ch - nh
        y, ch = ny, nh
    return (int(x) // 2 * 2, int(y) // 2 * 2, int(cw) // 2 * 2, int(ch) // 2 * 2)


def detectar(arquivos, prop=1080 / 640):
    """arquivos: 6+ segmentos espalhados em 1–2 min. -> (x,y,w,h) ou None (confiança baixa)."""
    global ULTIMO
    imgs = [i for i in (_quadro(a) for a in arquivos) if i is not None]
    ULTIMO = {"quadros": len(imgs)}
    if len(imgs) < 3:
        ULTIMO["motivo"] = "poucos quadros"
        return None
    h, w = imgs[0].shape[:2]
    achados = [[tuple(int(v) for v in f) for f in rostos(i)] for i in imgs]
    rosto = _rosto_estavel(achados, w, h)
    if not rosto:
        ULTIMO["motivo"] = "nenhum rosto fixo"
        return None
    ULTIMO["rosto"] = [round(v) for v in rosto]
    cam, bordas, _ = _overlay(imgs, rosto)
    ULTIMO["overlay"] = cam
    ULTIMO["bordas_achadas"] = bordas
    x, y, cw, ch = cam
    cx, cy, fw, fh, votos = rosto
    # bordas: cada lado tem de ter achado o fim da janela OU estar encostado na borda da tela
    encostado = [x <= 4, x + cw >= w - 4, y <= 4, y + ch >= h - 4]
    lados_ok = all(b or e for b, e in zip(bordas, [encostado[0], encostado[1], encostado[2], encostado[3]]))
    plausivel = (cw <= 0.5 * w and ch <= 0.6 * h and cw >= fw * 2.2 and ch >= fh * 1.8
                 and x + fw * 0.3 < cx - fw / 2 and cx + fw / 2 < x + cw - fw * 0.3 and y <= cy - fh / 2)
    ULTIMO.update(lados_ok=lados_ok, plausivel=plausivel, votos=votos)
    def _plaus(c):
        x_, y_, w_, h_ = c
        return (w_ <= 0.5 * w and h_ <= 0.6 * h and w_ >= fw * 2.2 and h_ >= fh * 1.8
                and x_ + fw * 0.3 < cx - fw / 2 and cx + fw / 2 < x_ + w_ - fw * 0.3 and y_ <= cy - fh / 2)
    if not (lados_ok and plausivel):
        # 1ª reserva: quando o lado direito/esquerdo encosta num painel parado do jogo (HUD), o contraste some;
        # a caminhada antiga acha a moldura reta
        try:
            c_ant, b_ant, _ = _overlay_antigo(imgs, rosto)
            if all(b_ant) and _plaus(c_ant):
                cam, bordas, plausivel, lados_ok = c_ant, b_ant, True, True
                ULTIMO["overlay_antigo"] = c_ant
        except Exception:
            pass
    if not (lados_ok and plausivel):
        # 2ª tentativa: o fundo do quarto é cheio de detalhe parado; agrupa as bordas paradas em volta do rosto
        cam = _cluster_parado(imgs, rosto)
        ULTIMO["overlay_2"] = cam
        if cam:
            x, y, cw, ch = cam
            plausivel = (cw <= 0.5 * w and ch <= 0.6 * h and cw >= fw * 2.2 and ch >= fh * 1.8
                         and x + fw * 0.3 < cx - fw / 2 and cx + fw / 2 < x + cw - fw * 0.3 and y <= cy - fh / 2)
        if not cam or not plausivel:
            ULTIMO["motivo"] = "overlay incerto"
            return None
    final = ajustar_proporcao(cam, rosto, w, h, prop, bordas if "overlay_2" not in ULTIMO else None)
    ULTIMO["final"] = final
    return final
