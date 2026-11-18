@echo off
rem Abre o visualizador "Ao vivo" do CorteLive no Edge em modo app (janela grande).
setlocal EnableExtensions
set "CL_RAIZ=%~dp0"
set "CL_PY=%CL_RAIZ%.venv\Scripts\pythonw.exe"
if not exist "%CL_PY%" set "CL_PY=%CL_RAIZ%.venv\Scripts\python.exe"
set "CL_SCRIPT=%CL_RAIZ%ferramentas_dev\ao_vivo\ao_vivo.py"

if not exist "%CL_SCRIPT%" (
  echo Visualizador Ao vivo nao encontrado nesta pasta.
  pause
  exit /b 1
)

if not exist "%CL_PY%" (
  echo Python do .venv nao encontrado em "%CL_RAIZ%.venv\Scripts\".
  echo Execute instalar.cmd ou ABRIR_PAINEL.cmd primeiro.
  pause
  exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference = 'Stop';" ^
  "$raiz = $env:CL_RAIZ.TrimEnd('\');" ^
  "$py = $env:CL_PY;" ^
  "$sc = $env:CL_SCRIPT;" ^
  "$inst = Join-Path $raiz 'ferramentas_dev\ao_vivo\instancia.json';" ^
  "$porta = 54777;" ^
  "if (Test-Path $inst) { try { $j = Get-Content $inst -Raw | ConvertFrom-Json; if ($j.porta) { $porta = [int]$j.porta } } catch {} };" ^
  "$ativo = $false;" ^
  "try { $r = Invoke-WebRequest -UseBasicParsing -Uri ('http://127.0.0.1:' + $porta + '/__aovivo/ping') -TimeoutSec 1; if ($r.StatusCode -eq 200) { $ativo = $true } } catch {};" ^
  "if (-not $ativo) {" ^
  "  $argPy = '\"' + $sc + '\" --porta 54777';" ^
  "  $processo = Start-Process -FilePath $py -ArgumentList $argPy -WorkingDirectory $raiz -WindowStyle Hidden -PassThru;" ^
  "  for ($i = 0; $i -lt 36; $i++) {" ^
  "    Start-Sleep -Milliseconds 250;" ^
  "    if (Test-Path $inst) { try { $j = Get-Content $inst -Raw | ConvertFrom-Json; if ($j.porta) { $porta = [int]$j.porta } } catch {} };" ^
  "    try { $r = Invoke-WebRequest -UseBasicParsing -Uri ('http://127.0.0.1:' + $porta + '/__aovivo/ping') -TimeoutSec 1; if ($r.StatusCode -eq 200) { $ativo = $true; break } } catch {}" ^
  "    if ($processo.HasExited) { break }" ^
  "  }" ^
  "  if (-not $ativo) { Write-Host 'O servidor Ao vivo nao iniciou. Confira a instalacao do CorteLive.'; exit 1 }" ^
  "};" ^
  "$url = 'http://127.0.0.1:' + $porta;" ^
  "$perfil = Join-Path $env:LOCALAPPDATA 'CorteLive\webview_ao_vivo';" ^
  "$candidatos = @(" ^
  "  (Join-Path ${env:ProgramFiles(x86)} 'Microsoft\Edge\Application\msedge.exe')," ^
  "  (Join-Path $env:ProgramFiles 'Microsoft\Edge\Application\msedge.exe')" ^
  ");" ^
  "$edge = $candidatos | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1;" ^
  "$argEdge = '--user-data-dir=\"' + $perfil + '\" --no-first-run --no-default-browser-check --app=' + $url + ' --window-size=1600,1000';" ^
  "if ($edge) { Start-Process -FilePath $edge -ArgumentList $argEdge } else { Start-Process -FilePath $url }"
if errorlevel 1 (
  pause
  exit /b 1
)
exit /b 0
