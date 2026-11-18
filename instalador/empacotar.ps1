# Gera o ZIP para atualização e o executável estável para instalação em pasta limpa.
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.IO.Compression.FileSystem

$raiz = Split-Path -Parent $PSScriptRoot
$dist = Join-Path $raiz 'dist'
$versao = (Get-Content -LiteralPath (Join-Path $raiz 'VERSION') -Raw -Encoding UTF8).Trim()
$build = Join-Path $raiz 'build_apps.ps1'
New-Item -ItemType Directory -Force -Path $dist | Out-Null
$temporaria = Join-Path $dist ('pacote-' + [guid]::NewGuid().ToString('N'))
$stage = Join-Path $temporaria 'app'
New-Item -ItemType Directory -Path $stage -Force | Out-Null

function Copiar-Arvore([string]$nome, [string[]]$excluirDirs, [string[]]$excluirArqs) {
  $origem = Join-Path $raiz $nome
  $destino = Join-Path $stage $nome
  & robocopy $origem $destino /E /XJ /XD @excluirDirs /XF @excluirArqs /NFL /NDL /NJH /NJS | Out-Null
  if ($LASTEXITCODE -ge 8) { throw "robocopy falhou em $nome com codigo $LASTEXITCODE." }
  $global:LASTEXITCODE = 0
}

try {
  $arqsRaiz = @(
    'VERSION', 'ABRIR_PAINEL.cmd', 'INSTALAR_E_RODAR.bat', 'Atualizar CorteLive.bat', 'instalar.cmd', 'cortar.cmd', 'atualizar.cmd',
    'LEIA-ME-AMIGOS.txt', 'GUIA_RAPIDO_AMIGOS.md', 'LEIA-ME_POSTAGEM.md', 'COMO_LANCAR_VERSAO.md', 'CONTRATO_ATUALIZACAO.md', 'README.md',
    'requirements.txt', 'pyproject.toml', 'THIRD_PARTY_NOTICES.md', 'build_apps.ps1'
  )
  foreach ($nome in $arqsRaiz) {
    $origem = Join-Path $raiz $nome
    if (Test-Path -LiteralPath $origem -PathType Leaf) { Copy-Item -LiteralPath $origem -Destination $stage }
  }

  $destinoInstalador = Join-Path $stage 'instalador'
  New-Item -ItemType Directory -Path $destinoInstalador | Out-Null
  foreach ($nome in 'instalar.ps1', 'atualizar.ps1', 'versoes.ps1', 'empacotar.ps1') {
    $origem = Join-Path $PSScriptRoot $nome
    if (Test-Path -LiteralPath $origem -PathType Leaf) { Copy-Item -LiteralPath $origem -Destination $destinoInstalador }
  }

  $dirsLocais = @('__pycache__', 'ferramentas_dev', '.wrangler', 'node_modules')
  $arqsLocais = @('*.pyc', '*.local.*', 'painel_config.json', 'painel_instancia*.json', '.dev.vars', '.dev.vars.*')
  Copiar-Arvore 'painel' ($dirsLocais + @(Join-Path $raiz 'painel\dev')) $arqsLocais
  Copiar-Arvore 'cortelive' $dirsLocais ($arqsLocais + @(Join-Path $raiz 'cortelive\postar\config.toml'))

  foreach ($nome in 'cortelive\pastas.py', 'LEIA-ME_POSTAGEM.md', 'GUIA_RAPIDO_AMIGOS.md', 'painel\tema_zerocod.css',
      'painel\img\cortelive.ico', 'painel\fontes\Outfit-Variable.woff2') {
    if (-not (Test-Path -LiteralPath (Join-Path $stage $nome) -PathType Leaf)) {
      throw "Arquivo necessario ausente no pacote: $nome"
    }
  }

  # O executável distribuído sozinho carrega o conteúdo do app. O executável dentro
  # do ZIP de atualização usa os arquivos instalados ao lado dele.
  $embutido = Join-Path $temporaria 'app.zip'
  [IO.Compression.ZipFile]::CreateFromDirectory($stage, $embutido, [IO.Compression.CompressionLevel]::Optimal, $false)
  & $build -SomenteEstavel -SaidaEstavel (Join-Path $stage 'CorteLive.exe')
  $zipNovo = Join-Path $temporaria 'cortelive_amigos.zip'
  [IO.Compression.ZipFile]::CreateFromDirectory($stage, $zipNovo, [IO.Compression.CompressionLevel]::Optimal, $false)

  $testeLimpo = Join-Path $temporaria 'instalacao-limpa'
  New-Item -ItemType Directory -Path $testeLimpo | Out-Null
  $exeNovo = Join-Path $testeLimpo 'CorteLive.exe'
  & $build -SomenteEstavel -SaidaEstavel $exeNovo -PacoteEmbutido $embutido
  $teste = Start-Process -FilePath $exeNovo -ArgumentList '--self-test' -WindowStyle Hidden -PassThru -Wait
  if ($teste.ExitCode -ne 0 -or -not (Test-Path -LiteralPath (Join-Path $testeLimpo 'cortelive\pastas.py'))) {
    throw "Autoteste do executavel em pasta limpa falhou ($($teste.ExitCode))."
  }

  $exe = Join-Path $dist 'CorteLive.exe'
  $zip = Join-Path $dist 'cortelive_amigos.zip'
  Copy-Item -LiteralPath $exeNovo -Destination $exe -Force
  Copy-Item -LiteralPath $zipNovo -Destination $zip -Force
  foreach ($arquivo in $exe, $zip) {
    $hash = (Get-FileHash -LiteralPath $arquivo -Algorithm SHA256).Hash
    "$hash  $(Split-Path -Leaf $arquivo)" | Set-Content -LiteralPath "$arquivo.sha256" -Encoding ASCII
  }

  try {
    Copy-Item -LiteralPath $exe -Destination (Join-Path $raiz 'CorteLive.exe') -Force
  } catch {
    Write-Host 'O executavel na raiz estava em uso. A copia em dist foi atualizada.'
  }

  Write-Output "v$versao empacotada. Autoteste em pasta limpa OK."
  Write-Output $exe
  Write-Output $zip
} finally {
  $base = (Resolve-Path -LiteralPath $dist).Path.TrimEnd('\') + '\'
  $alvo = (Resolve-Path -LiteralPath $temporaria).Path
  if ($alvo.StartsWith($base, [StringComparison]::OrdinalIgnoreCase)) {
    Remove-Item -LiteralPath $alvo -Recurse -Force
  }
}
