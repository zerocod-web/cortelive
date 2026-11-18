# Compila a variante Dev e gera a distribuicao estavel de arquivo unico.
param(
  [switch]$SomenteEstavel,
  [string]$SaidaEstavel,
  [string]$PacoteEmbutido
)

$ErrorActionPreference = 'Stop'
$raiz = $PSScriptRoot
$dist = Join-Path $raiz 'dist'
$versao = (Get-Content -LiteralPath (Join-Path $raiz 'VERSION') -Raw -Encoding UTF8).Trim()
if ($versao -notmatch '^\d+\.\d+\.\d+$') { throw 'VERSION deve conter tres numeros separados por ponto.' }

$framework = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319'
if (-not (Test-Path -LiteralPath (Join-Path $framework 'csc.exe'))) {
  $framework = Join-Path $env:WINDIR 'Microsoft.NET\Framework\v4.0.30319'
}
$csc = Join-Path $framework 'csc.exe'
if (-not (Test-Path -LiteralPath $csc)) { throw 'Compilador .NET Framework csc.exe indisponivel.' }

function Compilar-App([string]$saida, [switch]$dev, [string]$pacote) {
  $painel = Join-Path $raiz 'painel'
  $icone = if ($dev) { 'cortelive-dev.ico' } else { 'cortelive.ico' }
  $temporaria = Join-Path $dist ('build-' + [guid]::NewGuid().ToString('N'))
  New-Item -ItemType Directory -Path $temporaria | Out-Null
  try {
    $versaoCs = Join-Path $temporaria 'Versao.cs'
    @"
using System.Reflection;
[assembly: AssemblyVersion("$versao.0")]
[assembly: AssemblyFileVersion("$versao.0")]
"@ | Set-Content -LiteralPath $versaoCs -Encoding ASCII
    $exe = Join-Path $temporaria (Split-Path -Leaf $saida)
    $paramsCsc = @(
      '/nologo', '/target:winexe', '/optimize+', '/codepage:65001',
      "/out:$exe", "/win32manifest:$(Join-Path $painel 'app.manifest')",
      "/win32icon:$(Join-Path $painel ('img\' + $icone))"
    )
    foreach ($dll in 'System', 'System.Core', 'System.Drawing', 'System.Windows.Forms',
        'System.Web.Extensions', 'System.IO.Compression', 'System.IO.Compression.FileSystem') {
      $paramsCsc += "/reference:$(Join-Path $framework ($dll + '.dll'))"
    }
    if ($dev) { $paramsCsc += '/define:MODO_DEV' }
    if ($pacote) { $paramsCsc += "/resource:$pacote,app.zip" }
    & $csc @paramsCsc (Join-Path $painel 'CorteLive.cs') $versaoCs
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $exe)) {
      throw "Falha ao compilar $saida."
    }
    Copy-Item -LiteralPath $exe -Destination $saida -Force
  } finally {
    $base = (Resolve-Path -LiteralPath $dist).Path.TrimEnd('\') + '\'
    $alvo = (Resolve-Path -LiteralPath $temporaria).Path
    if ($alvo.StartsWith($base, [StringComparison]::OrdinalIgnoreCase)) {
      Remove-Item -LiteralPath $alvo -Recurse -Force
    }
  }
}

New-Item -ItemType Directory -Force -Path $dist | Out-Null
if ($SomenteEstavel) {
  if (-not $SaidaEstavel) { throw 'Informe -SaidaEstavel para a compilacao interna.' }
  Compilar-App $SaidaEstavel -pacote $PacoteEmbutido
} else {
  $saidaDev = Join-Path $dist 'CorteLive Dev.exe'
  Compilar-App $saidaDev -dev
  $dev = Start-Process -FilePath $saidaDev -ArgumentList "--self-test --raiz `"$raiz`"" -WindowStyle Hidden -PassThru -Wait
  if ($dev.ExitCode -ne 0) { throw "Autoteste do CorteLive Dev falhou com codigo $($dev.ExitCode)." }
  Write-Output "Autoteste do Dev OK: $saidaDev"
  & (Join-Path $raiz 'instalador\empacotar.ps1')
}
