@echo off
rem Atualiza o cortelive: roda o instalador de novo (so baixa o que mudou ou falta).
rem Para pegar uma versao nova da ferramenta, copie por cima os arquivos novos que te mandarem e rode este.
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0instalador\instalar.ps1" -SemPerguntas %*
set ERRO=%ERRORLEVEL%
if "%CORTELIVE_SEM_PAUSA%"=="" pause
exit /b %ERRO%
