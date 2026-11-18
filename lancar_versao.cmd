@echo off
rem Assistente para lancar uma versao nova do CorteLive no GitHub.
rem Opcao para teste sem enviar nada para a internet: lancar_versao.cmd -Simular
setlocal
cd /d "%~dp0"
chcp 65001 >nul
set "CORTELIVE_LANCAR_ARGS=%*"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference = 'Continue';" ^
  "$simular = ($env:CORTELIVE_LANCAR_ARGS -match '(?i)-Simular') -or ($env:CORTELIVE_SIMULAR -eq '1');" ^
  "$utf8 = New-Object System.Text.UTF8Encoding($false);" ^
  "[Console]::OutputEncoding = $utf8;" ^
  "Write-Host '================================================' -ForegroundColor Cyan;" ^
  "Write-Host '  CorteLive - Lancar Nova Versao para os Amigos ' -ForegroundColor Cyan;" ^
  "Write-Host '================================================' -ForegroundColor Cyan;" ^
  "if ($simular) { Write-Host '[MODO SIMULACAO: nada sera enviado ao GitHub]' -ForegroundColor Yellow }" ^
  "$arqVer = Join-Path $PWD 'VERSION';" ^
  "$verAtual = if (Test-Path $arqVer) { (Get-Content $arqVer -Raw -Encoding UTF8).Trim() } else { '0.1.0' };" ^
  "$partes = $verAtual -split '\.';" ^
  "$sugestao = if ($partes.Count -eq 3) { '{0}.{1}.{2}' -f $partes[0], $partes[1], ([int]$partes[2] + 1) } else { '0.1.1' };" ^
  "if (-not (Get-Command git.exe -ErrorAction SilentlyContinue)) { Write-Host 'ERRO: O Git nao esta instalado neste PC.' -ForegroundColor Red; exit 1 }" ^
  "if (-not (Test-Path '.git')) { Write-Host 'ERRO: Esta pasta ainda nao foi conectada ao GitHub (.git ausente).' -ForegroundColor Red; exit 1 }" ^
  "$remoto = (& git remote get-url origin 2>$null | Out-String).Trim();" ^
  "if (-not $remoto -and -not $simular) { Write-Host 'ERRO: O endereco do GitHub (origin) ainda nao esta configurado nesta pasta.' -ForegroundColor Red; exit 1 }" ^
  "Write-Host '';" ^
  "Write-Host ('Versao atual: ' + $verAtual) -ForegroundColor White;" ^
  "$entrada = (Read-Host ('1) Qual o numero da nova versao? (Enter = ' + $sugestao + ')')).Trim().TrimStart('v','V');" ^
  "if (-not $entrada) { $entrada = $sugestao }" ^
  "if ($entrada -notmatch '^\d+\.\d+\.\d+$') { Write-Host ('ERRO: Use tres numeros separados por ponto, como ' + $sugestao + '.') -ForegroundColor Red; exit 1 }" ^
  "if ($entrada -eq $verAtual) { Write-Host 'ERRO: O numero novo precisa ser diferente da versao atual.' -ForegroundColor Red; exit 1 }" ^
  "$sujo = (& git status --porcelain | Out-String).Trim();" ^
  "if ($sujo) { Write-Host 'ERRO: Ha mudancas nao gravadas (commit) nesta pasta. Grave o codigo novo antes de lancar, senao a versao sai sem ele:' -ForegroundColor Red; Write-Host $sujo; exit 1 }" ^
  "$tag = 'v' + $entrada;" ^
  "$tagExiste = (& git tag -l $tag | Out-String).Trim();" ^
  "if ($tagExiste) { Write-Host ('ERRO: A etiqueta ' + $tag + ' ja existe. Escolha um numero maior.') -ForegroundColor Red; exit 1 }" ^
  "$nota = (Read-Host '2) Resumo curto do que mudou (opcional, Enter para pular)').Trim();" ^
  "if (-not $nota) { $nota = 'Versao ' + $entrada }" ^
  "Write-Host '';" ^
  "Write-Host 'Resumo do lancamento:' -ForegroundColor Yellow;" ^
  "Write-Host ('  De:       ' + $verAtual + '  ->  Para: ' + $entrada + ' (etiqueta ' + $tag + ')');" ^
  "Write-Host ('  Nota:     ' + $nota);" ^
  "Write-Host ('  Destino:  ' + $(if ($remoto) { $remoto } else { '(simulacao local)' }));" ^
  "Write-Host '';" ^
  "$resp = (Read-Host ('Publicar a versao ' + $tag + ' no GitHub agora? (S/N)')).Trim();" ^
  "if ($resp -notmatch '^[sS]$') { Write-Host 'Cancelado. Nada foi alterado nem publicado.' -ForegroundColor Yellow; exit 0 }" ^
  "[IO.File]::WriteAllText($arqVer, ($entrada + \"`n\"), $utf8);" ^
  "& git add -- VERSION;" ^
  "if ($LASTEXITCODE -ne 0) { Write-Host 'ERRO ao preparar o arquivo VERSION.' -ForegroundColor Red; exit 1 }" ^
  "& git commit -m ('Lancamento ' + $tag + ': ' + $nota) -- VERSION;" ^
  "if ($LASTEXITCODE -ne 0) { Write-Host 'ERRO ao gravar o commit da versao.' -ForegroundColor Red; exit 1 }" ^
  "& git tag -a $tag -m $nota;" ^
  "if ($LASTEXITCODE -ne 0) { Write-Host 'ERRO ao criar a etiqueta da versao.' -ForegroundColor Red; exit 1 }" ^
  "if ($simular) { Write-Host ('OK (simulacao): VERSION atualizado para ' + $entrada + ' e tag local ' + $tag + ' criada (sem push).') -ForegroundColor Green; exit 0 }" ^
  "Write-Host 'Enviando para o GitHub...' -ForegroundColor Cyan;" ^
  "& git push origin HEAD;" ^
  "if ($LASTEXITCODE -ne 0) { Write-Host 'ERRO ao enviar o codigo para o GitHub. Confira sua internet/login.' -ForegroundColor Red; exit 1 }" ^
  "& git push origin $tag;" ^
  "if ($LASTEXITCODE -ne 0) { Write-Host 'ERRO ao enviar a etiqueta para o GitHub.' -ForegroundColor Red; exit 1 }" ^
  "Write-Host '';" ^
  "Write-Host ('PRONTO! A versao ' + $tag + ' foi enviada ao GitHub.') -ForegroundColor Green;" ^
  "Write-Host 'Em 1 a 2 minutos o pacote estara no ar e o painel dos seus amigos vai avisar sozinho.' -ForegroundColor Green;"
set ERRO=%ERRORLEVEL%
if "%CORTELIVE_SEM_PAUSA%"=="" pause
exit /b %ERRO%
