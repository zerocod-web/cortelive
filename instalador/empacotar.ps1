# Monta dist\cortelive_amigos.zip so com o que o amigo precisa (sem .venv, modelos, cortes, testes).
$ErrorActionPreference = 'Stop'
$RAIZ = Split-Path -Parent $PSScriptRoot
$DIST = Join-Path $RAIZ 'dist'
$STAGE = Join-Path $DIST 'cortelive'
Remove-Item -Recurse -Force $STAGE -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $STAGE | Out-Null
foreach ($f in 'INSTALAR_E_RODAR.bat', 'instalar.cmd', 'cortar.cmd', 'atualizar.cmd', 'LEIA-ME-AMIGOS.txt', 'README.md', 'requirements.txt', 'pyproject.toml', 'THIRD_PARTY_NOTICES.md') {
  Copy-Item (Join-Path $RAIZ $f) $STAGE
}
New-Item -ItemType Directory -Force (Join-Path $STAGE 'instalador') | Out-Null
foreach ($f in 'instalar.ps1', 'versoes.ps1', 'empacotar.ps1') { Copy-Item (Join-Path $RAIZ "instalador\$f") (Join-Path $STAGE 'instalador') }
& robocopy (Join-Path $RAIZ 'cortelive') (Join-Path $STAGE 'cortelive') /E /XD __pycache__ /XF *.pyc /NFL /NDL /NJH /NJS | Out-Null
$zip = Join-Path $DIST 'cortelive_amigos.zip'
Remove-Item $zip -ErrorAction SilentlyContinue
Compress-Archive -Path (Join-Path $STAGE '*') -DestinationPath $zip
Remove-Item -Recurse -Force $STAGE
$h = (Get-FileHash $zip -Algorithm SHA256).Hash
"$h  cortelive_amigos.zip" | Set-Content (Join-Path $DIST 'cortelive_amigos.zip.sha256') -Encoding ASCII
"{0}  {1:N0} KB  sha256 {2}" -f $zip, ((Get-Item $zip).Length / 1KB), $h
