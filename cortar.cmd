@echo off
rem Atalho de uso do cortelive.
rem   Dois cliques: pergunta o link, o modelo e a legenda.
rem   No CMD: cortar.cmd https://twitch.tv/canal   ou   cortar.cmd --arquivo video.mp4 [outras opcoes]
setlocal EnableDelayedExpansion
set "RAIZ=%~dp0"
if not exist "%RAIZ%.venv\Scripts\python.exe" (
  echo O cortelive ainda nao foi instalado. De dois cliques em instalar.cmd primeiro.
  if "%CORTELIVE_SEM_PAUSA%"=="" pause
  exit /b 1
)
rem tudo local: FFmpeg proprio (se houver), modelos em modelos\, codigo desta pasta
set "PATH=%RAIZ%ffmpeg\bin;%RAIZ%.venv\Scripts;%PATH%"
set "HF_HOME=%RAIZ%modelos"
set "HF_HUB_DISABLE_SYMLINKS_WARNING=1"
set "PYTHONPATH=%RAIZ%"
set "PYTHONUTF8=1"
set "EXTRA="
if exist "%RAIZ%instalador\modo.txt" (
  for /f "usebackq tokens=1,* delims==" %%a in ("%RAIZ%instalador\modo.txt") do if "%%a"=="args" set "EXTRA=%%b"
)
cd /d "%RAIZ%"

if not "%~1"=="" (
  "%RAIZ%.venv\Scripts\python.exe" -m cortelive %* !EXTRA!
  exit /b !ERRORLEVEL!
)

echo ==========================================
echo   cortelive - cortes automaticos de live
echo ==========================================
echo.
echo Cole os links das lives (Twitch, YouTube, Kick), UM POR VEZ, apertando Enter.
echo (Tambem vale o caminho de um video do PC, para testar.)
echo Quando acabar, aperte Enter vazio.
echo Ou digite L para abrir/editar a lista lives.txt (um link por linha).
set "LINKS="
set "N=0"
:pede_link
set "LINK="
set /a P=N+1
set /p "LINK=Link !P! (Enter vazio = acabou): "
if /i "!LINK!"=="L" goto usa_lista
if "!LINK!"=="" goto fim_links
rem tira aspas (arrastar arquivo para a janela poe aspas)
set "LINK=!LINK:"=!"
rem so espacos = Enter vazio (antes virava uma live com nome em branco e quebrava)
if "!LINK: =!"=="" goto fim_links
rem caminho de video do PC vira --arquivo (teste como se fosse live)
if exist "!LINK!" (
  set "LINKS=!LINKS! --arquivo "!LINK!""
) else (
  set "LINKS=!LINKS! "!LINK!""
)
set /a N+=1
goto pede_link
:usa_lista
if not exist "%RAIZ%lives.txt" (
  > "%RAIZ%lives.txt" (
    echo # Um link de live por linha. Linhas com # sao ignoradas.
    echo # Tire o # da frente dos exemplos ou cole os seus. Salve e feche o Bloco de Notas.
    echo # A lista e relida a cada 30 s: da para tirar e por live com o programa rodando.
    echo # https://www.twitch.tv/canal1
    echo # https://www.youtube.com/@canal2/live
    echo # https://kick.com/canal3
  )
)
echo Abrindo lives.txt. Salve e FECHE o Bloco de Notas para continuar...
start /wait notepad "%RAIZ%lives.txt"
set "LINKS=--lista "%RAIZ%lives.txt""
set "N=lista"
:fim_links
if "!N!"=="0" ( echo Nenhum link. Saindo. & pause & exit /b 1 )
echo.
echo Modelo do corte:
echo   1 = cam em cima, jogo embaixo (padrao)
echo   2 = jogo com fundo desfocado
echo   3 = jogo em tela cheia
set "M="
set /p "M=Escolha 1, 2 ou 3 (Enter = 1): "
set "MODELO=cam-em-cima"
if "!M!"=="2" set "MODELO=jogo-fundo-desfocado"
if "!M!"=="3" set "MODELO=jogo-tela-cheia-crop"
echo.
echo Legenda:
echo   1 = opus (padrao)   2 = karaoke   3 = minimal
set "L="
set /p "L=Escolha 1, 2 ou 3 (Enter = 1): "
set "LEG=opus"
if "!L!"=="2" set "LEG=karaoke"
if "!L!"=="3" set "LEG=minimal"
echo.
echo Rodando. Os cortes saem na pasta cortes\. Para parar: Ctrl+C.
echo.
"%RAIZ%.venv\Scripts\python.exe" -m cortelive !LINKS! --modelo !MODELO! --legenda !LEG! !EXTRA!
set ERRO=!ERRORLEVEL!
echo.
if not "!ERRO!"=="0" echo Terminou com erro. Veja a mensagem acima e o log em cortes\.
pause
exit /b !ERRO!
