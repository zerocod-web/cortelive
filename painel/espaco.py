"""Aviso de espaço em disco do painel (v1.0.17): quanto está livre, quanto dá para liberar e o botão de limpar.

Dá para liberar (só coisas do CorteLive, nada fora da pasta de cortes):
- lixeira: cortes que o painel mandou para a Lixeira do Windows ("Não quero", excluir) e ainda estão lá.
  Limpar apaga DE VEZ só esses itens (os outros itens da Lixeira não são tocados).
- originais: trechos originais guardados para o "Ajustar foco" (_interno/*.mkv) com mais de N dias
  (originais_dias, padrão 7) ou cujo corte já não existe. Sem eles, o "Ajustar foco" desses cortes só funciona
  se a live ainda estiver no ar/VOD.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

from cortelive import disco

_PS = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"

# Lista (ou apaga) os itens da Lixeira que vieram de dentro da pasta de cortes. Saída: JSON [{caminho, bytes}].
_LIXEIRA = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$raiz = [IO.Path]::GetFullPath($env:CORTELIVE_PASTA).TrimEnd('\') + '\'
$apagar = $env:CORTELIVE_APAGAR -eq '1'
$out = @()
foreach ($item in (New-Object -ComObject Shell.Application).NameSpace(10).Items()) {
    $de = [string]$item.ExtendedProperty('System.Recycle.DeletedFrom')
    if (-not $de) { continue }
    if (-not (($de.TrimEnd('\') + '\').StartsWith($raiz, [StringComparison]::OrdinalIgnoreCase))) { continue }
    $r = $item.Path
    $tam = [int64]$item.ExtendedProperty('System.Size')
    if ($apagar) {
        $i = Join-Path (Split-Path $r) ('$I' + (Split-Path $r -Leaf).Substring(2))
        Remove-Item -LiteralPath $r -Recurse -Force -ErrorAction SilentlyContinue
        if (Test-Path -LiteralPath $i) { Remove-Item -LiteralPath $i -Force -ErrorAction SilentlyContinue }
        if (Test-Path -LiteralPath $r) { continue }
    }
    $out += [pscustomobject]@{ nome = [string]$item.Name; bytes = $tam }
}
ConvertTo-Json -InputObject @($out) -Compress
"""


def _lixeira(pasta, apagar=False) -> list[dict]:
    if os.name != "nt":
        return []
    try:
        r = subprocess.run([str(_PS), "-NoProfile", "-NonInteractive", "-STA", "-Command", _LIXEIRA],
                           env={**os.environ, "CORTELIVE_PASTA": str(pasta), "CORTELIVE_APAGAR": "1" if apagar else "0"},
                           stdin=subprocess.DEVNULL, capture_output=True, encoding="utf-8", errors="replace",
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=60)
        itens = json.loads(r.stdout.strip() or "[]")
        return itens if isinstance(itens, list) else [itens]
    except (OSError, ValueError, subprocess.SubprocessError):
        return []


def originais(pasta, dias, agora=None) -> list[Path]:
    """Trechos originais (_interno/*.mkv) antigos ou sem corte. Nunca toca no .buffer de uma live em andamento."""
    agora = time.time() if agora is None else agora
    pasta = Path(pasta)
    achados = []
    if not pasta.is_dir():
        return achados
    for mkv in pasta.glob("*/_interno/*.mkv"):
        try:
            velho = agora - mkv.stat().st_mtime > dias * 86400
        except OSError:
            continue
        sem_corte = not (mkv.parent.parent / f"{mkv.stem}.mp4").is_file()
        if velho or sem_corte:
            achados.append(mkv)
    return achados


def _soma(arqs) -> int:
    total = 0
    for a in arqs:
        try:
            total += a.stat().st_size
        except OSError:
            pass
    return total


def _dias(cfg) -> float:
    try:
        return max(0.0, float(cfg.get("originais_dias", 7)))
    except (TypeError, ValueError):
        return 7.0


def estado(pasta, cfg) -> dict:
    nivel, livre, frase = disco.avaliar(pasta, cfg)
    lix = _lixeira(pasta)
    orig = originais(pasta, _dias(cfg))
    return {
        "nivel": nivel, "livre_gb": round(livre, 2), "frase": frase,
        "aviso_gb": disco.limites(cfg)[0],
        "lixeira": {"itens": len(lix), "bytes": sum(int(i.get("bytes") or 0) for i in lix)},
        "originais": {"itens": len(orig), "bytes": _soma(orig), "dias": _dias(cfg)},
    }


def limpar(pasta, cfg, corpo) -> dict:
    livre_antes = disco.livre_gb(pasta)
    apagados = 0
    if (corpo or {}).get("originais", True):
        for mkv in originais(pasta, _dias(cfg)):
            try:
                mkv.unlink()
                apagados += 1
            except OSError:
                pass
    if (corpo or {}).get("lixeira", True):
        _lixeira(pasta, apagar=True)
    novo = estado(pasta, cfg)
    liberado = max(0.0, novo["livre_gb"] - livre_antes)
    txt = f"{liberado:.1f}".replace(".", ",")
    return {**novo, "liberado_gb": round(liberado, 2), "mensagem": f"Pronto: {txt} GB liberados."}
