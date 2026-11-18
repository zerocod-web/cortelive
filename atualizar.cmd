@echo off
rem Atualizador automatico do CorteLive.
rem   atualizar.cmd -Checar    -> verifica se ha nova release no GitHub
rem   atualizar.cmd -Aplicar   -> baixa, confere SHA-256, faz backup e atualiza mantendo modelos/.venv/cortes
rem   atualizar.cmd -Voltar    -> restaura a versao anterior de versoes_antigas\
rem   Dois cliques (sem args)  -> roda -Aplicar e pausa no final
setlocal
cd /d "%~dp0"
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0instalador\atualizar.ps1" %*
set ERRO=%ERRORLEVEL%
if "%~1"=="" if "%CORTELIVE_SEM_PAUSA%"=="" pause
exit /b %ERRO%
