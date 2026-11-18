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


MODELO_YUNET = Path(__file__).parent / "modelos_cam" / "face_detection_yunet_2023mar.onnx"
_YUNET = {}


def _yunet(img, limiar):
    """Rostos (x, y, w, h) pelo YuNet (cv2.FaceDetectorYN, ONNX de ~230 KB do opencv_zoo, roda em CPU). Bem mais
    sensível que o Haar: acha rosto de lado, olhando para baixo, sob neon e de pele escura. None se não der."""
    import cv2
    if not hasattr(cv2, "FaceDetectorYN") or not MODELO_YUNET.exists():
        return None
    _sem_opencl()
    h, w = img.shape[:2]
    esc = min(1.0, 1280 / w)
    im = cv2.resize(img, (int(w * esc), int(h * esc))) if esc < 1 else img
    chave = (im.shape[1], im.shape[0], limiar)
    det = _YUNET.get(chave)
    if det is None:
        det = _YUNET[chave] = cv2.FaceDetectorYN.create(str(MODELO_YUNET), "", (im.shape[1], im.shape[0]), limiar, 0.3, 300)
    _, f = det.detect(im)
    if f is None:
        return []
    out = []
    for r in f:
        x, y, fw, fh = (int(round(float(v) / esc)) for v in r[:4])
        x, y = max(0, x), max(0, y)
        if fw >= int(h * 0.025) and fh >= int(h * 0.025):
            out.append((x, y, min(fw, w - x), min(fh, h - y)))
    return out


def rostos(img):
    r = None
    try:
        r = _yunet(img, 0.6)
    except Exception:
        r = None
    if r is not None:
        return r
    import cv2
    _sem_opencl()
    casc = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    h = img.shape[0]
    return casc.detectMultiScale(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), 1.1, 6,
                                 minSize=(int(h * 0.04), int(h * 0.04)), maxSize=(int(h * 0.3), int(h * 0.3)))


def rostos_sensivel(img):
    try:
        r = _yunet(img, 0.4)
        if r is not None:
            return r
    except Exception:
        pass
    return _rostos_haar_sensivel(img)


def _rostos_haar_sensivel(img):
    """2ª passada (só quando a normal não achou rosto fixo): detector alt2 + padrão menos exigente. Pega a pessoa
    olhando para baixo ou virada (ovotz 18/11/2026: 16 de 74 quadros sem rosto na passada normal)."""
    import cv2
    _sem_opencl()
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    h = img.shape[0]
    m = (int(h * 0.04), int(h * 0.04)), (int(h * 0.3), int(h * 0.3))
    out = []
    for nome in ("haarcascade_frontalface_alt2.xml", "haarcascade_frontalface_default.xml"):
        casc = cv2.CascadeClassifier(cv2.data.haarcascades + nome)
        out += [tuple(int(v) for v in f) for f in casc.detectMultiScale(g, 1.05, 3, minSize=m[0], maxSize=m[1])]
    return out


def caixa_estimada(cx, cy, fw, fh, w, h):
    """Caixa em volta do rosto (1,6:1) quando não se acha a moldura: o rosto ocupa ~60% da altura da cam. Cola nas bordas
    da tela quando fica a menos de 4% delas."""
    ch = fh / 0.62
    cw = ch * 1.6
    x, y = cx - cw / 2, cy - ch * 0.55
    x, y = min(max(0, x), w - cw), min(max(0, y), h - ch)
    if x < 0.04 * w:
        x = 0
    if x + cw > 0.96 * w:
        x = w - cw
    if y < 0.04 * h:
        y = 0
    if y + ch > 0.96 * h:
        y = h - ch
    return (int(x) // 2 * 2, int(y) // 2 * 2, int(cw) // 2 * 2, int(ch) // 2 * 2)


def caixa_da_cam(face, w, h):
    """Caixa em volta do rosto (usada só no marcar-cam como sugestão)."""
    fx, fy, fw, fh = face
    cw, ch = int(fw * 6.3), int(fh * 3.8)
    x, y = max(0, fx + fw // 2 - cw // 2), max(0, fy + fh // 2 - ch // 2)
    return (x // 2 * 2, y // 2 * 2, min(cw, w - x) // 2 * 2, min(ch, h - y) // 2 * 2)


def _rosto_parado(imgs, f):
    """True se o 'rosto' f=(x,y,w,h) é um desenho fixo do jogo (ícone, retrato do HUD): a imagem ali fica idêntica em
    todos os quadros (diferença média < 2,5 de 255). Rosto de pessoa em câmera tem ruído e movimento (>= 10 medido
    no liviomateus; o ícone falso deu 1,3)."""
    x, y, fw, fh = [int(v) for v in f]
    ps = [i[max(0, y):y + fh, max(0, x):x + fw].astype(np.float32) for i in imgs]
    ps = [p for p in ps if p.size and p.shape == ps[0].shape]
    if len(ps) < 3:
        return False
    return max(float(np.abs(a - b).mean()) for a in ps for b in ps) < 2.5


def _pele(p):
    """Fração do recorte com tom de pele (YCrCb). Rosto de pessoa >= 0,38 em todas as lives medidas; ícone/retrato
    do HUD (barra de habilidades do WoW 0,03) e arma de inventário ficam perto de 0."""
    import cv2
    if p.size == 0:
        return 0.0
    y = cv2.cvtColor(p, cv2.COLOR_BGR2YCrCb)
    m = (y[..., 1] >= 133) & (y[..., 1] <= 173) & (y[..., 2] >= 77) & (y[..., 2] <= 127)
    return float(m.mean())


PELE_MIN = 0.15
_OLHOS = []


def _olhos(p):
    """True se o detector de olhos acha olho na metade de cima do recorte. Salva o rosto sob luz colorida forte
    (neon magenta/azul tira o tom de pele: mira_irl 18/11/2026 deu 0 de pele, mas tem olhos)."""
    import cv2
    if not _OLHOS:
        _OLHOS.append(cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_eye.xml"))
    if p.size == 0 or p.shape[0] < 24:
        return False
    g = cv2.cvtColor(p[: int(p.shape[0] * 0.6)], cv2.COLOR_BGR2GRAY)
    return len(_OLHOS[0].detectMultiScale(g, 1.1, 3, minSize=(max(8, p.shape[1] // 10),) * 2)) > 0


def _parece_rosto(p):
    return _pele(p) >= PELE_MIN or _olhos(p)


def _na_borda(cam, w, h, tol=0.15):
    """Webcam de streamer fica encostada numa borda/canto da tela (medido: <= 6% nas 14 lives). Janela solta no meio
    do jogo (inventário, painel) não é câmera."""
    x, y, cw, ch = cam
    return min(x / w, (w - x - cw) / w, y / h, (h - y - ch) / h) <= tol


def _agrupar(achados, w, h, tx, ty):
    todos = [f for lista in achados for f in lista]
    melhor, votos = None, 0
    for f in todos:
        cx, cy = f[0] + f[2] / 2, f[1] + f[3] / 2
        v = sum(1 for lista in achados if any(abs(g[0] + g[2] / 2 - cx) < tx * w and
                                               abs(g[1] + g[3] / 2 - cy) < ty * h for g in lista))
        if v > votos:
            melhor, votos = f, v
    if not melhor or votos < max(2, len(achados) // 2):
        return None
    cx, cy = melhor[0] + melhor[2] / 2, melhor[1] + melhor[3] / 2
    mesmos = [g for lista in achados for g in lista
              if abs(g[0] + g[2] / 2 - cx) < tx * w and abs(g[1] + g[3] / 2 - cy) < ty * h]
    return (float(np.median([g[0] + g[2] / 2 for g in mesmos])), float(np.median([g[1] + g[3] / 2 for g in mesmos])),
            float(np.median([g[2] for g in mesmos])), float(np.median([g[3] for g in mesmos])), votos)


def _filtrar(achados, imgs, tx=0.03, ty=0.04, minimo=0.2):
    """Tira 'rostos' desenhados no jogo (parados) e os grupos que não parecem rosto. A decisão é por GRUPO de
    aparições no mesmo ponto (a luz muda quadro a quadro; um grupo vale se >= 20% das aparições têm tom de pele ou
    olhos: ícone da barra de habilidades 0%, pessoa sob neon passa pelos olhos)."""
    h, w = imgs[0].shape[:2]
    fixos = {f for lista in achados for f in lista if _rosto_parado(imgs, f)}
    achados = [[f for f in lista if f not in fixos] for lista in achados]
    grupos = []
    for k, lista in enumerate(achados):
        for f in lista:
            cx, cy = f[0] + f[2] / 2, f[1] + f[3] / 2
            for g in grupos:
                if abs(g[0] - cx) < tx * w and abs(g[1] - cy) < ty * h:
                    g[2].append((k, f))
                    break
            else:
                grupos.append([cx, cy, [(k, f)]])
    ruins = set()
    for _, _, ms in (grupos if minimo > 0 else []):
        ok = sum(1 for k, f in ms if _parece_rosto(imgs[k][max(0, f[1]):f[1] + f[3], max(0, f[0]):f[0] + f[2]]))
        if ok < minimo * len(ms):
            ruins |= {(k, f) for k, f in ms}
    return [[f for f in lista if (k, f) not in ruins] for k, lista in enumerate(achados)]


def _rosto_estavel(achados, w, h, imgs=None, minimo=0.2):
    """O rosto que aparece no mesmo ponto em >= metade dos quadros. -> (cx, cy, fw, fh, votos) ou None.
    Com imgs: descarta 'rostos' desenhados no jogo (idênticos em todos os quadros) e o que não parece rosto antes de
    votar, e tenta de novo com tolerância maior (pessoa se mexe na cam; liviomateus 18/11/2026: o ícone parado ganhou
    da pessoa)."""
    if imgs is not None:
        achados = _filtrar(achados, imgs, minimo=minimo)
    return _agrupar(achados, w, h, 0.03, 0.04) or _agrupar(achados, w, h, 0.10, 0.12)


def _semente(r, esc=1.0):
    """Rosto do YuNet -> 'rosto' quadrado do tamanho que a busca da moldura foi calibrada (Haar, ~altura do YuNet)."""
    cx, cy, fw, fh, v = r
    lado = max(fw, fh * 0.95) * esc
    return (cx, cy, lado, lado, v)


def _tamanho_ok(cam, w, h):
    """Webcam de verdade tem >= 10% da largura e da altura da tela (menor medida: 11,7% x 12,2%). Caixa menor é
    ícone/logo com rosto (oatley 18/11/2026)."""
    return cam is not None and cam[2] >= 0.10 * w and cam[3] >= 0.10 * h


def _minimo():
    """Com YuNet não precisa do teste de pele/olhos (o detector já devolve só rostos); com Haar precisa."""
    import cv2
    return 0.0 if (MODELO_YUNET.exists() and hasattr(cv2, "FaceDetectorYN")) else 0.2


def _candidatos(lista, w, h, tx=0.05, ty=0.06, n=4):
    """Grupos de rosto que aparecem no mesmo ponto em >= metade dos quadros (>= 3): (cx, cy, fw, fh, votos),
    os melhores primeiro (votos x tamanho do rosto)."""
    grupos = []
    for k, l in enumerate(lista):
        for f in l:
            cx, cy = f[0] + f[2] / 2, f[1] + f[3] / 2
            for g in grupos:
                # rosto grande se mexe mais (cam em tela cheia): tolerância cresce com o tamanho do rosto
                if abs(g["cx"] - cx) < max(tx * w, 0.8 * g["fw"]) and abs(g["cy"] - cy) < max(ty * h, 0.8 * g["fh"]):
                    g["m"].append((k, f))
                    break
            else:
                grupos.append({"cx": cx, "cy": cy, "fw": f[2], "fh": f[3], "m": [(k, f)]})
    out = []
    for g in grupos:
        v = len({k for k, _ in g["m"]})
        if v < max(3, len(lista) // 2):
            continue
        ms = [f for _, f in g["m"]]
        out.append((float(np.median([f[0] + f[2] / 2 for f in ms])), float(np.median([f[1] + f[3] / 2 for f in ms])),
                    float(np.median([f[2] for f in ms])), float(np.median([f[3] for f in ms])), v))
    out.sort(key=lambda r: -r[4] * r[2])
    return out[:n]


def _mexe(lista, cand):
    """Quanto o rosto do grupo se mexe entre quadros (amplitude de x + de y, em larguras de rosto). Pessoa de
    verdade ~0,3 ou mais; logo/avatar animado parado ~0,15 (oatley)."""
    cx, cy, fw, fh, _ = cand
    xs, ys = [], []
    for l in lista:
        melhor = None
        for f in l:
            fx, fy = f[0] + f[2] / 2, f[1] + f[3] / 2
            d = abs(fx - cx) + abs(fy - cy)
            if abs(fx - cx) < max(0.05 * 1280, 0.8 * fw) and (melhor is None or d < melhor[0]):
                melhor = (d, fx, fy)
        if melhor:
            xs.append(melhor[1])
            ys.append(melhor[2])
    if len(xs) < 2:
        return 0.0
    return ((max(xs) - min(xs)) + (max(ys) - min(ys))) / max(fw, 1)


def _solo_cam(cands, w, h, n_quadros):
    """Layout 'só cam' (Just Chatting): rosto GRANDE (>= 13% da altura), no meio da tela (20-80% da largura), em
    >= 62% dos quadros. Aí a câmera é a tela inteira. Cam de canto fica no canto (junitombl/leokaos/bokaofc: fora do
    meio) e com rosto de 5-15% da altura."""
    for cx, cy, fw, fh, v in cands:
        if fh >= 0.13 * h and 0.2 * w <= cx <= 0.8 * w and v >= max(4, n_quadros * 5 // 8):
            return (0, 0, w // 2 * 2, h // 2 * 2)
    return None


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
    if not b4:
        # a janela pode acabar logo no queixo (Gaules: borda 0,5 rosto abaixo do centro); começar em 0,7 pula a
        # borda e a busca chega ao fim da tela. Só nesse caso tenta de novo começando mais perto do rosto.
        baixo2, b4b, n4b = _borda(plin, llin, int(cy + fh * 0.4), H, 1, k)
        if b4b:
            baixo, b4, n4 = baixo2, b4b, n4b
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


def _moldura(imgs, rosto):
    """Reserva para jogo parado/escuro (stepbr12: Minecraft numa caverna — o jogo quase não mexe e o contraste
    parado x em movimento some). Procura o retângulo em volta do rosto cujos 4 lados são linhas retas presentes em
    TODOS os quadros, ao longo do lado inteiro. Vence o de lados mais completos (pior lado); empate -> maior.
    -> ((x, y, w, h) em px da imagem original, nota do pior lado) ou None."""
    import cv2
    h, w = imgs[0].shape[:2]
    esc = LARG_ANALISE / w
    pq = [cv2.GaussianBlur(cv2.cvtColor(cv2.resize(i, (LARG_ANALISE, int(round(h * esc)))), cv2.COLOR_BGR2GRAY),
                           (5, 5), 0).astype(np.float32) for i in imgs]
    H, W = pq[0].shape
    gx = (np.min(np.stack([np.abs(cv2.Sobel(p, cv2.CV_32F, 1, 0, ksize=3)) for p in pq]), 0) > 18).astype(np.uint8)
    gy = (np.min(np.stack([np.abs(cv2.Sobel(p, cv2.CV_32F, 0, 1, ksize=3)) for p in pq]), 0) > 18).astype(np.uint8)
    gx = cv2.dilate(gx, np.ones((1, 3), np.uint8))        # tolerância de 1 px
    gy = cv2.dilate(gy, np.ones((3, 1), np.uint8))
    sx = np.vstack([np.zeros((1, W)), np.cumsum(gx, 0)])  # sx[y, x] = linhas de borda na coluna x até y
    sy = np.hstack([np.zeros((H, 1)), np.cumsum(gy, 1)])  # sy[y, x] = colunas de borda na linha y até x
    cx, cy, fw, fh = (v * esc for v in rosto[:4])
    col = lambda x, t, b: (sx[b, x] - sx[t, x]) / max(1, b - t)
    lin = lambda y, l, r: (sy[y, r] - sy[y, l]) / max(1, r - l)

    def picos(perfil, a, b):
        a, b = max(0, int(a)), min(len(perfil), int(b))
        ix = [i for i in range(a, b) if perfil[i] >= 0.35 and perfil[i] == perfil[max(a, i - 2):min(b, i + 3)].max()]
        return sorted(ix, key=lambda i: -perfil[i])[:14]

    ya, yb = int(max(0, cy - fh)), int(min(H, cy + fh))
    xa, xb = int(max(0, cx - fw)), int(min(W, cx + fw))
    pc, pl = gx[ya:yb].mean(0), gy[:, xa:xb].mean(1)
    esqs = set(picos(pc, 0, cx - fw * 0.7) + [0])
    dirs = set(picos(pc, cx + fw * 0.7, W) + [W - 1])
    cimas = set(picos(pl, 0, cy - fh * 0.7) + [0])
    baixos = set(picos(pl, cy + fh * 0.6, H) + [H - 1])
    melhor = None
    for L in esqs:
        for R in dirs:
            if not fw * 2.2 <= R - L <= 0.5 * W:
                continue
            for T in cimas:
                for B in baixos:
                    if not fh * 1.8 <= B - T <= 0.6 * H:
                        continue
                    # lado encostado na borda da tela vale como achado
                    lados = [1.0 if L <= 1 else col(L, T, B), 1.0 if R >= W - 2 else col(R, T, B),
                             1.0 if T <= 1 else lin(T, L, R), 1.0 if B >= H - 2 else lin(B, L, R)]
                    chave = (round(min(lados), 1), (R - L) * (B - T))
                    if melhor is None or chave > melhor[0]:
                        melhor = (chave, [L, T, R, B], min(lados))
    if not melhor or melhor[2] < 0.55:
        return None
    L, T, R, B = melhor[1]
    # encostado na tela, mas a moldura de verdade está a poucos px: usa a moldura
    perto = lambda cands, f: max(cands, key=f) if cands and f(max(cands, key=f)) >= 0.8 else None
    if L <= 1:
        L = perto(range(2, int(0.04 * W)), lambda x: col(x, T, B)) or L
    if R >= W - 2:
        R = perto(range(W - int(0.04 * W), W - 2), lambda x: col(x, T, B)) or R
    if T <= 1:
        T = perto(range(2, int(0.04 * H)), lambda y: lin(y, L, R)) or T
    if B >= H - 2:
        B = perto(range(H - int(0.04 * H), H - 2), lambda y: lin(y, L, R)) or B
    x0, y0 = (0 if L <= 1 else (L + 1) / esc), (0 if T <= 1 else (T + 1) / esc)
    x1, y1 = (w if R >= W - 2 else R / esc), (h if B >= H - 2 else B / esc)
    return (int(x0), int(y0), int(x1 - x0), int(y1 - y0)), round(float(melhor[2]), 2)


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


def faixa_topo(imgs, cam, rosto):
    """Faixa de cor lisa colada em cima da cam (nome/relógio, ex.: a azul do Gaules). -> altura em px até
    logo abaixo dela, ou None se não houver. Acha a linha reta que existe em todos os quadros no 1/3 de cima
    da cam (acima do rosto) e exige que o trecho acima dela seja quase todo da mesma cor."""
    x, y, cw, ch = (int(v) for v in cam)
    cy, fh = rosto[1], rosto[3]
    xa, xb = x + int(cw * 0.05), x + int(cw * 0.95)
    lim = int(min(y + ch * 0.3, cy - fh * 0.8))
    if lim - y < 6 or xb - xa < 20:
        return None
    pilha = np.stack([i[y:lim, xa:xb].astype(np.float32) for i in imgs])          # quadros x linhas x col x cor
    linhas = pilha.mean(2)                                                           # cor média de cada linha
    salto = np.abs(np.diff(linhas, axis=1)).sum(2).min(0)                           # salto entre linhas, em todos
    r = int(np.argmax(salto))
    if salto[r] < 60 or r < 3:
        return None
    faixa = np.median(pilha[:, :r + 1], 0)                                          # trecho acima da linha
    cor = np.median(faixa.reshape(-1, 3), 0)
    lisa = float((np.abs(faixa - cor).sum(2) < 60).mean())
    ULTIMO["faixa_topo"] = {"px": r + 1, "salto": round(float(salto[r])), "lisa": round(lisa, 2)}
    if lisa < 0.6:
        return None
    return r + 1 + max(2, int(round(ch * 0.015)))       # + folga: o vídeo comprimido borra 1-2 linhas


def ajustar_proporcao(cam, rosto, w, h, prop, bordas=None, topo_px=None):
    """Recorta o retângulo da cam na proporção do painel final (largura/altura = prop), centrado no rosto
    na horizontal e com o rosto a ~48% da altura (espaço acima da cabeça). Nunca corta o rosto.
    topo_px: altura medida da faixa de nome em cima da cam (faixa_topo); sem ela, tira 14% por garantia."""
    x, y, cw, ch = cam
    # margem para dentro da borda detectada (~2,5%): a borda medida pode pegar 1-2 linhas do jogo
    y_orig, ch_orig = y, ch
    mx, my = cw * 0.025, ch * 0.025
    topo = topo_px if topo_px else ch * 0.14   # faixa de nome/relógio que algumas lives põem colada em cima da cam
    if not topo_px:
        # v1.0.7: sem faixa medida, o corte de 14% às cegas comia o cabelo (stepbr12). Só corta o que sobra acima da
        # cabeça (topo do cabelo ~0,75 rosto acima do centro) deixando meio rosto de folga.
        cabeca = rosto[1] - rosto[3] * 0.75
        topo = min(topo, max(ch * 0.02, cabeca - y - rosto[3] * 0.5))
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
    mini = _minimo()
    achados = [[tuple(int(v) for v in f) for f in rostos(i)] for i in imgs]
    cands = _candidatos(_filtrar(achados, imgs, minimo=mini), w, h)
    if not cands:
        achados2 = [[tuple(int(v) for v in f) for f in rostos_sensivel(i)] for i in imgs]
        cands = _candidatos(_filtrar(achados2, imgs, minimo=mini), w, h)
        if cands:
            ULTIMO["rosto_sensivel"] = True
    if not cands:
        ULTIMO["motivo"] = "nenhum rosto fixo"
        return None
    ULTIMO["candidatos"] = [[round(v) for v in c] for c in cands]
    # layout só cam: rosto grande no meio da tela -> a câmera é a tela inteira
    solo = _solo_cam(cands, w, h, len(imgs))
    if solo:
        ULTIMO["solo_cam"] = True
        ULTIMO["final"] = solo
        return solo
    ULTIMO["mexe"] = round(_mexe(achados, cands[0]), 2)
    if ULTIMO["mexe"] < 0.12:     # logo/avatar do jogo: parado demais (oatley 0,07; pessoa real >= 0,16)
        ULTIMO["motivo"] = "rosto parado demais (logo/avatar)"
        return None
    # cam de canto: moldura em volta do rosto estável (os 3 melhores grupos, um por vez)
    for rosto in cands[:3]:
        for esc in (1.0, 1.4, 1.8):      # a busca da moldura depende do tamanho do rosto-semente: tenta 3
            ULTIMO.pop("motivo", None)
            cam = _cam_do_rosto(imgs, _semente(rosto, esc), w, h, prop)
            if cam and not _tamanho_ok(cam, w, h):
                ULTIMO["motivo"] = "caixa pequena demais"
                cam = None
            if cam:
                ULTIMO["semente_esc"] = esc
                return cam
    # sem moldura reconhecível: rosto muito estável encostado numa lateral -> caixa estimada em volta do rosto
    cx, cy, fw, fh, votos = cands[0]
    n = len(imgs)
    if votos >= max(5, n * 3 // 4) and (cx <= 0.3 * w or cx >= 0.7 * w) and fh >= 0.035 * h and fh <= 0.3 * h:
        x, y, cw, ch = caixa_estimada(cx, cy, fw, fh, w, h)
        if cw * ch <= 0.3 * w * h:
            ULTIMO["cam_estimada"] = (x, y, cw, ch)
            ULTIMO.pop("motivo", None)
            final = ajustar_proporcao((x, y, cw, ch), cands[0], w, h, prop, None, None)
            if _tamanho_ok((x, y, cw, ch), w, h):
                ULTIMO["final"] = final
                return final
            ULTIMO["motivo"] = "caixa pequena demais"
            return None
    ULTIMO["motivo"] = "overlay incerto"
    return None


def _cam_do_rosto(imgs, rosto, w, h, prop):
    ULTIMO["rosto"] = [round(v) for v in rosto]
    cam, bordas, _ = _overlay(imgs, rosto)
    ULTIMO["overlay"] = cam
    ULTIMO["bordas_achadas"] = bordas
    x, y, cw, ch = cam
    cx, cy, fw, fh, votos = rosto
    # bordas: cada lado tem de ter achado o fim da janela OU estar encostado na borda da tela
    encostado = [x <= 4, x + cw >= w - 4, y <= 4, y + ch >= h - 4]
    lados_ok = all(b or e for b, e in zip(bordas, [encostado[0], encostado[1], encostado[2], encostado[3]]))
    plausivel = (cw <= 0.5 * w and ch <= 0.6 * h and cw * ch <= 0.3 * w * h and cw >= fw * 2.2 and ch >= fh * 1.8
                 and x + fw * 0.3 < cx - fw / 2 and cx + fw / 2 < x + cw - fw * 0.3 and y <= cy - fh / 2)
    ULTIMO.update(lados_ok=lados_ok, plausivel=plausivel, votos=votos)
    def _plaus(c):
        x_, y_, w_, h_ = c
        return (w_ <= 0.5 * w and h_ <= 0.6 * h and w_ * h_ <= 0.3 * w * h and w_ >= fw * 2.2 and h_ >= fh * 1.8
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
        # 2ª reserva: jogo parado/escuro em volta da cam (contraste de movimento some): moldura reta nos 4 lados
        try:
            mold = _moldura(imgs, rosto)
            if mold and _plaus(mold[0]):
                cam, bordas, plausivel, lados_ok = mold[0], [True] * 4, True, True
                ULTIMO["moldura"] = list(mold)
        except Exception:
            pass
    if not (lados_ok and plausivel):
        # 3ª tentativa: o fundo do quarto é cheio de detalhe parado; agrupa as bordas paradas em volta do rosto
        cam = _cluster_parado(imgs, rosto)
        ULTIMO["overlay_2"] = cam
        if cam:
            x, y, cw, ch = cam
            plausivel = (cw <= 0.5 * w and ch <= 0.6 * h and cw * ch <= 0.3 * w * h and cw >= fw * 2.2 and ch >= fh * 1.8
                         and x + fw * 0.3 < cx - fw / 2 and cx + fw / 2 < x + cw - fw * 0.3 and y <= cy - fh / 2)
        if not cam or not plausivel:
            ULTIMO["motivo"] = "overlay incerto"
            return None
    try:
        topo_px = faixa_topo(imgs, cam, rosto)
    except Exception:
        topo_px = None
    final = ajustar_proporcao(cam, rosto, w, h, prop, bordas if "overlay_2" not in ULTIMO else None, topo_px)
    ULTIMO["final"] = final
    return final
