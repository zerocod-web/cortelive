@echo off
rem Atualizar CorteLive - dois cliques. Fecha o CorteLive, baixa a versao mais nova e instala por cima.
chcp 65001 >nul
title Atualizar CorteLive
set "CL_BAT=%~f0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "& ([scriptblock]::Create((([IO.File]::ReadAllText($env:CL_BAT,[Text.Encoding]::UTF8)) -split '(?m)^#PS#\r?\n',2)[1]))"
set "CL_SAIDA=%errorlevel%"
echo.
pause
exit /b %CL_SAIDA%
#PS#
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}
try { [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12 } catch {}

function Titulo($t) { Write-Host ''; Write-Host $t -ForegroundColor Cyan }
function Falha($msg) {
  Write-Host ''
  Write-Host "NÃO FOI POSSÍVEL ATUALIZAR: $msg" -ForegroundColor Red
  Write-Host 'Seus modelos, cortes e configurações não foram apagados. Tente de novo; se continuar, fale com o Gabriel.' -ForegroundColor Yellow
  exit 1
}

Write-Host '=============================================' -ForegroundColor Cyan
Write-Host '   Atualizar o CorteLive' -ForegroundColor Cyan
Write-Host '=============================================' -ForegroundColor Cyan
Write-Host 'Este arquivo vai:'
Write-Host '  1. fechar o CorteLive (e tudo que ele abriu);'
Write-Host '  2. baixar a versão mais nova e conferir a segurança do arquivo;'
Write-Host '  3. instalar por cima, sem apagar modelos, cortes e configurações;'
Write-Host '  4. abrir o CorteLive atualizado.'
Write-Host 'Não precisa de administrador. Pode levar alguns minutos.'

# ---- 1) achar a instalação ----
$pasta = $null
if ($env:CORTELIVE_PASTA) { $pasta = $env:CORTELIVE_PASTA.Trim().TrimEnd('\') }
else {
  $padrao = Join-Path $env:LOCALAPPDATA 'CorteLive'
  $achadas = @()
  try {
    foreach ($k in Get-ChildItem 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall' -ErrorAction SilentlyContinue) {
      $p = Get-ItemProperty -LiteralPath $k.PSPath -ErrorAction SilentlyContinue
      if ($p -and $p.DisplayName -eq 'CorteLive' -and $p.InstallLocation -and (Test-Path -LiteralPath (Join-Path $p.InstallLocation 'CorteLive.exe'))) { $achadas += $p.InstallLocation.TrimEnd('\') }
    }
  } catch {}
  if ($achadas -contains $padrao) { $pasta = $padrao }
  elseif ($achadas.Count -gt 0) { $pasta = $achadas[0] }
  elseif (Test-Path -LiteralPath (Join-Path $padrao 'CorteLive.exe')) { $pasta = $padrao }
}
if (-not $pasta -or -not (Test-Path -LiteralPath (Join-Path $pasta 'CorteLive.exe'))) {
  Falha 'não encontrei o CorteLive instalado neste computador. Se ainda não instalou, baixe e abra o CorteLive-Setup.exe.'
}
$pasta = [IO.Path]::GetFullPath($pasta).TrimEnd('\')
$arqVersao = Join-Path $pasta 'VERSION'
$antes = if (Test-Path -LiteralPath $arqVersao) { (Get-Content -LiteralPath $arqVersao -Raw).Trim() } else { '?' }
Titulo "CorteLive encontrado em: $pasta (versão $antes)"

# ---- 2) ver se já está na versão mais nova (antes de fechar qualquer coisa) ----
Titulo 'Verificando a versão mais nova no GitHub…'
$base = 'https://github.com/zerocod-web/cortelive/releases/latest/download/'
if ($env:CORTELIVE_URL_BASE) { $base = $env:CORTELIVE_URL_BASE }
$urlUltima = 'https://github.com/zerocod-web/cortelive/releases/latest'
if ($env:CORTELIVE_ULTIMA_URL) { $urlUltima = $env:CORTELIVE_ULTIMA_URL }
$remota = $null
try {
  $rq = [Net.HttpWebRequest]::Create($urlUltima); $rq.AllowAutoRedirect = $false; $rq.Timeout = 15000; $rq.UserAgent = 'CorteLive-Atualizar'
  $rs = $rq.GetResponse()
  try { $loc = [string]$rs.Headers['Location'] } finally { $rs.Close() }
  $m = [regex]::Match($loc, '/tag/v?([0-9][0-9A-Za-z\.\-]*)\s*$')
  if ($m.Success) { $remota = $m.Groups[1].Value }
} catch [Net.WebException] {
  $rs = $_.Exception.Response
  if ($rs -and [int]$rs.StatusCode -ge 300 -and [int]$rs.StatusCode -lt 400) {
    $m = [regex]::Match([string]$rs.Headers['Location'], '/tag/v?([0-9][0-9A-Za-z\.\-]*)\s*$')
    if ($m.Success) { $remota = $m.Groups[1].Value }
  } else { Falha 'não consegui falar com o GitHub. Confira se o computador está com internet e tente de novo.' }
} catch { Falha 'não consegui falar com o GitHub. Confira se o computador está com internet e tente de novo.' }
if (-not $remota) { Falha 'não consegui descobrir qual é a versão mais nova. Confira a internet e tente de novo.' }
Write-Host "Versão mais nova: $remota   |   a sua: $antes"
if ($antes -eq $remota) {
  Write-Host ''
  Write-Host "O CorteLive já está na versão mais nova (v$antes). Nada a fazer." -ForegroundColor Green
  exit 0
}

# ---- 3) baixar o Setup da última versão e conferir ----
$tmp = Join-Path ([IO.Path]::GetTempPath()) ('CorteLive-atualizar-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $tmp -Force | Out-Null
$setup = Join-Path $tmp 'CorteLive-Setup.exe'
Titulo 'Baixando a versão mais nova…'
try {
  $web = New-Object Net.WebClient
  $web.DownloadFile($base + 'CorteLive-Setup.exe', $setup)
  $selo = $web.DownloadString($base + 'CorteLive-Setup.exe.sha256')
} catch {
  Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
  Falha 'o download falhou. Confira a internet (e se o GitHub abre no navegador) e tente de novo.'
}
$esperado = [regex]::Match($selo.Trim(), '\A[0-9a-fA-F]{64}').Value
$real = (Get-FileHash -LiteralPath $setup -Algorithm SHA256).Hash
if (-not $esperado -or $real -ine $esperado) {
  Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
  Falha 'o arquivo baixado não passou na conferência de segurança (SHA-256 diferente). Baixe de novo; se repetir, avise o Gabriel.'
}
Write-Host 'Arquivo conferido (SHA-256 confere).' -ForegroundColor Green

# ---- 4) fechar tudo desta instalação (nunca outros Edge/Python do usuário) ----
function Processos-Da-Instalacao {
  $raiz = $pasta + '\'
  $perfil = $pasta + '\webview_'
  $nomes = 'CorteLive.exe','msedge.exe','chrome.exe','python.exe','pythonw.exe','ffmpeg.exe','streamlink.exe','yt-dlp.exe'
  $lista = @()
  foreach ($p in (Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)) {
    if ($nomes -notcontains $p.Name.ToLowerInvariant() -and $nomes -notcontains $p.Name) { continue }
    $exe = [string]$p.ExecutablePath; $cmd = [string]$p.CommandLine
    $n = $p.Name.ToLowerInvariant()
    if ($n -eq 'msedge.exe' -or $n -eq 'chrome.exe') { $meu = $cmd.IndexOf($perfil, [StringComparison]::OrdinalIgnoreCase) -ge 0 }
    else { $meu = $exe.StartsWith($raiz, [StringComparison]::OrdinalIgnoreCase) -or $cmd.IndexOf($raiz, [StringComparison]::OrdinalIgnoreCase) -ge 0 }
    if ($meu) { $lista += $p }
  }
  return $lista
}

Titulo 'Fechando o CorteLive…'
$abertos = @(Processos-Da-Instalacao)
if ($abertos.Count -gt 0) {
  # primeiro pede para o painel sair de forma limpa
  try {
    $inst = Join-Path $pasta 'painel_instancia.json'
    if (Test-Path -LiteralPath $inst) {
      $dados = (Get-Content -LiteralPath $inst -Raw -Encoding UTF8).TrimStart([char]0xFEFF) | ConvertFrom-Json
      if ($dados.porta) {
        Invoke-WebRequest -UseBasicParsing -Method Post -Uri "http://127.0.0.1:$($dados.porta)/api/encerrar" -Body '{}' -ContentType 'application/json' -TimeoutSec 4 | Out-Null
      }
    }
  } catch {}
  $limite = (Get-Date).AddSeconds(10)
  while ((Get-Date) -lt $limite -and @(Processos-Da-Instalacao).Count -gt 0) { Start-Sleep -Milliseconds 500 }
  # o que sobrou, fecha à força (só desta instalação)
  foreach ($p in @(Processos-Da-Instalacao)) {
    if ($p.Name -ieq 'CorteLive.exe') { & taskkill.exe /PID $p.ProcessId /T /F 2>$null | Out-Null }
  }
  Start-Sleep -Seconds 1
  foreach ($p in @(Processos-Da-Instalacao)) { try { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue } catch {} }
  Start-Sleep -Seconds 1
  if (@(Processos-Da-Instalacao).Count -gt 0) { Falha 'não consegui fechar o CorteLive. Feche o painel (ícone ao lado do relógio, botão direito, Sair) e tente de novo.' }
  Write-Host 'CorteLive fechado.' -ForegroundColor Green
} else { Write-Host 'O CorteLive já estava fechado.' -ForegroundColor Green }

# ---- 5) rodar o Setup por cima, na mesma pasta ----
Titulo 'Instalando por cima (o instalador abre uma janela, é normal)…'
Add-Type -TypeDefinition @"
using System; using System.Text; using System.Collections.Generic; using System.Runtime.InteropServices;
public static class CLW {
  public delegate bool EnumProc(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] static extern bool EnumWindows(EnumProc p, IntPtr l);
  [DllImport("user32.dll")] static extern bool EnumChildWindows(IntPtr h, EnumProc p, IntPtr l);
  [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern int GetClassName(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern IntPtr SendMessage(IntPtr h, uint m, IntPtr w, string l);
  [DllImport("user32.dll")] static extern IntPtr SendMessage(IntPtr h, uint m, IntPtr w, IntPtr l);
  public static string Text(IntPtr h) { var s = new StringBuilder(512); GetWindowText(h, s, 512); return s.ToString(); }
  public static string Cls(IntPtr h) { var s = new StringBuilder(256); GetClassName(h, s, 256); return s.ToString(); }
  public static IntPtr Principal(uint pid) {
    IntPtr r = IntPtr.Zero;
    EnumWindows(delegate(IntPtr h, IntPtr l) { uint p; GetWindowThreadProcessId(h, out p); if (p == pid && Cls(h).StartsWith("WindowsForms") && Text(h).Length > 0) { r = h; return false; } return true; }, IntPtr.Zero);
    return r;
  }
  public static List<IntPtr> Filhos(IntPtr j) { var l = new List<IntPtr>(); EnumChildWindows(j, delegate(IntPtr h, IntPtr x) { l.Add(h); return true; }, IntPtr.Zero); return l; }
  public static void Escrever(IntPtr h, string s) { SendMessage(h, 0x000C, IntPtr.Zero, s); }
  public static void Clicar(IntPtr h) { SendMessage(h, 0x00F5, IntPtr.Zero, IntPtr.Zero); }
}
"@
$proc = Start-Process -FilePath $setup -PassThru
$janela = [IntPtr]::Zero
$limite = (Get-Date).AddSeconds(45)
while ((Get-Date) -lt $limite -and $janela -eq [IntPtr]::Zero) {
  if ($proc.HasExited) { break }
  $janela = [CLW]::Principal([uint32]$proc.Id)
  if ($janela -eq [IntPtr]::Zero) { Start-Sleep -Milliseconds 300 }
}
function Botao($nomeRegex) { foreach ($h in [CLW]::Filhos($janela)) { if ([CLW]::Cls($h) -like '*BUTTON*' -and [CLW]::Text($h) -match $nomeRegex) { return $h } }; return [IntPtr]::Zero }
$automatico = $false
if ($janela -ne [IntPtr]::Zero) {
  try {
    $campo = [IntPtr]::Zero
    foreach ($h in [CLW]::Filhos($janela)) { if ([CLW]::Cls($h) -like '*EDIT*') { $campo = $h; break } }
    $botao = Botao '^&?Instalar$'
    if ($campo -ne [IntPtr]::Zero -and $botao -ne [IntPtr]::Zero) {
      [CLW]::Escrever($campo, $pasta)
      Start-Sleep -Milliseconds 500
      [CLW]::Clicar($botao)
      $automatico = $true
    }
  } catch {}
}
if (-not $automatico) {
  Write-Host 'Na janela do instalador, confira a pasta e clique em "Instalar".' -ForegroundColor Yellow
}
$resultado = $null
$limite = (Get-Date).AddMinutes(15)
while ((Get-Date) -lt $limite -and -not $resultado) {
  if ($proc.HasExited) { $resultado = 'saiu'; break }
  if ($automatico) {
    $b = Botao '^(&?Fechar|Tentar &?novamente)$'
    if ($b -ne [IntPtr]::Zero) {
      if ([CLW]::Text($b) -match 'Fechar') { $resultado = 'ok'; [CLW]::Clicar($b) } else { $resultado = 'erro' }
    }
  }
  if (-not $resultado) { Start-Sleep -Milliseconds 700 }
}
if ($resultado -eq 'erro') {
  try { $proc.Kill() } catch {}
  Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
  Falha "o instalador avisou um erro. O motivo está em: $pasta\setup_log.txt"
}
if (-not $proc.HasExited) { $proc.WaitForExit(15000) | Out-Null }
Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue

# ---- 6) conferir e avisar ----
$depois = if (Test-Path -LiteralPath $arqVersao) { (Get-Content -LiteralPath $arqVersao -Raw).Trim() } else { '' }
if (-not $depois) { Falha "não consegui confirmar a nova versão. Veja: $pasta\setup_log.txt" }
if ($depois -eq $antes -and $resultado -ne 'ok') { Falha "a versão continua $antes. Veja: $pasta\setup_log.txt" }
Write-Host ''
Write-Host "Pronto, CorteLive atualizado para v$depois" -ForegroundColor Green
Write-Host 'O painel já está abrindo. Seus modelos, cortes e configurações foram mantidos.' -ForegroundColor Green
exit 0
