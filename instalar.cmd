@echo off
rem Instalador do cortelive. Dois cliques aqui. Nao precisa de administrador.
rem Tudo fica dentro desta pasta. Desinstalar = apagar a pasta.
rem Opcoes (para quem usa o CMD): -Gpu cpu  -BaixarFFmpeg  -SemPerguntas  -SemModelos
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0instalador\instalar.ps1" %*
set ERRO=%ERRORLEVEL%
echo.
if not "%ERRO%"=="0" echo A instalacao NAO terminou. Leia a mensagem em vermelho acima.
if "%CORTELIVE_SEM_PAUSA%"=="" pause
exit /b %ERRO%
