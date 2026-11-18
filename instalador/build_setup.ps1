$ErrorActionPreference = 'Stop'
$raiz = Split-Path -Parent $PSScriptRoot
$fonte = Join-Path $PSScriptRoot 'setup'
$dist = Join-Path $raiz 'dist'
$saida = Join-Path $dist 'CorteLive-Setup.exe'
$temporario = Join-Path $dist ('setup-' + [guid]::NewGuid().ToString('N') + '.exe')

$framework = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319'
if (-not (Test-Path -LiteralPath (Join-Path $framework 'csc.exe'))) {
  $framework = Join-Path $env:WINDIR 'Microsoft.NET\Framework\v4.0.30319'
}
$csc = Join-Path $framework 'csc.exe'
if (-not (Test-Path -LiteralPath $csc)) { throw 'Compilador .NET Framework csc.exe indisponivel.' }

New-Item -ItemType Directory -Path $dist -Force | Out-Null
try {
  $opcoes = @(
    '/nologo', '/target:winexe', '/optimize+', '/codepage:65001',
    "/out:$temporario",
    "/win32manifest:$(Join-Path $fonte 'app.manifest')",
    "/win32icon:$(Join-Path $raiz 'painel\img\cortelive.ico')"
  )
  foreach ($dll in 'System', 'System.Core', 'System.Drawing', 'System.Windows.Forms',
      'System.Web.Extensions', 'System.IO.Compression', 'System.IO.Compression.FileSystem',
      'Microsoft.CSharp') {
    $opcoes += "/reference:$(Join-Path $framework ($dll + '.dll'))"
  }
  & $csc @opcoes (Join-Path $fonte 'Setup.cs')
  if ($LASTEXITCODE -ne 0) { throw 'Falha ao compilar CorteLive-Setup.exe.' }
  Copy-Item -LiteralPath $temporario -Destination $saida -Force
  Write-Output "CorteLive-Setup.exe compilado ($((Get-Item -LiteralPath $saida).Length) bytes)."
} finally {
  if (Test-Path -LiteralPath $temporario) { Remove-Item -LiteralPath $temporario -Force }
}
