@echo off
rem INSTALAR_E_RODAR.bat - dois cliques: confere o que falta, instala so isso e abre o cortelive.
rem Nao precisa de administrador nem de chave/senha. Tudo fica dentro desta pasta.
rem   INSTALAR_E_RODAR.bat checar   = so mostra o que tem e o que falta (nao instala, nao roda)
setlocal EnableDelayedExpansion
cd /d "%~dp0"
set "RAIZ=%~dp0"
set "FALTA=0"

echo ==========================================
echo   cortelive - conferindo o que falta no PC
echo ==========================================
for %%T in (curl.exe tar.exe powershell.exe) do (
  where %%T >nul 2>nul || ( echo ERRO: o Windows nao tem %%T. Atualize o Windows 10/11 e tente de novo. & goto erro )
)
if exist "%RAIZ%python\python.exe" ( echo   OK     Python proprio ) else ( echo   FALTA  Python proprio & set "FALTA=1" )
if exist "%RAIZ%.venv\Scripts\python.exe" (
  "%RAIZ%.venv\Scripts\python.exe" -c "import faster_whisper,streamlink,yt_dlp,cv2,numpy,requests" >nul 2>nul && (
    echo   OK     bibliotecas do projeto
  ) || ( echo   FALTA  bibliotecas do projeto & set "FALTA=1" )
) else ( echo   FALTA  ambiente .venv e bibliotecas & set "FALTA=1" )
set "FF="
if exist "%RAIZ%ffmpeg\bin\ffmpeg.exe" set "FF=%RAIZ%ffmpeg\bin\ffmpeg.exe"
if not defined FF for /f "delims=" %%F in ('where ffmpeg.exe 2^>nul') do if not defined FF set "FF=%%F"
if defined FF ( echo   OK     FFmpeg: !FF! ) else ( echo   FALTA  FFmpeg & set "FALTA=1" )
if exist "%RAIZ%modelos\hub" ( echo   OK     modelo de fala ) else ( echo   FALTA  modelo de fala & set "FALTA=1" )
if exist "%RAIZ%instalador\modo.txt" ( echo   OK     placa de video ja detectada ) else ( echo   FALTA  deteccao da placa de video & set "FALTA=1" )
echo.

if /i "%~1"=="checar" (
  if "!FALTA!"=="0" ( echo Tudo pronto. Nada para instalar. ) else ( echo Falta coisa: rode sem "checar" para instalar. )
  goto fim
)

if "!FALTA!"=="1" (
  echo Instalando o que falta. Demora de 5 a 20 minutos na primeira vez. Nao feche a janela.
  rem o instalador pula o que ja existe, confere o checksum de cada download e checa o FFmpeg a fundo
  powershell -NoProfile -ExecutionPolicy Bypass -File "%RAIZ%instalador\instalar.ps1" -SemPerguntas
  if errorlevel 1 goto erro
) else (
  echo Tudo ja instalado. Pulando a instalacao.
)

echo.
echo Abrindo o cortelive...
set "CORTELIVE_SEM_PAUSA="
call "%RAIZ%cortar.cmd"
exit /b %ERRORLEVEL%

:erro
echo.
echo A instalacao NAO terminou. Leia a mensagem em vermelho acima.
echo O detalhe fica em instalacao_log.txt nesta pasta. Confira a internet e rode de novo:
echo ele continua de onde parou.
pause
exit /b 1

:fim
pause
exit /b 0
