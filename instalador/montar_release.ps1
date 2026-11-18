# Roteiro unico de lancamento do CorteLive: confere, empacota e deixa tudo pronto em uma pasta.
# NAO cria tag, NAO faz push, NAO cria Release, NAO mexe no VERSION do repositorio. Quem publica e outro passo (com OK do Gabriel).
#
#   -Ensaio (padrao): trabalha numa COPIA do codigo atual em %TEMP% com VERSION = -Versao; o repositorio nao muda.
#                     Saida em C:\Users\gabri\Downloads\CorteLive_para_Drive\v<versao>_ENSAIO
#   -Final          : trabalha no proprio repositorio (VERSION precisa ja ser igual a -Versao e a arvore estar limpa).
#                     Saida em C:\Users\gabri\Downloads\CorteLive_para_Drive\v<versao>
#
# Uso: powershell -NoProfile -ExecutionPolicy Bypass -File instalador\montar_release.ps1 -Versao 1.0.7 [-Final] [-PularTestes]
param(
  [Parameter(Mandatory)][ValidatePattern('^\d+\.\d+\.\d+$')][string]$Versao,
  [switch]$Final,
  [switch]$PularTestes,
  [string]$Drive = 'C:\Users\gabri\Downloads\CorteLive_para_Drive',
  [int]$TimeoutTesteSeg = 120
)
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$ensaio = -not $Final
$resumo = New-Object System.Collections.Generic.List[string]
function Etapa([string]$t) { Write-Host ''; Write-Host "== $t" -ForegroundColor Cyan }
function Ok([string]$t) { $resumo.Add("OK   $t"); Write-Host "OK   $t" -ForegroundColor Green }
function Falhou([string]$t) { Write-Host "FALHOU: $t" -ForegroundColor Red; throw $t }

# --- 1) Pasta de trabalho ---------------------------------------------------------------------
Etapa "1) Pasta de trabalho ($(if ($ensaio) { 'ENSAIO sobre copia' } else { 'FINAL no repositorio' }))"
Push-Location $repo
try {
  if ($ensaio) {
    $raiz = Join-Path $env:TEMP ("cortelive_ensaio_$Versao")
    if (Test-Path -LiteralPath $raiz) { Remove-Item -LiteralPath $raiz -Recurse -Force }
    New-Item -ItemType Directory -Path $raiz | Out-Null
    $lista = & git ls-files -z --cached --others --exclude-standard
    $arquivos = ($lista -join '') -split "`0" | Where-Object { $_ }
    $n = 0
    foreach ($rel in $arquivos) {
      $orig = Join-Path $repo $rel
      if (-not (Test-Path -LiteralPath $orig -PathType Leaf)) { continue }
      $dest = Join-Path $raiz $rel
      New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dest) | Out-Null
      Copy-Item -LiteralPath $orig -Destination $dest
      $n++
    }
    [IO.File]::WriteAllText((Join-Path $raiz 'VERSION'), "$Versao`n", (New-Object Text.UTF8Encoding($false)))
    Ok "copia do codigo atual: $n arquivos em $raiz (VERSION=$Versao so na copia)"
  } else {
    $raiz = $repo
    $atual = (Get-Content -LiteralPath (Join-Path $repo 'VERSION') -Raw -Encoding UTF8).Trim()
    if ($atual -ne $Versao) { Falhou "VERSION do repositorio e '$atual', esperado '$Versao'." }
    $sujo = & git status --porcelain --untracked-files=no
    if ($sujo) { Falhou "Arvore com mudancas nao commitadas:`n$($sujo -join "`n")" }
    Ok "VERSION=$Versao e arvore limpa"
  }
} finally { Pop-Location }

# --- 2) Consistencia de versao ----------------------------------------------------------------
Etapa '2) Versao em todos os lugares'
$vArq = (Get-Content -LiteralPath (Join-Path $raiz 'VERSION') -Raw -Encoding UTF8).Trim()
if ($vArq -ne $Versao) { Falhou "VERSION=$vArq" }
$py = Join-Path $repo '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $py)) { $py = (Get-Command python.exe).Source }
$vPkg = (& $py -I -c "import sys; sys.path.insert(0, sys.argv[1]); import cortelive; print(cortelive.__version__)" $raiz).Trim()
if ($vPkg -ne $Versao) { Falhou "cortelive.__version__=$vPkg" }
$pp = Get-Content -LiteralPath (Join-Path $raiz 'pyproject.toml') -Raw -Encoding UTF8
if ($pp -notmatch 'version\s*=\s*\{\s*file\s*=\s*"VERSION"\s*\}') { Falhou 'pyproject.toml nao le a versao do arquivo VERSION.' }
Ok "VERSION, cortelive.__version__ e pyproject (dynamic -> VERSION) = $Versao"

# --- 3) Testes --------------------------------------------------------------------------------
Etapa '3) pytest inteiro (com limite de tempo por teste)'
if ($PularTestes) { $resumo.Add('PULOU pytest (-PularTestes)'); Write-Host 'pulado' -ForegroundColor Yellow }
else {
  Push-Location $repo
  try {
    $env:PYTHONPATH = $repo; $env:PYTHONUTF8 = '1'
    $saida = & $py -m pytest -c testes/pytest.ini testes tests -q -p no:cacheprovider "--timeout=$TimeoutTesteSeg" 2>&1 | Out-String
    $codigo = $LASTEXITCODE
    $ultimas = ($saida -split "`r?`n" | Where-Object { $_.Trim() } | Select-Object -Last 3) -join ' | '
    if ($codigo -ne 0) { Write-Host $saida; Falhou "pytest falhou ($codigo)" }
    Ok "pytest: $ultimas"
  } finally { Pop-Location; Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue }
}

# --- 4) gitleaks ------------------------------------------------------------------------------
Etapa '4) gitleaks (segredos)'
$gl = (Get-Command gitleaks -ErrorAction SilentlyContinue)
if (-not $gl) { Falhou 'gitleaks nao encontrado no PATH.' }
& gitleaks detect --source $raiz --no-git --no-banner --redact -v 2>&1 | Select-Object -Last 3 | ForEach-Object { Write-Host $_ }
if ($LASTEXITCODE -ne 0) { Falhou 'gitleaks achou segredo na arvore.' }
if ($Final) {
  Push-Location $repo
  try { & gitleaks detect --source $repo --no-banner --redact 2>&1 | Select-Object -Last 2 | ForEach-Object { Write-Host $_ } }
  finally { Pop-Location }
  if ($LASTEXITCODE -ne 0) { Falhou 'gitleaks achou segredo no historico.' }
  Ok 'gitleaks: arvore e historico limpos'
} else { Ok 'gitleaks: arvore limpa (historico so no -Final)' }

# --- 5) Empacotar e Setup ---------------------------------------------------------------------
Etapa '5) empacotar.ps1 e build_setup.ps1'
$dist = Join-Path $raiz 'dist'
foreach ($f in 'cortelive_amigos.zip', 'cortelive_amigos.zip.sha256', 'CorteLive.exe', 'CorteLive.exe.sha256', 'CorteLive-Setup.exe', 'CorteLive-Setup.exe.sha256') {
  $p = Join-Path $dist $f
  if (Test-Path -LiteralPath $p) { Remove-Item -LiteralPath $p -Force }
}
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $raiz 'instalador\empacotar.ps1') | ForEach-Object { Write-Host $_ }
if ($LASTEXITCODE -ne 0) { Falhou "empacotar.ps1 falhou ($LASTEXITCODE)" }
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $raiz 'instalador\build_setup.ps1') | ForEach-Object { Write-Host $_ }
if ($LASTEXITCODE -ne 0) { Falhou "build_setup.ps1 falhou ($LASTEXITCODE)" }
$setup = Join-Path $dist 'CorteLive-Setup.exe'
$h = (Get-FileHash -LiteralPath $setup -Algorithm SHA256).Hash
"$h  CorteLive-Setup.exe" | Set-Content -LiteralPath "$setup.sha256" -Encoding ASCII

# Atualizar CorteLive.bat tambem vai como anexo, com selo.
$bat = Join-Path $raiz 'Atualizar CorteLive.bat'
if (-not (Test-Path -LiteralPath $bat)) { Falhou "'Atualizar CorteLive.bat' nao existe na raiz." }
Copy-Item -LiteralPath $bat -Destination (Join-Path $dist 'Atualizar CorteLive.bat') -Force
$hb = (Get-FileHash -LiteralPath $bat -Algorithm SHA256).Hash
"$hb  Atualizar CorteLive.bat" | Set-Content -LiteralPath (Join-Path $dist 'Atualizar CorteLive.bat.sha256') -Encoding ASCII

# --- 6) Conferir os anexos --------------------------------------------------------------------
Etapa '6) Conferencia dos anexos'
$anexos = 'CorteLive-Setup.exe', 'CorteLive.exe', 'cortelive_amigos.zip', 'Atualizar CorteLive.bat'
foreach ($a in $anexos) {
  $p = Join-Path $dist $a
  if (-not (Test-Path -LiteralPath $p)) { Falhou "anexo ausente: $a" }
  $real = (Get-FileHash -LiteralPath $p -Algorithm SHA256).Hash.ToUpperInvariant()
  $selo = ((Get-Content -LiteralPath "$p.sha256" -Raw -Encoding ASCII).Trim() -split '\s+')[0].ToUpperInvariant()
  if ($real -ne $selo) { Falhou "selo divergente em $a" }
}
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [IO.Compression.ZipFile]::OpenRead((Join-Path $dist 'cortelive_amigos.zip'))
try {
  $nomes = $zip.Entries | ForEach-Object { $_.FullName.Replace('\', '/') }
  foreach ($obrig in 'VERSION', 'Atualizar CorteLive.bat', 'GUIA_RAPIDO_AMIGOS.md', 'CorteLive.exe', 'instalador/atualizar.ps1', 'instalador/instalar.ps1', 'cortelive/disco.py', 'painel/relatorio_erro.py') {
    if ($nomes -notcontains $obrig) { Falhou "o zip nao contem $obrig" }
  }
  $e = $zip.GetEntry('VERSION'); $sr = New-Object IO.StreamReader($e.Open())
  $vz = $sr.ReadToEnd().Trim(); $sr.Dispose()
  if ($vz -ne $Versao) { Falhou "VERSION dentro do zip = $vz" }
} finally { $zip.Dispose() }
Ok "4 anexos com selo .sha256 conferido; zip contem VERSION=$Versao, Atualizar CorteLive.bat, GUIA_RAPIDO_AMIGOS.md, disco.py, relatorio_erro.py"

# --- 7) Copia para o Drive --------------------------------------------------------------------
Etapa '7) Copia para a pasta do Drive'
$destino = Join-Path $Drive ("v$Versao" + $(if ($ensaio) { '_ENSAIO' } else { '' }))
New-Item -ItemType Directory -Force -Path $destino | Out-Null
foreach ($a in $anexos) {
  Copy-Item -LiteralPath (Join-Path $dist $a) -Destination $destino -Force
  Copy-Item -LiteralPath (Join-Path $dist "$a.sha256") -Destination $destino -Force
}
Ok "copiado para $destino"

Write-Host ''
Write-Host '================ RESUMO ================' -ForegroundColor Cyan
$resumo | ForEach-Object { Write-Host $_ }
Write-Host "Pacote: $dist"
Write-Host "Drive : $destino"
if ($ensaio) { Write-Host 'ENSAIO OK. Nada foi publicado; VERSION do repositorio nao mudou.' -ForegroundColor Green }
else { Write-Host "FINAL OK. Para publicar (so com OK do Gabriel): git tag v$Versao; git push origin main v$Versao" -ForegroundColor Green }
