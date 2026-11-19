"""Recicla um único MP4 e confirma que o Windows guardou o mesmo arquivo."""
import os
from pathlib import Path
import subprocess
import uuid


_MOVER = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$video = Get-Item -LiteralPath $env:CORTELIVE_VIDEO_LIXEIRA
$lixeira = (New-Object -ComObject Shell.Application).NameSpace(10)
$lixeira.MoveHere($video.FullName, 1044)
foreach ($item in $lixeira.Items()) {
    if ($item.ExtendedProperty('System.FileName') -eq $video.Name -and
        $item.ExtendedProperty('System.Recycle.DeletedFrom') -eq $video.DirectoryName) {
        $item.Path
    }
}
"""


def reciclar(video: Path):
    # A referência extra no mesmo volume permite reverter se a Lixeira da unidade
    # estiver indisponível, cheia ou configurada para exclusão definitiva.
    reserva = video.with_name(".reciclando-" + uuid.uuid4().hex)
    os.link(video, reserva)
    try:
        powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        movido = subprocess.run(
            [str(powershell), "-NoProfile", "-NonInteractive", "-STA", "-Command", _MOVER],
            env={**os.environ, "CORTELIVE_VIDEO_LIXEIRA": str(video)},
            stdin=subprocess.DEVNULL, capture_output=True, encoding="utf-8", errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW, timeout=22, check=True,
        )
        for linha in movido.stdout.splitlines():
            item = Path(linha)
            if item.is_file() and reserva.samefile(item) and not video.exists():
                break
        else:
            raise OSError("O Windows não confirmou o envio à Lixeira.")
    except BaseException:
        if not video.exists():
            reserva.rename(video)
        elif video.samefile(reserva):
            reserva.unlink()
        raise
    reserva.unlink()
