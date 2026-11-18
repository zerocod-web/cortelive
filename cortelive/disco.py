"""Espaço em disco da pasta de cortes e limpeza da gravação bruta de uma live.

- livre_gb(pasta): espaço livre (GB) do disco onde a pasta está. É o ponto único que os testes simulam.
- avaliar(pasta, cfg): "ok" | "aviso" | "critico" + frase pronta para o painel.
- limpar_bruto(pasta_sessao, interno): apaga só o que é bruto/temporário DENTRO da pasta daquela live
  (.buffer, trechos, pedidos/respostas do render, parciais de corte que não virou final).
  Nunca toca em .mp4 final, 'sem legenda', .json/.ass de corte, log.txt, sessao.json.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

GB = 1024 ** 3
# nomes que o render/sessão usam como temporário dentro de _interno: corte_001_000018_trecho.mkv,
# corte_001_000018.json (pedido), .res, .ass.tmp, parciais de render
_TEMP = re.compile(r"^corte_\d+_\d+.*$")


def livre_gb(pasta) -> float:
    """GB livres no disco da pasta (sobe até achar uma pasta que exista)."""
    p = Path(pasta).resolve()
    while not p.exists() and p.parent != p:
        p = p.parent
    return shutil.disk_usage(p).free / GB


def limites(cfg) -> tuple[float, float]:
    aviso = float(cfg.get("disco_aviso_gb", 10))
    critico = float(cfg.get("disco_critico_gb", 3))
    if critico > aviso:
        critico = aviso
    return aviso, critico


def avaliar(pasta, cfg):
    """(nivel, livre_gb, frase). nivel: 'ok' | 'aviso' | 'critico'. Desligável: disco_aviso_gb = 0."""
    aviso, critico = limites(cfg)
    livre = livre_gb(pasta)
    if aviso <= 0:
        return "ok", livre, ""
    txt = f"{livre:.1f}".replace(".", ",")
    if livre < critico:
        return "critico", livre, (f"Disco quase lotado: {txt} GB livres. Parei a gravação para não encher o disco; "
                                  f"seus cortes prontos estão salvos. Libere espaço e comece de novo.")
    if livre < aviso:
        return "aviso", livre, f"Pouco espaço no disco: {txt} GB livres."
    return "ok", livre, ""


def _tamanho(p: Path) -> int:
    try:
        if p.is_file():
            return p.stat().st_size
        return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
    except OSError:
        return 0


def limpar_bruto(pasta_sessao, interno) -> int:
    """Apaga o bruto/temporário da live. Devolve bytes liberados. Só mexe dentro de pasta_sessao."""
    raiz = Path(pasta_sessao).resolve()
    dint = Path(interno).resolve()
    liberados = 0
    if raiz not in dint.parents and dint != raiz:
        return 0                                  # _interno fora da pasta da live: não toca em nada
    alvos = [dint / ".buffer"]
    if dint.is_dir():
        alvos += [f for f in dint.iterdir() if f.is_file() and _TEMP.match(f.name)
                  and f.suffix.lower() in (".mkv", ".ts", ".res", ".tmp", ".part", ".partial", ".wav")]
        # pedido .json do render (corte_NNN_HHMMSS.json) é temporário; o .json do corte final tem nome legível
        alvos += [f for f in dint.glob("corte_*.json") if _TEMP.match(f.name)]
    for a in alvos:
        if a.is_file() and ((raiz / f"{a.stem}.mp4").exists() or (raiz / "sem legenda" / f"{a.stem}.mp4").exists()):
            continue                              # pertence a um corte pronto: intocável
        try:
            liberados += _tamanho(a)
            if a.is_dir():
                shutil.rmtree(a, ignore_errors=True)
            else:
                a.unlink(missing_ok=True)
        except OSError:
            pass
    return liberados
