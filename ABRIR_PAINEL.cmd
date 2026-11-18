@echo off
rem Abre o Painel visual do CorteLive (sem precisar de administrador).
setlocal
set "RAIZ=%~dp0"
if exist "%RAIZ%CorteLive.exe" (
  start "" "%RAIZ%CorteLive.exe"
  exit /b 0
)
if exist "%RAIZ%dist\CorteLive.exe" (
  start "" "%RAIZ%dist\CorteLive.exe" --raiz "%RAIZ%."
  exit /b 0
)
echo Compilando CorteLive.exe pela primeira vez...
powershell -NoProfile -ExecutionPolicy Bypass -File "%RAIZ%instalador\empacotar.ps1"
if exist "%RAIZ%CorteLive.exe" (
  start "" "%RAIZ%CorteLive.exe"
  exit /b 0
)
echo Nao foi possivel abrir o painel. Use INSTALAR_E_RODAR.bat.
pause
exit /b 1
