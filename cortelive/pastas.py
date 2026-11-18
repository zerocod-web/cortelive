"""Onde cada arquivo de uma sessão mora e como se chama.

    cortes/
      2026-11-17 22h35 - alanzoka/                                  pasta da sessão (o que a pessoa abre)
        2026-11-17 22h36 - alanzoka - A coisa tá feíssima.mp4      pronto para postar (com legenda)
        sem legenda/2026-11-17 22h36 - alanzoka - A coisa ....mp4  mesmo corte sem legenda (CapCut etc.)
        _interno/   (oculta)                                        .json, .ass, log.txt, sessao.json,
                                                                    .autopost.jsonl, .buffer, temporários
      _autopost/    (oculta)                                        fila do autopost e anti-duplicado

Cortes no formato antigo (corte_001_000018.mp4 com .json/.ass ao lado, pasta 2026-11-17_1628_canal)
continuam sendo lidos. Para trazer uma pasta antiga para o formato novo:
    python -m cortelive.pastas migrar [cortes]          (mostra o que faria)
    python -m cortelive.pastas migrar [cortes] --aplicar
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

INTERNO = "_interno"
SEM_LEGENDA = "sem legenda"
MAX_NOME = 80                      # caracteres do nome do arquivo, sem a extensão
_PROIBIDOS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVADOS = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
_RE_NOVA = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}h\d{2} - (.+)$")
_RE_VELHA = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{4}_(.+)$")


# ------------------------------------------------------------------ nomes
# Cópia da lista do autopost (autopost/metadados.py): a pasta autopost/ não vai no pacote público,
# e o nome do arquivo tem que sair sem palavrão mesmo assim.
_PALAVROES = {"porra": "p*rra", "caralho": "c*ralho", "puta": "p*ta", "foda": "f*da", "fodase": "f*da-se",
              "merda": "m*rda", "cacete": "c*cete", "buceta": "b*ceta"}


def _suavizar(t: str) -> str:
    try:
        from .autopost.metadados import suavizar
        return suavizar(t)
    except Exception:
        import unicodedata

        def troca(m):
            w = m.group(0)
            k = "".join(c for c in unicodedata.normalize("NFKD", w.lower()) if not unicodedata.combining(c))
            s = _PALAVROES.get(k.replace("-", ""))
            if not s:
                return w
            return s.upper() if w.isupper() else (s[:1].upper() + s[1:] if w[:1].isupper() else s)
        return re.sub(r"\b[\w-]+\b", troca, t)


def _corta(t: str, n: int) -> str:
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) <= n:
        return t
    c = t[:n + 1].rsplit(" ", 1)[0]
    return c if c else t[:n]


def limpar(texto, maximo: int = MAX_NOME) -> str:
    """Texto -> pedaço de nome de arquivo válido no Windows: sem <>:"/\\|?*, sem palavrão, sem ponto/espaço
    no fim, no máximo `maximo` caracteres sem partir palavra."""
    t = _suavizar(str(texto or ""))
    t = re.sub(r"(?<=\w)\*(?=\w)", "∗", t)   # p*rra -> p∗rra (o * é proibido em nome de arquivo)
    t = _PROIBIDOS.sub(" ", t)
    t = _corta(t, maximo).rstrip(" .-—,;")
    if t.split(".")[0].upper() in _RESERVADOS:
        t = "_" + t
    return t


def carimbo(quando: float | None = None) -> str:
    return time.strftime("%Y-%m-%d %Hh%M", time.localtime(quando or time.time()))


def nome_sessao(canal: str, quando: float | None = None) -> str:
    return f"{carimbo(quando)} - {limpar(canal, 40) or 'live'}"


def nome_corte(canal: str, titulo: str | None, quando: float | None = None, reserva: str = "") -> str:
    """'2026-11-17 22h35 - alanzoka - A coisa tá feíssima' (sem extensão, até MAX_NOME caracteres)."""
    base = f"{carimbo(quando)} - {limpar(canal, 30) or 'live'}"
    folga = MAX_NOME - len(base) - 3
    tit = limpar(titulo, folga) if titulo and folga > 8 else ""
    if not tit and reserva:
        tit = limpar(reserva, max(8, folga))
    return f"{base} - {tit}" if tit else base


def unico(pasta: Path, nome: str, ext: str = ".mp4", tambem: tuple[Path, ...] = ()) -> str:
    """Nome que ainda não existe em `pasta` (nem nas pastas de `tambem`): 'x', 'x (2)', 'x (3)'…"""
    n, k = nome, 2
    while any((Path(p) / f"{n}{ext}").exists() for p in (pasta, *tambem)):
        n, k = f"{nome} ({k})", k + 1
    return n


def canal_da_pasta(nome_pasta: str) -> str:
    for rx in (_RE_NOVA, _RE_VELHA):
        m = rx.match(nome_pasta)
        if m:
            return m.group(1)
    return nome_pasta


# ------------------------------------------------------------------ pastas
def ocultar(p: Path) -> None:
    """Marca a pasta como oculta no Windows (no Linux/Mac o '_' no nome já basta)."""
    if os.name != "nt":
        return
    try:
        import ctypes
        k = ctypes.windll.kernel32
        k.GetFileAttributesW.restype = ctypes.c_uint32
        a = k.GetFileAttributesW(str(p))
        if a != 0xFFFFFFFF and not a & 0x2:
            k.SetFileAttributesW(str(p), a | 0x2)
    except Exception:
        pass


def interno(pasta_sessao: Path) -> Path:
    d = Path(pasta_sessao) / INTERNO
    d.mkdir(parents=True, exist_ok=True)
    ocultar(d)
    return d


def pasta_sessao_do_video(video: Path) -> Path:
    p = Path(video).parent
    return p.parent if p.name == SEM_LEGENDA else p


def interno_do_video(video: Path) -> Path:
    """Onde ficam os arquivos internos daquele corte (formato novo: _interno; antigo: ao lado do MP4)."""
    s = pasta_sessao_do_video(video)
    return s / INTERNO if (s / INTERNO).is_dir() else Path(video).parent


def _stem_base(video: Path) -> str:
    return re.sub(r"_sem_legenda$", "", Path(video).stem)


def meta_do_video(video) -> Path | None:
    """.json do corte (novo: _interno/<nome>.json; antigo: ao lado, também para o _sem_legenda)."""
    v = Path(video)
    st = _stem_base(v)
    for c in (pasta_sessao_do_video(v) / INTERNO / f"{st}.json", v.parent / f"{st}.json"):
        if c.is_file():
            return c
    return None


def ass_do_video(video) -> Path | None:
    v = Path(video)
    st = _stem_base(v)
    for c in (pasta_sessao_do_video(v) / INTERNO / f"{st}.ass", v.parent / f"{st}.ass"):
        if c.is_file():
            return c
    return None


def sessao_json_do_video(video) -> Path:
    s = pasta_sessao_do_video(Path(video))
    novo = s / INTERNO / "sessao.json"
    return novo if novo.is_file() else s / "sessao.json"


def log_autopost(video) -> Path:
    v = Path(video)
    return interno_do_video(v) / f"{v.stem}.autopost.jsonl"


# ------------------------------------------------------------------ migrador (formato antigo -> novo)
def _mover(a: Path, b: Path, feitos: list, aplicar: bool):
    if not a.exists():
        return
    feitos.append((a, b))
    if aplicar:
        b.parent.mkdir(parents=True, exist_ok=True)
        os.replace(a, b)


def migrar_sessao(pasta: Path, aplicar=False) -> dict:
    """Uma pasta 2026-11-17_1628_canal -> '2026-11-17 16h28 - canal' no formato novo."""
    pasta = Path(pasta)
    m = _RE_VELHA.match(pasta.name)
    if not m:
        return {"pasta": str(pasta), "pulada": "não está no formato antigo"}
    canal = m.group(1)
    data = time.mktime(time.strptime(pasta.name[:15], "%Y-%m-%d_%H%M"))
    destino = pasta.parent / unico(pasta.parent, nome_sessao(canal, data), ext="")
    feitos, mapa, usados = [], {}, set()
    dint = destino / INTERNO
    for js in sorted(pasta.glob("corte_*.json")):
        st = js.stem
        mp4 = pasta / f"{st}.mp4"
        try:
            info = json.loads(js.read_text(encoding="utf-8"))
        except Exception:
            info = {}
        quando = mp4.stat().st_mtime if mp4.exists() else data
        base = novo = nome_corte(canal, info.get("titulo_sugerido"), quando, reserva=st)
        k = 2
        while novo in usados or (destino / f"{novo}.mp4").exists():   # num teste sem aplicar, o disco não ajuda
            novo, k = f"{base} ({k})", k + 1
        usados.add(novo)
        _mover(mp4, destino / f"{novo}.mp4", feitos, aplicar)
        _mover(pasta / f"{st}_sem_legenda.mp4", destino / SEM_LEGENDA / f"{novo}.mp4", feitos, aplicar)
        _mover(pasta / f"{st}.ass", dint / f"{novo}.ass", feitos, aplicar)
        _mover(pasta / f"{st}.autopost.jsonl", dint / f"{novo}.autopost.jsonl", feitos, aplicar)
        if aplicar:
            info.update(arquivo=f"{novo}.mp4", sem_legenda=f"{SEM_LEGENDA}/{novo}.mp4", nome_interno=st,
                        legenda=f"{novo}.ass" if info.get("legenda") else None)
            dint.mkdir(parents=True, exist_ok=True)
            (dint / f"{novo}.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
            js.unlink()
        feitos.append((js, dint / f"{novo}.json"))
        mapa[str(mp4.resolve())] = str((destino / f"{novo}.mp4").resolve())
    for resto in ("log.txt", "sessao.json"):
        _mover(pasta / resto, dint / resto, feitos, aplicar)
    if aplicar:
        dint.mkdir(parents=True, exist_ok=True)
        ocultar(dint)
        for sobra in list(pasta.iterdir()):           # o que sobrou (.buffer etc.) vai para o interno
            os.replace(sobra, dint / sobra.name)
        pasta.rmdir()
    return {"pasta": str(pasta), "destino": str(destino), "movidos": len(feitos), "videos": mapa}


def migrar(raiz="cortes", aplicar=False) -> list[dict]:
    raiz = Path(raiz)
    out = [migrar_sessao(p, aplicar) for p in sorted(raiz.iterdir()) if p.is_dir() and _RE_VELHA.match(p.name)]
    mapa = {k: v for r in out for k, v in r.get("videos", {}).items()}
    if aplicar and mapa and (raiz / "_autopost" / "fila.json").exists():
        from .autopost.fila import Fila
        Fila(raiz).trocar_caminhos(mapa)
    return out


def main(argv=None):
    a = list(sys.argv[1:] if argv is None else argv)
    if not a or a[0] != "migrar":
        print(__doc__)
        return 2
    aplicar = "--aplicar" in a
    resto = [x for x in a[1:] if x != "--aplicar"]
    res = migrar(resto[0] if resto else "cortes", aplicar)
    for r in res:
        print(("MIGRADA " if aplicar else "migraria ") + f"{Path(r['pasta']).name} -> "
              f"{Path(r.get('destino', '')).name} ({r.get('movidos', 0)} arquivos)")
    if not aplicar:
        print("Nada foi mexido. Para aplicar: python -m cortelive.pastas migrar [cortes] --aplicar")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
