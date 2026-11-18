# Instalador do cortelive. Chamado pelo instalar.cmd. Nao precisa de administrador.
# Tudo fica dentro da pasta da ferramenta: python\, .venv\, ffmpeg\, ferramentas\, modelos\.
# Opcoes: -Gpu nvidia|amd|intel|cpu   (forcar o modo; padrao = detectar)
#         -BaixarFFmpeg                (baixar o FFmpeg proprio mesmo se o PC ja tiver um bom)
#         -SemPerguntas                (nao perguntar nada; Ollama = nao)
#         -SemModelos                  (nao baixar modelo de fala agora; baixa no primeiro uso)
param(
  [ValidateSet('', 'nvidia', 'amd', 'intel', 'cpu')][string]$Gpu = '',
  [switch]$BaixarFFmpeg,
  [switch]$SemPerguntas,
  [switch]$SemModelos
)
$ErrorActionPreference = 'Continue'  # erros checados um a um ($LASTEXITCODE); 'Stop' quebra com aviso do pip no stderr
$ProgressPreference = 'SilentlyContinue'
$RAIZ = Split-Path -Parent $PSScriptRoot
$TMP = Join-Path $RAIZ '_downloads'
$LOG = Join-Path $RAIZ 'instalacao_log.txt'
. (Join-Path $PSScriptRoot 'versoes.ps1')
$inicio = Get-Date
"=== instalacao $(Get-Date -Format s) ===" | Out-File $LOG -Encoding utf8

function Passo($t) { Write-Host ""; Write-Host ">> $t" -ForegroundColor Cyan; "[$(Get-Date -Format T)] $t" | Out-File $LOG -Append -Encoding utf8 }
function Ok($t) { Write-Host "   OK: $t" -ForegroundColor Green; "   OK: $t" | Out-File $LOG -Append -Encoding utf8 }
function Falha($t, $oque) {
  Write-Host ""; Write-Host "ERRO: $t" -ForegroundColor Red
  Write-Host "O que fazer: $oque" -ForegroundColor Yellow
  Write-Host "Detalhes em: $LOG"
  "ERRO: $t | $oque" | Out-File $LOG -Append -Encoding utf8
  exit 1
}

function Baixar($item) {
  New-Item -ItemType Directory -Force $TMP | Out-Null
  $dest = Join-Path $TMP $item.Arq
  if ((Test-Path $dest) -and ((Get-FileHash $dest -Algorithm SHA256).Hash -eq $item.Sha256)) { Ok "$($item.Nome) (ja baixado)"; return $dest }
  Write-Host "   baixando $($item.Nome)..."
  # curl.exe vem no Windows 10/11; -C - retoma download interrompido
  & curl.exe -L --fail --retry 3 -C - -# -o $dest $item.Url
  if ($LASTEXITCODE -ne 0) {
    Remove-Item $dest -ErrorAction SilentlyContinue
    & curl.exe -L --fail --retry 3 -# -o $dest $item.Url
    if ($LASTEXITCODE -ne 0) { Falha "nao consegui baixar $($item.Nome)" "confira a internet (ou antivirus/proxy) e rode instalar.cmd de novo. Link: $($item.Url)" }
  }
  $h = (Get-FileHash $dest -Algorithm SHA256).Hash
  if ($h -ne $item.Sha256) {
    Remove-Item $dest -ErrorAction SilentlyContinue
    Falha "o arquivo baixado de $($item.Nome) nao bate com o checksum oficial (pode estar corrompido ou adulterado)" "rode instalar.cmd de novo. Se repetir, nao use: avise quem te mandou a ferramenta."
  }
  Ok "$($item.Nome) (checksum conferido)"
  return $dest
}

function FFmpegBom($exe) {
  if (-not $exe -or -not (Test-Path $exe)) { return $false }
  try {
    $f = & $exe -hide_banner -filters 2>$null | Out-String
    $e = & $exe -hide_banner -encoders 2>$null | Out-String
    $probe = Join-Path (Split-Path $exe) 'ffprobe.exe'
    return ($f -match '\ssubtitles\s' -and $f -match '\sass\s' -and $e -match 'libx264' -and (Test-Path $probe))
  } catch { return $false }
}

Write-Host "=========================================="
Write-Host "  Instalando o cortelive (cortes de live)"
Write-Host "=========================================="
Write-Host "Pasta: $RAIZ"
if ($RAIZ.Length -gt 120) { Write-Host "Aviso: a pasta tem um caminho muito longo. Se der erro, mova para algo como C:\cortelive." -ForegroundColor Yellow }
$livre = (Get-PSDrive ($RAIZ.Substring(0,1))).Free / 1GB
if ($livre -lt 6) { Falha ("pouco espaco no disco ({0:N1} GB livres)" -f $livre) "libere pelo menos 6 GB e rode de novo." }

# 1) Python portatil proprio ------------------------------------------------
Passo "1/6 Python portatil (nao usa o Python do PC)"
$PYEXE = Join-Path $RAIZ 'python\python.exe'
if (-not (Test-Path $PYEXE)) {
  $tgz = Baixar $PY
  & tar.exe -xzf $tgz -C $RAIZ
  if ($LASTEXITCODE -ne 0 -or -not (Test-Path $PYEXE)) { Falha "nao consegui descompactar o Python" "rode instalar.cmd de novo. Windows antigo sem tar.exe? Atualize o Windows 10." }
}
Ok (& $PYEXE --version)

# 2) Ambiente isolado + bibliotecas fixadas ---------------------------------
Passo "2/6 Ambiente isolado (.venv) e bibliotecas"
$VPY = Join-Path $RAIZ '.venv\Scripts\python.exe'
if (Test-Path $VPY) {
  # venv de outro Python (ex.: copiado de outro PC) nao serve: recria
  $base = (& $VPY -c "import sys;print(sys.base_prefix)" 2>$null)
  if ($LASTEXITCODE -ne 0 -or ($base -and ([IO.Path]::GetFullPath($base) -ne [IO.Path]::GetFullPath((Join-Path $RAIZ 'python'))))) {
    Write-Host "   .venv antigo nao e deste Python; recriando."
    Remove-Item -Recurse -Force (Join-Path $RAIZ '.venv')
  }
}
if (-not (Test-Path $VPY)) {
  & $PYEXE -m venv (Join-Path $RAIZ '.venv')
  if ($LASTEXITCODE -ne 0) { Falha "nao consegui criar o ambiente .venv" "apague a pasta .venv e rode de novo." }
}
# requirements.txt sem as linhas nvidia-* (essas so entram com placa NVIDIA, no passo 4)
$req = Get-Content (Join-Path $RAIZ 'requirements.txt') -Encoding UTF8
$reqBase = Join-Path $TMP 'req_base.txt'; $reqNv = Join-Path $TMP 'req_nvidia.txt'
New-Item -ItemType Directory -Force $TMP | Out-Null
$req | Where-Object { $_ -notmatch '^\s*nvidia-' } | Set-Content $reqBase -Encoding ASCII
$req | Where-Object { $_ -match '^\s*nvidia-' } | Set-Content $reqNv -Encoding ASCII
Write-Host "   instalando bibliotecas (versoes fixadas; pode levar alguns minutos)..."
& $VPY -m pip install --disable-pip-version-check --no-warn-script-location -q -r $reqBase 2>&1 | Out-File $LOG -Append -Encoding utf8
if ($LASTEXITCODE -ne 0) { Falha "pip nao conseguiu instalar as bibliotecas" "confira a internet e rode instalar.cmd de novo (o log mostra qual pacote falhou)." }
Ok "bibliotecas instaladas (faster-whisper, streamlink, yt-dlp, opencv...)"

# 3) FFmpeg ----------------------------------------------------------------
Passo "3/6 FFmpeg (video e legenda)"
$FFDIR = Join-Path $RAIZ 'ffmpeg\bin'
$ffLocal = Join-Path $FFDIR 'ffmpeg.exe'
$ffPC = (Get-Command ffmpeg.exe -ErrorAction SilentlyContinue).Source
if (FFmpegBom $ffLocal) { Ok "FFmpeg proprio ja instalado" }
elseif (-not $BaixarFFmpeg -and (FFmpegBom $ffPC)) { Ok "usando o FFmpeg que ja existe no PC ($ffPC), com legenda (libass)" }
else {
  if ($ffPC) { Write-Host "   o FFmpeg do PC nao serve (sem libass/ffprobe) ou foi pedido o proprio; baixando." }
  $zip = Baixar $FFMPEG
  $tmpx = Join-Path $TMP 'ffx'
  Remove-Item -Recurse -Force $tmpx -ErrorAction SilentlyContinue; New-Item -ItemType Directory -Force $tmpx | Out-Null
  & tar.exe -xf $zip -C $tmpx
  $bin = Get-ChildItem $tmpx -Recurse -Filter ffmpeg.exe | Select-Object -First 1
  if (-not $bin) { Falha "o pacote do FFmpeg veio sem ffmpeg.exe" "rode instalar.cmd de novo." }
  New-Item -ItemType Directory -Force $FFDIR | Out-Null
  Copy-Item (Join-Path $bin.DirectoryName '*.exe') $FFDIR -Force
  Remove-Item -Recurse -Force $tmpx
  if (-not (FFmpegBom $ffLocal)) { Falha "o FFmpeg baixado nao passou no teste" "rode instalar.cmd de novo." }
  Ok "FFmpeg instalado em ffmpeg\bin"
}

# 4) Placa de video ----------------------------------------------------------
Passo "4/6 Placa de video"
$env:PYTHONPATH = $RAIZ
$det = & $VPY -c "from cortelive import hw; ps=hw.placas(); vs=[v for v in map(hw.vendor_de,ps) if v]; print(next((v for v in ('nvidia','amd','intel') if v in vs),'cpu')); print(' | '.join(ps) or 'nenhuma')"
$vendor = ($det | Select-Object -First 1).Trim()
$nomes = ($det | Select-Object -Skip 1 -First 1)
Write-Host "   placa(s): $nomes"
$forcado = $false
if ($Gpu) { $vendor = $Gpu; $forcado = $true; Write-Host "   modo forcado: $Gpu" }
$modo = 'cpu'
if ($vendor -eq 'nvidia') {
  Write-Host "   NVIDIA: instalando bibliotecas CUDA para a fala (~1 GB)..."
  & $VPY -m pip install --disable-pip-version-check -q -r $reqNv 2>&1 | Out-File $LOG -Append -Encoding utf8
  if ($LASTEXITCODE -ne 0) { Write-Host "   nao consegui instalar o CUDA; a fala vai pela CPU." -ForegroundColor Yellow }
  $cu = & $VPY -c "from cortelive import hw; print(hw.cuda_ok())"
  if ($cu -match 'True') { $modo = 'nvidia' } else { Write-Host "   a placa NVIDIA nao respondeu ao CUDA (driver antigo?). Fala pela CPU; atualize o driver da NVIDIA para acelerar." -ForegroundColor Yellow }
}
elseif ($vendor -in 'amd', 'intel') {
  $modo = $vendor
  Ok "fala no processador (faster-whisper small, int8); video na placa"
}
$txt = @{ nvidia = 'NVIDIA: fala na placa (CUDA) + video h264_nvenc'; amd = 'AMD: fala no processador + video na placa (h264_amf)'; intel = 'Intel: fala no processador + video na placa (h264_qsv)'; cpu = 'SEM PLACA: tudo na CPU (mais lento; cortes podem atrasar em live longa)' }
Ok "MODO ESCOLHIDO: $($txt[$modo])"
$argGpu = ''
if ($forcado) { $argGpu = "--gpu $modo" }
@("modo=$modo", "args=$argGpu", "instalado=$(Get-Date -Format s)") | Set-Content (Join-Path $RAIZ 'instalador\modo.txt') -Encoding ASCII

# 5) Modelo de fala ----------------------------------------------------------
Passo "5/6 Modelo de fala (Whisper)"
$env:HF_HOME = Join-Path $RAIZ 'modelos'
$env:HF_HUB_DISABLE_SYMLINKS_WARNING = '1'
$env:HF_HUB_DISABLE_TELEMETRY = '1'
if ($SemModelos) { Write-Host "   pulado: baixa sozinho no primeiro uso." }
else {
  # NVIDIA: turbo na placa + small (o motor troca para o small quando a placa fica apertada)
  $nomes = @('small'); if ($modo -eq 'nvidia') { $nomes = @('large-v3-turbo', 'small') }
  foreach ($nome in $nomes) {
    Write-Host "   baixando modelo '$nome' (uma vez so; fica em modelos\)..."
    & $VPY -c "import sys,types; sys.modules.setdefault('av', types.ModuleType('av')); from faster_whisper import download_model; print(download_model('$nome'))"
    if ($LASTEXITCODE -ne 0) { Falha "nao consegui baixar o modelo de fala" "confira a internet e rode instalar.cmd de novo." }
    Ok "modelo '$nome' pronto"
  }
}

# 6) Ollama (opcional) -------------------------------------------------------
Passo "6/6 Ollama (opcional: IA local que escolhe melhor os momentos)"
if (Get-Command ollama -ErrorAction SilentlyContinue) { Ok "Ollama ja instalado" }
elseif ($SemPerguntas) { Write-Host "   pulado. O cortelive funciona 100% sem ele." }
else {
  $r = Read-Host "   Quer instalar o Ollama? Nao e obrigatorio. (s/n)"
  if ($r -match '^[sS]') {
    if (Get-Command winget -ErrorAction SilentlyContinue) {
      & winget install -e --id Ollama.Ollama --accept-source-agreements --accept-package-agreements
      Write-Host "   Depois rode no CMD: ollama pull qwen2.5:3b   (modelo pequeno)"
    } else { Write-Host "   Baixe no site oficial: https://ollama.com/download" }
  } else { Write-Host "   ok, sem Ollama." }
}

Remove-Item -Recurse -Force $TMP -ErrorAction SilentlyContinue
$min = ((Get-Date) - $inicio).TotalMinutes
$tam = (Get-ChildItem $RAIZ -Recurse -File -Force -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum / 1GB
"tempo_min=$([math]::Round($min,1)) tamanho_gb=$([math]::Round($tam,2)) modo=$modo" | Out-File $LOG -Append -Encoding utf8
Write-Host ""
Write-Host "==========================================" -ForegroundColor Green
Write-Host ("  PRONTO em {0:N1} min. Pasta ocupa {1:N1} GB." -f $min, $tam) -ForegroundColor Green
Write-Host "  Para cortar: de dois cliques em cortar.cmd" -ForegroundColor Green
Write-Host "==========================================" -ForegroundColor Green
exit 0
