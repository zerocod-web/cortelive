# Atualizador automatico do CorteLive com backup e rollback.
# Modos (mutuamente exclusivos; padrao sem argumentos = -Aplicar):
#   -Checar   : consulta a API publica de releases do GitHub (sem token), com timeout e cache.
#   -Aplicar  : baixa o zip da release, confere o SHA-256 publicado, faz backup em versoes_antigas\,
#               troca apenas o codigo preservando .venv, python, ffmpeg, modelos, cortes e configs do usuario,
#               roda instalar.ps1 -SemPerguntas se requirements.txt ou versoes.ps1 mudarem,
#               valida a inicializacao e faz rollback automatico se falhar.
#   -Voltar   : restaura a versao anterior guardada em versoes_antigas\.
# Saida stdout: exclusivamente linhas JSON UTF-8 {etapa, pct, msg, versao_atual, versao_nova, erro}.

param(
  [switch]$Checar,
  [switch]$Aplicar,
  [switch]$Voltar,
  [ValidateSet('', 'estavel', 'beta')]
  [string]$Canal = '',
  [string]$Raiz = '',
  [string]$ApiBase = '',
  [string]$Repo = '',
  [int]$TimeoutSeg = 0,
  [switch]$SemCache
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$utf8SemBom = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = $utf8SemBom
$OutputEncoding = $utf8SemBom

if (-not $Raiz) {
  $Raiz = Split-Path -Parent $PSScriptRoot
}
$RAIZ = [IO.Path]::GetFullPath($Raiz)
$PASTA_ANTIGAS = Join-Path $RAIZ 'versoes_antigas'
$ARQ_ULTIMA    = Join-Path $PASTA_ANTIGAS 'ultima.txt'
# .exe que estava aberto durante a troca: fica aqui (fora dos backups) ate o app reabrir e soltar o arquivo.
$PASTA_EM_USO  = Join-Path $PASTA_ANTIGAS '_em_uso'
$TMP_UPDATE    = Join-Path $RAIZ '_update_tmp'
$LOG_UPDATE    = Join-Path $RAIZ 'atualizacao.log'
$ARQ_CACHE     = Join-Path $RAIZ 'instalador\cache_release.local.json'

$PASTAS_PROTEGIDAS = @(
  '.venv', 'venv', 'python', 'ffmpeg', 'ferramentas', 'modelos', 'cortes',
  'versoes_antigas', '_downloads', '_update_tmp', '.buffer', 'logs', 'dist',
  '.git', '.github', '.githooks', '.agents', '.codex', '.gemini', '.overclock-app',
  'qa', 'testes', 'teste_video', 'cortelive.egg-info', 'build', '.pytest_cache',
  'contas', 'secrets', 'tokens', 'cookies', 'perfil_navegador', 'perfil-browser', 'navegador',
  'webview_app', 'webview_dev', 'webview_ao_vivo'
)

$ARQS_USUARIO_RAIZ = @(
  'config.toml', 'postar.toml', 'usuario.toml', 'lives.txt',
  'painel_config.json', 'instalacao_log.txt', 'atualizacao.log', '.gitignore',
  'setup_log.txt', 'cams.json', 'encoder.json'
)

function Eh-ArquivoUsuarioProtegido([string]$nomeArq) {
  $n = $nomeArq.ToLowerInvariant()
  foreach ($p in $ARQS_USUARIO_RAIZ) {
    if ($n -eq $p.ToLowerInvariant()) { return $true }
  }
  if ($n -like '*.local.*' -or $n -like 'config.local*' -or $n -like '*.log' -or
      $n -like '.env*' -or $n -like '*token*' -or $n -like '*cookie*' -or
      $n -like 'client_secret*' -or $n -like 'credentials*' -or $n -like 'auth*.json' -or
      $n -like '*.key' -or $n -like '*.pem' -or $n -like '*.pfx' -or $n -like '*.p12') {
    return $true
  }
  return $false
}

function Eh-PastaProtegida([string]$nomePasta) {
  $n = $nomePasta.ToLowerInvariant()
  foreach ($p in $PASTAS_PROTEGIDAS) {
    if ($n -eq $p.ToLowerInvariant()) { return $true }
  }
  # Perfil do Edge do painel (cookies, cache, login): dado do usuario e o msedge filho do app o mantem
  # aberto/travado durante a atualizacao. Nunca copiar, mover nem apagar.
  if ($n -like 'sessao_login*' -or $n -like '*.egg-info' -or $n -like 'webview_*') { return $true }
  return $false
}

function Registrar-Log([string]$texto) {
  try {
    "[$(Get-Date -Format s)] $texto" | Out-File -FilePath $LOG_UPDATE -Append -Encoding utf8
  } catch {}
}

function Emitir-Evento {
  param(
    [string]$Etapa,
    [int]$Pct,
    [string]$Msg,
    [string]$VersaoAtual,
    [object]$VersaoNova = $null,
    [object]$Erro = $null
  )
  $pctLim = [Math]::Max(0, [Math]::Min(100, $Pct))
  $vn = if ($null -eq $VersaoNova -or "$VersaoNova" -eq '') { $null } else { [string]$VersaoNova }
  $er = if ($null -eq $Erro -or "$Erro" -eq '') { $null } else { [string]$Erro }
  $ev = [ordered]@{
    etapa        = $Etapa
    pct          = $pctLim
    msg          = $Msg
    versao_atual = $VersaoAtual
    versao_nova  = $vn
    erro         = $er
  }
  $linha = $ev | ConvertTo-Json -Compress -Depth 4
  [Console]::Out.WriteLine($linha)
  [Console]::Out.Flush()
  Registrar-Log "EVENTO $linha"
}

function Ler-Versao([string]$pasta) {
  $arq = Join-Path $pasta 'VERSION'
  if (Test-Path $arq) {
    $v = (Get-Content $arq -Raw -Encoding UTF8).Trim().TrimStart('v', 'V')
    if ($v) { return $v }
  }
  return '0.0.0'
}

function Separar-SemVer([string]$v) {
  $limpo = $v.Trim().TrimStart('v', 'V')
  $maisIdx = $limpo.IndexOf('+')
  if ($maisIdx -ge 0) { $limpo = $limpo.Substring(0, $maisIdx) }
  $pre = ''
  $core = $limpo
  $hifenIdx = $limpo.IndexOf('-')
  if ($hifenIdx -ge 0) {
    $core = $limpo.Substring(0, $hifenIdx)
    $pre  = $limpo.Substring($hifenIdx + 1)
  }
  $nums = @($core -split '[^0-9]+' | Where-Object { $_ -ne '' } | ForEach-Object { [int]$_ })
  return @{ nums = $nums; pre = $pre }
}

function Comparar-SemVer([string]$a, [string]$b) {
  $sa = Separar-SemVer $a
  $sb = Separar-SemVer $b
  $pa = $sa.nums
  $pb = $sb.nums
  $n = [Math]::Max($pa.Count, $pb.Count)
  for ($i = 0; $i -lt $n; $i++) {
    $va = if ($i -lt $pa.Count) { $pa[$i] } else { 0 }
    $vb = if ($i -lt $pb.Count) { $pb[$i] } else { 0 }
    if ($va -gt $vb) { return 1 }
    if ($va -lt $vb) { return -1 }
  }
  $temPreA = ($sa.pre -ne '')
  $temPreB = ($sb.pre -ne '')
  if (-not $temPreA -and -not $temPreB) { return 0 }
  if (-not $temPreA -and $temPreB)      { return 1 }
  if ($temPreA -and -not $temPreB)      { return -1 }

  $ta = @($sa.pre -split '\.')
  $tb = @($sb.pre -split '\.')
  $m = [Math]::Max($ta.Count, $tb.Count)
  for ($j = 0; $j -lt $m; $j++) {
    if ($j -ge $ta.Count) { return -1 }
    if ($j -ge $tb.Count) { return 1 }
    $ia = 0; $ib = 0
    $numA = [int]::TryParse($ta[$j], [ref]$ia)
    $numB = [int]::TryParse($tb[$j], [ref]$ib)
    if ($numA -and $numB) {
      if ($ia -gt $ib) { return 1 }
      if ($ia -lt $ib) { return -1 }
    } elseif ($numA -and -not $numB) {
      return -1
    } elseif (-not $numA -and $numB) {
      return 1
    } else {
      $cmp = [string]::Compare($ta[$j], $tb[$j], [StringComparison]::OrdinalIgnoreCase)
      if ($cmp -gt 0) { return 1 }
      if ($cmp -lt 0) { return -1 }
    }
  }
  return 0
}

function Hash-Sha256([string]$caminho) {
  if (Test-Path $caminho) {
    return (Get-FileHash -Path $caminho -Algorithm SHA256).Hash.ToUpperInvariant()
  }
  return ''
}

function Extrair-RepoDeRemote([string]$pasta) {
  $gitDir = Join-Path $pasta '.git'
  if (-not (Test-Path $gitDir)) { return '' }
  $gitCmd = (Get-Command git.exe -ErrorAction SilentlyContinue).Source
  if (-not $gitCmd) { return '' }
  try {
    $rem = (& $gitCmd -C $pasta remote get-url origin 2>$null | Out-String).Trim()
    if ($rem -match 'github\.com[:/]([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(\.git)?$') {
      return $Matches[1]
    }
  } catch {}
  return ''
}

function Extrair-RepoDeGh([string]$pasta) {
  $cands = @(
    (Get-Command gh.exe -ErrorAction SilentlyContinue).Source,
    'C:\Program Files\GitHub CLI\gh.exe'
  ) | Where-Object { $_ -and (Test-Path $_) }
  if (-not $cands -or -not (Test-Path (Join-Path $pasta '.git'))) { return '' }
  try {
    Push-Location $pasta
    try {
      $out = (& $cands[0] repo view --json owner,name 2>$null | Out-String).Trim()
      if ($LASTEXITCODE -eq 0 -and $out) {
        $j = $out | ConvertFrom-Json
        if ($j.owner.login -and $j.name) {
          return "$($j.owner.login)/$($j.name)"
        }
      }
    } finally {
      Pop-Location
    }
  } catch {}
  return ''
}

function Obter-ConfigUpdate {
  $repoCfg = ''
  $versoesArq = Join-Path $RAIZ 'instalador\versoes.ps1'
  if (Test-Path $versoesArq) {
    $escopo = & {
      . $versoesArq
      return $REPO_GITHUB
    }
    if ($escopo) { $repoCfg = [string]$escopo }
  }

  if ($env:CORTELIVE_REPO) { $repoCfg = $env:CORTELIVE_REPO }
  if ($Repo)               { $repoCfg = $Repo }
  $repoCfg = $repoCfg.Trim()

  if (-not $repoCfg -or $repoCfg -match '<dono>' -or $repoCfg -match '^DONO/') {
    $viaGit = Extrair-RepoDeRemote $RAIZ
    if ($viaGit) { $repoCfg = $viaGit }
    else {
      $viaGh = Extrair-RepoDeGh $RAIZ
      if ($viaGh) { $repoCfg = $viaGh }
    }
  }

  $baseCfg = 'https://api.github.com'
  $endpointDireto = ''
  if ($env:CORTELIVE_RELEASE_API_URL) {
    $u = $env:CORTELIVE_RELEASE_API_URL.Trim()
    if ($u -match '/releases/latest$' -or $u -match '\.json$') {
      $endpointDireto = $u
    } else {
      $baseCfg = $u
    }
  }
  if ($env:CORTELIVE_API_BASE) { $baseCfg = $env:CORTELIVE_API_BASE.Trim() }
  if ($ApiBase)                { $baseCfg = $ApiBase.Trim() }
  $baseCfg = $baseCfg.TrimEnd('/')

  $tSeg = 15
  if ($env:CORTELIVE_TIMEOUT_S -and ($env:CORTELIVE_TIMEOUT_S -as [int]) -gt 0) {
    $tSeg = [int]$env:CORTELIVE_TIMEOUT_S
  }
  if ($TimeoutSeg -gt 0) { $tSeg = $TimeoutSeg }

  $ttlSeg = 900
  if ($null -ne $env:CORTELIVE_CACHE_TTL_S -and $env:CORTELIVE_CACHE_TTL_S -ne '') {
    $parsedTtl = 0
    if ([int]::TryParse($env:CORTELIVE_CACHE_TTL_S, [ref]$parsedTtl) -and $parsedTtl -ge 0) {
      $ttlSeg = $parsedTtl
    }
  }

  $canalCfg = 'estavel'
  if ($env:CORTELIVE_CANAL) {
    $candCanal = $env:CORTELIVE_CANAL.Trim().ToLowerInvariant()
    if ($candCanal -eq 'beta' -or $candCanal -eq 'estavel') { $canalCfg = $candCanal }
  }
  if ($Canal) {
    $canalCfg = $Canal.Trim().ToLowerInvariant()
  }

  return @{
    repo            = $repoCfg
    api_base        = $baseCfg
    endpoint_direto = $endpointDireto
    timeout_s       = $tSeg
    cache_ttl_s     = $ttlSeg
    canal           = $canalCfg
  }
}

function Ler-CacheRelease([string]$chaveEndpoint) {
  if (-not (Test-Path $ARQ_CACHE)) { return $null }
  try {
    $c = Get-Content $ARQ_CACHE -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($c.endpoint -eq $chaveEndpoint -and $c.payload) {
      return $c
    }
  } catch {}
  return $null
}

function Gravar-CacheRelease([string]$chaveEndpoint, [string]$etag, $payloadObj) {
  try {
    $pai = Split-Path -Parent $ARQ_CACHE
    if (-not (Test-Path $pai)) { New-Item -ItemType Directory -Force $pai | Out-Null }
    $dados = [ordered]@{
      endpoint     = $chaveEndpoint
      etag         = $etag
      consultado_s = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
      payload      = $payloadObj
    }
    [IO.File]::WriteAllText($ARQ_CACHE, ($dados | ConvertTo-Json -Depth 8), $utf8SemBom)
  } catch {}
}

function Selecionar-ReleaseDoCanal($payloadBruto, [string]$canal, [string]$repoNome) {
  if ($payloadBruto -is [System.Array]) {
    $validas = @($payloadBruto | Where-Object {
      $null -ne $_ -and (-not $_.draft) -and $_.tag_name -and
      ($canal -eq 'beta' -or (-not $_.prerelease))
    })
    if ($validas.Count -eq 0) {
      throw "Nenhuma release publicada encontrada para o canal '$canal' em $repoNome."
    }
    $melhor = $validas[0]
    for ($i = 1; $i -lt $validas.Count; $i++) {
      if ((Comparar-SemVer ([string]$validas[$i].tag_name) ([string]$melhor.tag_name)) -gt 0) {
        $melhor = $validas[$i]
      }
    }
    return $melhor
  }
  return $payloadBruto
}

function Montar-InfoRelease($relObjBruto, [string]$verAtual, [string]$canal = 'estavel', [string]$repoNome = '') {
  $relObj = Selecionar-ReleaseDoCanal $relObjBruto $canal $repoNome
  $tag = ([string]$relObj.tag_name).Trim()
  if (-not $tag) {
    throw 'Resposta da API de releases não contém tag_name.'
  }
  $verNova = $tag.TrimStart('v', 'V')
  $temNova = (Comparar-SemVer $verNova $verAtual) -gt 0
  $ehPre = [bool]$relObj.prerelease

  $zipUrl = ''
  $zipNome = ''
  $shaUrl = ''
  if ($relObj.assets) {
    foreach ($a in $relObj.assets) {
      if ($a.name -eq 'cortelive_amigos.zip') {
        $zipUrl = [string]$a.browser_download_url
        $zipNome = [string]$a.name
      } elseif ($a.name -eq 'cortelive_amigos.zip.sha256') {
        $shaUrl = [string]$a.browser_download_url
      }
    }
    if (-not $zipUrl) {
      $candZip = $relObj.assets | Where-Object { $_.name -like '*.zip' } | Select-Object -First 1
      if ($candZip) {
        $zipUrl = [string]$candZip.browser_download_url
        $zipNome = [string]$candZip.name
      }
    }
    if (-not $shaUrl -and $zipNome) {
      $candSha = $relObj.assets | Where-Object { $_.name -eq "$zipNome.sha256" -or $_.name -like '*.sha256' } | Select-Object -First 1
      if ($candSha) {
        $shaUrl = [string]$candSha.browser_download_url
      }
    }
  }

  return @{
    tag          = $tag
    versao_atual = $verAtual
    versao_nova  = $verNova
    disponivel   = $temNova
    prerelease   = $ehPre
    canal        = $canal
    zip_url      = $zipUrl
    sha_url      = $shaUrl
  }
}

function Consultar-Release([switch]$IgnorarTtl) {
  $verAtual = Ler-Versao $RAIZ
  $cfg = Obter-ConfigUpdate

  $endpoint = $cfg.endpoint_direto
  if (-not $endpoint) {
    if (-not $cfg.repo -or $cfg.repo -match '<dono>' -or $cfg.repo -match '^DONO/') {
      throw 'Repositório GitHub ainda não configurado em instalador\versoes.ps1 ($REPO_GITHUB = "<dono>/cortelive").'
    }
    if ($cfg.canal -eq 'beta') {
      $endpoint = "$($cfg.api_base)/repos/$($cfg.repo)/releases?per_page=15"
    } else {
      $endpoint = "$($cfg.api_base)/repos/$($cfg.repo)/releases/latest"
    }
  }

  $agoraS = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
  $cache = $null
  if (-not $SemCache) {
    $cache = Ler-CacheRelease $endpoint
    if ($cache -and -not $IgnorarTtl -and $cfg.cache_ttl_s -gt 0) {
      $idade = $agoraS - [long]$cache.consultado_s
      if ($idade -ge 0 -and $idade -lt $cfg.cache_ttl_s) {
        Registrar-Log "CACHE HIT (${idade}s < $($cfg.cache_ttl_s)s) $endpoint"
        return Montar-InfoRelease $cache.payload $verAtual $cfg.canal $cfg.repo
      }
    }
  }

  $tmpHttp = Join-Path ([IO.Path]::GetTempPath()) ("cortelive_chk_" + [Guid]::NewGuid().ToString('N'))
  New-Item -ItemType Directory -Force $tmpHttp | Out-Null
  $arqHdr  = Join-Path $tmpHttp 'headers.txt'
  $arqBody = Join-Path $tmpHttp 'body.json'

  try {
    $connTimeout = [Math]::Min(8, [Math]::Max(2, $cfg.timeout_s))
    $curlArgs = @(
      '-sS', '-L',
      '--connect-timeout', "$connTimeout",
      '--max-time', "$($cfg.timeout_s)",
      '-D', $arqHdr,
      '-o', $arqBody,
      '-w', '%{http_code}',
      '-H', 'Accept: application/vnd.github+json',
      '-H', "User-Agent: CorteLive-Updater/$verAtual"
    )
    if ($cache -and $cache.etag -and -not $SemCache) {
      $curlArgs += @('-H', "If-None-Match: $($cache.etag)")
    }
    $curlArgs += $endpoint

    $httpCodeRaw = & curl.exe @curlArgs 2>&1 | Out-String
    $curlExit = $LASTEXITCODE
    $httpCode = 0
    if ($httpCodeRaw.Trim() -match '(\d{3})\s*$') {
      $httpCode = [int]$Matches[1]
    }

    if ($curlExit -ne 0) {
      if ($curlExit -eq 28) {
        throw "Tempo esgotado (${cfg.timeout_s}s) ao consultar atualizações em $endpoint."
      }
      throw "Sem conexão ou falha de rede ao consultar atualizações ($($httpCodeRaw.Trim()))."
    }

    if ($httpCode -eq 304 -and $cache -and $cache.payload) {
      Gravar-CacheRelease $endpoint ([string]$cache.etag) $cache.payload
      return Montar-InfoRelease $cache.payload $verAtual $cfg.canal $cfg.repo
    }

    if ($httpCode -eq 404) {
      throw "Nenhuma release publicada encontrada em $($cfg.repo) (HTTP 404)."
    }
    if ($httpCode -eq 403 -or $httpCode -eq 429) {
      throw 'Limite temporário da API pública do GitHub atingido (sem token). Tente novamente em alguns minutos.'
    }
    if ($httpCode -lt 200 -or $httpCode -ge 300) {
      throw "Servidor de atualizações respondeu HTTP $httpCode."
    }

    $rawJson = Get-Content $arqBody -Raw -Encoding UTF8
    $relObj = $rawJson | ConvertFrom-Json

    $novoEtag = ''
    if (Test-Path $arqHdr) {
      foreach ($hl in (Get-Content $arqHdr -Encoding ASCII)) {
        if ($hl -match '^(?i)etag:\s*(.+)$') {
          $novoEtag = $Matches[1].Trim()
        }
      }
    }
    Gravar-CacheRelease $endpoint $novoEtag $relObj
    return Montar-InfoRelease $relObj $verAtual $cfg.canal $cfg.repo
  } finally {
    Remove-Item -Recurse -Force $tmpHttp -ErrorAction SilentlyContinue
  }
}

function Copiar-ArvoreCodigo([string]$origem, [string]$destino) {
  if (Test-Path $destino) {
    Remove-Item -Recurse -Force $destino
  }
  New-Item -ItemType Directory -Force $destino | Out-Null
  foreach ($item in (Get-ChildItem -LiteralPath $origem -Force)) {
    if ($item.PSIsContainer) {
      if ($item.Name -eq '__pycache__' -or $item.Name -like '*.egg-info') { continue }
      Copiar-ArvoreCodigo $item.FullName (Join-Path $destino $item.Name)
    } else {
      if ($item.Name -like '*.pyc' -or $item.Name -like '*.pyo' -or $item.Name -like '*.local.*') { continue }
      Copy-Item -LiteralPath $item.FullName -Destination (Join-Path $destino $item.Name) -Force
    }
  }
}

function Copiar-ArquivoComExeSeguro([string]$origem, [string]$destino, [string]$pastaReservaExe) {
  if ($destino.EndsWith('.exe', [StringComparison]::OrdinalIgnoreCase) -and (Test-Path $destino)) {
    try {
      Copy-Item -LiteralPath $origem -Destination $destino -Force
      return
    } catch {
      New-Item -ItemType Directory -Force $pastaReservaExe | Out-Null
      $emUso = Join-Path $pastaReservaExe ((Split-Path -Leaf $destino) + '.em_uso_' + [DateTimeOffset]::UtcNow.ToUnixTimeSeconds())
      Move-Item -LiteralPath $destino -Destination $emUso -Force
      Copy-Item -LiteralPath $origem -Destination $destino -Force
      return
    }
  }
  Copy-Item -LiteralPath $origem -Destination $destino -Force
}

function Salvar-Backup([string]$pastaRaiz, [string]$versaoAtual) {
  New-Item -ItemType Directory -Force $PASTA_ANTIGAS | Out-Null
  $nomeSlot = "v$versaoAtual"
  $dest = Join-Path $PASTA_ANTIGAS $nomeSlot
  if (Test-Path $dest) {
    Remove-Item -Recurse -Force $dest
  }
  New-Item -ItemType Directory -Force $dest | Out-Null

  $arqsRaizSalvos = @()
  $pastasCodigoSalvas = @()

  foreach ($item in (Get-ChildItem -LiteralPath $pastaRaiz -Force)) {
    if ($item.PSIsContainer) {
      if (Eh-PastaProtegida $item.Name) { continue }
      $subDest = Join-Path $dest $item.Name
      Copiar-ArvoreCodigo $item.FullName $subDest
      # Remove arquivos de estado local dentro das pastas de codigo do backup
      Remove-Item (Join-Path $subDest 'modo.txt') -Force -ErrorAction SilentlyContinue
      if ($item.Name -eq 'cortelive') {
        Remove-Item (Join-Path $subDest 'postar\config.toml') -Force -ErrorAction SilentlyContinue
      }
      $pastasCodigoSalvas += $item.Name
    } else {
      if (Eh-ArquivoUsuarioProtegido $item.Name) { continue }
      Copy-Item -LiteralPath $item.FullName -Destination (Join-Path $dest $item.Name) -Force
      $arqsRaizSalvos += $item.Name
    }
  }

  $manifesto = [ordered]@{
    versao        = $versaoAtual
    criado_em     = (Get-Date -Format o)
    arqs_raiz     = $arqsRaizSalvos
    pastas_codigo = $pastasCodigoSalvas
  }
  [IO.File]::WriteAllText((Join-Path $dest 'manifesto_backup.json'), ($manifesto | ConvertTo-Json -Depth 5), $utf8SemBom)
  [IO.File]::WriteAllText($ARQ_ULTIMA, $nomeSlot, $utf8SemBom)

  # Mantem no maximo as 3 ultimas versoes em versoes_antigas\ para nao acumular lixo eterno
  $todosBackups = Get-ChildItem -LiteralPath $PASTA_ANTIGAS -Directory -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -ne $nomeSlot -and $_.Name -notlike '_*' } |
    Sort-Object LastWriteTime -Descending
  if ($todosBackups.Count -gt 2) {
    foreach ($antigo in ($todosBackups | Select-Object -Skip 2)) {
      Remove-Item -Recurse -Force $antigo.FullName -ErrorAction SilentlyContinue
    }
  }

  return $dest
}

function Capturar-ArquivosInternosUsuario([string]$pastaRaiz) {
  $guardados = @{}
  $relativos = @(
    'instalador\modo.txt',
    'instalador\cache_release.local.json',
    'cortelive\postar\config.toml',
    'painel\config.local.json'
  )
  foreach ($rel in $relativos) {
    $full = Join-Path $pastaRaiz $rel
    if (Test-Path $full) {
      $guardados[$rel] = [IO.File]::ReadAllBytes($full)
    }
  }
  return $guardados
}

function Restaurar-ArquivosInternosUsuario([string]$pastaRaiz, [hashtable]$guardados) {
  foreach ($kv in $guardados.GetEnumerator()) {
    $alvo = Join-Path $pastaRaiz $kv.Key
    $pai = Split-Path -Parent $alvo
    if (-not (Test-Path $pai)) {
      New-Item -ItemType Directory -Force $pai | Out-Null
    }
    [IO.File]::WriteAllBytes($alvo, $kv.Value)
  }
}

function Aplicar-ArvoreCodigo([string]$origemPacote, [string]$pastaRaiz, [string]$pastaReservaExe, [string]$repoAntes) {
  $internos = Capturar-ArquivosInternosUsuario $pastaRaiz

  foreach ($item in (Get-ChildItem -LiteralPath $origemPacote -Force)) {
    if ($item.Name -eq 'manifesto_backup.json' -or $item.Name -like '*.em_uso_*') { continue }
    if ($item.PSIsContainer) {
      if (Eh-PastaProtegida $item.Name) { continue }
      Copiar-ArvoreCodigo $item.FullName (Join-Path $pastaRaiz $item.Name)
    } else {
      if (Eh-ArquivoUsuarioProtegido $item.Name) { continue }
      Copiar-ArquivoComExeSeguro $item.FullName (Join-Path $pastaRaiz $item.Name) $pastaReservaExe
    }
  }

  # Se versoes.ps1 novo veio com placeholder <dono>/cortelive mas o local ja tinha dono configurado, preserva o dono
  $versoesDestino = Join-Path $pastaRaiz 'instalador\versoes.ps1'
  if ($repoAntes -and ($repoAntes -notmatch '<dono>') -and ($repoAntes -notmatch '^DONO/') -and (Test-Path $versoesDestino)) {
    $txtVer = Get-Content $versoesDestino -Raw -Encoding UTF8
    if ($txtVer -match '<dono>/cortelive') {
      $txtVer = $txtVer -replace '<dono>/cortelive', $repoAntes
      [IO.File]::WriteAllText($versoesDestino, $txtVer, $utf8SemBom)
    }
  }

  Restaurar-ArquivosInternosUsuario $pastaRaiz $internos
}

function Restaurar-Backup([string]$pastaRaiz, [string]$pastaBackup) {
  if (-not (Test-Path $pastaBackup)) {
    throw "Pasta de backup não encontrada: $pastaBackup"
  }
  $manifestoArq = Join-Path $pastaBackup 'manifesto_backup.json'
  $manifesto = $null
  if (Test-Path $manifestoArq) {
    $manifesto = Get-Content $manifestoArq -Raw -Encoding UTF8 | ConvertFrom-Json
  }

  $internos = Capturar-ArquivosInternosUsuario $pastaRaiz

  if ($manifesto) {
    # Remove arquivos/pastas de codigo que foram introduzidos apenas pela versao nova falha
    $arqsOriginais = @($manifesto.arqs_raiz)
    $pastasOriginais = @($manifesto.pastas_codigo)
    foreach ($atual in (Get-ChildItem -LiteralPath $pastaRaiz -Force)) {
      if ($atual.PSIsContainer) {
        if (Eh-PastaProtegida $atual.Name) { continue }
        if ($pastasOriginais -notcontains $atual.Name) {
          Remove-Item -Recurse -Force $atual.FullName -ErrorAction SilentlyContinue
        }
      } else {
        if (Eh-ArquivoUsuarioProtegido $atual.Name) { continue }
        if ($arqsOriginais -notcontains $atual.Name) {
          Remove-Item -Force $atual.FullName -ErrorAction SilentlyContinue
        }
      }
    }
  }

  Aplicar-ArvoreCodigo $pastaBackup $pastaRaiz $PASTA_EM_USO ''
  Restaurar-ArquivosInternosUsuario $pastaRaiz $internos
}

function Achar-PythonParaTeste([string]$pastaRaiz) {
  $vpy = Join-Path $pastaRaiz '.venv\Scripts\python.exe'
  if (Test-Path $vpy) { return @{ exe = $vpy; completo = $true } }

  $ppy = Join-Path $pastaRaiz 'python\python.exe'
  if (Test-Path $ppy) { return @{ exe = $ppy; completo = $false } }

  if ($env:CORTELIVE_PYTHON_TESTE -and (Test-Path $env:CORTELIVE_PYTHON_TESTE)) {
    return @{ exe = $env:CORTELIVE_PYTHON_TESTE; completo = $false }
  }

  $cmds = Get-Command python.exe -All -ErrorAction SilentlyContinue
  foreach ($c in $cmds) {
    if ($c.Source -and ($c.Source -notmatch 'WindowsApps')) {
      return @{ exe = $c.Source; completo = $false }
    }
  }
  return $null
}

function Testar-Inicializacao([string]$pastaRaiz, [string]$versaoEsperada = '') {
  $verDisco = Ler-Versao $pastaRaiz
  if (-not $verDisco -or $verDisco -eq '0.0.0' -or ($verDisco -notmatch '^\d+\.\d+\.\d+')) {
    return @{ ok = $false; motivo = "Arquivo VERSION ausente ou inválido ('$verDisco')." }
  }
  if ($versaoEsperada -and ($verDisco -ne $versaoEsperada)) {
    return @{ ok = $false; motivo = "VERSION em disco ('$verDisco') não corresponde à versão esperada ('$versaoEsperada')." }
  }

  foreach ($obrig in @('cortelive\__init__.py', 'cortelive\cli.py', 'instalador\instalar.ps1', 'instalador\atualizar.ps1', 'instalador\versoes.ps1')) {
    if (-not (Test-Path (Join-Path $pastaRaiz $obrig))) {
      return @{ ok = $false; motivo = "Arquivo essencial ausente: $obrig" }
    }
  }

  $exePainel = Join-Path $pastaRaiz 'CorteLive.exe'
  if (Test-Path $exePainel) {
    $outExe = & $exePainel --self-test --raiz $pastaRaiz 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0) {
      return @{ ok = $false; motivo = "CorteLive.exe falhou ao iniciar: $($outExe.Trim())" }
    }
  }

  $pyInfo = Achar-PythonParaTeste $pastaRaiz
  if ($pyInfo) {
    $oldPyPath = $env:PYTHONPATH
    $oldUtf8 = $env:PYTHONUTF8
    try {
      $env:PYTHONPATH = $pastaRaiz
      $env:PYTHONUTF8 = '1'

      $scriptSintaxe = @"
import pathlib, py_compile, sys
raiz = pathlib.Path(r'''$pastaRaiz''')
for sub in ('cortelive', 'painel'):
    p = raiz / sub
    if p.is_dir():
        for arq in p.rglob('*.py'):
            py_compile.compile(str(arq), doraise=True)
import cortelive
assert cortelive.__version__ == '$verDisco', f'__version__={cortelive.__version__} != $verDisco'
"@
      $outComp = & $pyInfo.exe -c $scriptSintaxe 2>&1 | Out-String
      if ($LASTEXITCODE -ne 0) {
        return @{ ok = $false; motivo = "Falha ao carregar o código Python da versão ${verDisco}: $($outComp.Trim())" }
      }

      if ($pyInfo.completo) {
        $outCli = & $pyInfo.exe -m cortelive --versao 2>&1 | Out-String
        if ($LASTEXITCODE -ne 0 -or ($outCli.Trim() -ne $verDisco)) {
          return @{ ok = $false; motivo = "Falha ao iniciar 'python -m cortelive --versao': $($outCli.Trim())" }
        }
        $temMotorPainel = Test-Path (Join-Path $pastaRaiz 'painel\motor_painel.py')
        $impExtra = if ($temMotorPainel) { '; import painel.motor_painel' } else { '' }
        $outImp = & $pyInfo.exe -c "import cortelive.cli, cortelive.sessao, cortelive.multi$impExtra" 2>&1 | Out-String
        if ($LASTEXITCODE -ne 0) {
          return @{ ok = $false; motivo = "Módulos principais falharam ao iniciar: $($outImp.Trim())" }
        }
      }
    } finally {
      $env:PYTHONPATH = $oldPyPath
      $env:PYTHONUTF8 = $oldUtf8
    }
  }

  return @{ ok = $true; motivo = '' }
}

function Obter-PastaBackupAnterior([string]$versaoAtualDisco = '') {
  if (Test-Path $ARQ_ULTIMA) {
    $nome = (Get-Content $ARQ_ULTIMA -Raw -Encoding UTF8).Trim()
    if ($nome) {
      $cand = Join-Path $PASTA_ANTIGAS $nome
      if ((Test-Path $cand) -and ((Ler-Versao $cand) -ne $versaoAtualDisco)) {
        return $cand
      }
    }
  }
  if (Test-Path $PASTA_ANTIGAS) {
    $maisRecente = Get-ChildItem -LiteralPath $PASTA_ANTIGAS -Directory -ErrorAction SilentlyContinue |
      Where-Object { $_.Name -notlike '_*' -and (Ler-Versao $_.FullName) -ne $versaoAtualDisco } |
      Sort-Object LastWriteTime -Descending |
      Select-Object -First 1
    if ($maisRecente) { return $maisRecente.FullName }
  }
  return ''
}

# --- Selecao e validacao de modo --------------------------------------------------------------
$qtdModos = (@($Checar, $Aplicar, $Voltar) | Where-Object { $_ }).Count
$verInicial = Ler-Versao $RAIZ

if (Test-Path -LiteralPath (Join-Path $RAIZ '.git')) {
  Emitir-Evento -Etapa 'erro' -Pct 100 -Msg 'Atualizador bloqueado neste repositório de desenvolvimento.' -VersaoAtual $verInicial -VersaoNova $null -Erro 'Use uma instalação do CorteLive fora do repositório.'
  exit 1
}

if ($qtdModos -gt 1) {
  Emitir-Evento -Etapa 'erro' -Pct 100 -Msg 'Modos incompatíveis.' -VersaoAtual $verInicial -VersaoNova $null -Erro 'Informe apenas um modo por vez: -Checar, -Aplicar ou -Voltar.'
  exit 1
}
if ($qtdModos -eq 0) {
  $Aplicar = $true
}

# --- MODO: -Checar ----------------------------------------------------------------------------
if ($Checar) {
  Emitir-Evento -Etapa 'checando' -Pct 15 -Msg 'Consultando atualizações no GitHub...' -VersaoAtual $verInicial -VersaoNova $null -Erro $null
  try {
    $info = Consultar-Release
    if ($info.disponivel) {
      $rotuloVer = if ($info.prerelease) { "beta $($info.versao_nova)" } else { "$($info.versao_nova)" }
      Emitir-Evento -Etapa 'disponivel' -Pct 100 -Msg "Nova versão $rotuloVer disponível." -VersaoAtual $info.versao_atual -VersaoNova $info.versao_nova -Erro $null
    } else {
      $rotuloCanal = if ($info.canal -eq 'beta') { ' (canal beta)' } else { '' }
      Emitir-Evento -Etapa 'em_dia' -Pct 100 -Msg "Você já está na versão mais recente ($($info.versao_atual))$rotuloCanal." -VersaoAtual $info.versao_atual -VersaoNova $info.versao_nova -Erro $null
    }
    exit 0
  } catch {
    Emitir-Evento -Etapa 'erro' -Pct 100 -Msg 'Não foi possível verificar atualizações.' -VersaoAtual $verInicial -VersaoNova $null -Erro $_.Exception.Message
    exit 1
  }
}

# --- MODO: -Voltar ----------------------------------------------------------------------------
if ($Voltar) {
  $pastaBak = Obter-PastaBackupAnterior $verInicial
  if (-not $pastaBak) {
    Emitir-Evento -Etapa 'erro' -Pct 100 -Msg 'Nenhuma versão anterior disponível para voltar.' -VersaoAtual $verInicial -VersaoNova $null -Erro 'Não há backup salvo em versoes_antigas\.'
    exit 1
  }

  $verAlvo = Ler-Versao $pastaBak
  Emitir-Evento -Etapa 'rollback' -Pct 20 -Msg "Voltando para a versão anterior ($verAlvo)..." -VersaoAtual $verInicial -VersaoNova $verAlvo -Erro $null

  try {
    $reqAntes = Hash-Sha256 (Join-Path $RAIZ 'requirements.txt')
    $verPsAntes = Hash-Sha256 (Join-Path $RAIZ 'instalador\versoes.ps1')

    Restaurar-Backup $RAIZ $pastaBak

    $reqDepois = Hash-Sha256 (Join-Path $RAIZ 'requirements.txt')
    $verPsDepois = Hash-Sha256 (Join-Path $RAIZ 'instalador\versoes.ps1')
    $mudouDeps = ($reqAntes -ne $reqDepois) -or ($verPsAntes -ne $verPsDepois)
    if ($mudouDeps -and (Test-Path (Join-Path $RAIZ '.venv\Scripts\python.exe'))) {
      Emitir-Evento -Etapa 'dependencias' -Pct 65 -Msg 'Sincronizando bibliotecas da versão anterior...' -VersaoAtual $verAlvo -VersaoNova $verAlvo -Erro $null
      & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $RAIZ 'instalador\instalar.ps1') -SemPerguntas >> $LOG_UPDATE 2>&1
      if ($LASTEXITCODE -ne 0) {
        throw "instalar.ps1 retornou código $LASTEXITCODE durante o rollback."
      }
    }

    Emitir-Evento -Etapa 'validando' -Pct 85 -Msg "Validando inicialização da versão $verAlvo..." -VersaoAtual $verAlvo -VersaoNova $verAlvo -Erro $null
    $teste = Testar-Inicializacao $RAIZ $verAlvo
    if (-not $teste.ok) {
      throw $teste.motivo
    }

    $verFinal = Ler-Versao $RAIZ
    Emitir-Evento -Etapa 'concluido' -Pct 100 -Msg "Voltou com sucesso para a versão $verFinal." -VersaoAtual $verFinal -VersaoNova $verFinal -Erro $null
    exit 0
  } catch {
    Emitir-Evento -Etapa 'erro' -Pct 100 -Msg 'Falha ao restaurar a versão anterior.' -VersaoAtual (Ler-Versao $RAIZ) -VersaoNova $verAlvo -Erro $_.Exception.Message
    exit 1
  }
}

# --- MODO: -Aplicar ---------------------------------------------------------------------------
$verAntes = $verInicial
$verNovaAlvo = $null
$cfgAtual = Obter-ConfigUpdate

# Fase 1: checagem, download e validacao SHA-256 (antes de tocar em qualquer arquivo da instalacao)
try {
  Emitir-Evento -Etapa 'checando' -Pct 5 -Msg 'Verificando última release no GitHub...' -VersaoAtual $verAntes -VersaoNova $null -Erro $null
  $info = Consultar-Release -IgnorarTtl
  $verNovaAlvo = $info.versao_nova

  if (-not $info.disponivel) {
    Emitir-Evento -Etapa 'em_dia' -Pct 100 -Msg "Você já está na versão mais recente ($verAntes)." -VersaoAtual $verAntes -VersaoNova $verNovaAlvo -Erro $null
    exit 0
  }

  if (-not $info.zip_url) {
    throw "A release $($info.tag) não possui o arquivo cortelive_amigos.zip anexado."
  }
  if (-not $info.sha_url) {
    throw "A release $($info.tag) não possui o arquivo .sha256 publicado; atualização recusada por segurança."
  }

  if (Test-Path $TMP_UPDATE) { Remove-Item -Recurse -Force $TMP_UPDATE }
  New-Item -ItemType Directory -Force $TMP_UPDATE | Out-Null
  # Solta o que sobrou de trocas anteriores (o que ainda estiver aberto fica para a proxima).
  if (Test-Path $PASTA_EM_USO) {
    Get-ChildItem -LiteralPath $PASTA_EM_USO -Force -ErrorAction SilentlyContinue |
      ForEach-Object { Remove-Item -LiteralPath $_.FullName -Force -ErrorAction SilentlyContinue }
  }

  $zipArq = Join-Path $TMP_UPDATE 'cortelive_amigos.zip'
  $shaArq = Join-Path $TMP_UPDATE 'cortelive_amigos.zip.sha256'
  $maxTimeDl = [Math]::Max(120, $cfgAtual.timeout_s * 8)

  Emitir-Evento -Etapa 'baixando' -Pct 20 -Msg "Baixando pacote da versão $verNovaAlvo..." -VersaoAtual $verAntes -VersaoNova $verNovaAlvo -Erro $null
  & curl.exe -sS -L --fail --retry 2 --connect-timeout 10 --max-time "$maxTimeDl" -o $zipArq $info.zip_url 2>> $LOG_UPDATE
  if ($LASTEXITCODE -ne 0 -or -not (Test-Path $zipArq)) {
    throw "Falha ao baixar o pacote de atualização ($($info.zip_url))."
  }

  & curl.exe -sS -L --fail --retry 2 --connect-timeout 10 --max-time "$($cfgAtual.timeout_s)" -o $shaArq $info.sha_url 2>> $LOG_UPDATE
  if ($LASTEXITCODE -ne 0 -or -not (Test-Path $shaArq)) {
    throw "Falha ao baixar o arquivo de checksum SHA-256 ($($info.sha_url))."
  }

  Emitir-Evento -Etapa 'verificando' -Pct 45 -Msg 'Conferindo checksum SHA-256 do pacote...' -VersaoAtual $verAntes -VersaoNova $verNovaAlvo -Erro $null
  $textoSha = (Get-Content $shaArq -Raw -Encoding ASCII).Trim()
  $hashEsperado = (($textoSha -split '\s+')[0]).Trim().ToUpperInvariant()
  if ($hashEsperado -notmatch '^[0-9A-F]{64}$') {
    throw "Arquivo .sha256 publicado tem formato inválido ('$textoSha')."
  }
  $hashObtido = Hash-Sha256 $zipArq
  if ($hashObtido -ne $hashEsperado) {
    throw "Checksum SHA-256 inválido! Esperado: $hashEsperado | Obtido: $hashObtido. Atualização recusada."
  }

  $pastaExtraida = Join-Path $TMP_UPDATE 'extraido'
  New-Item -ItemType Directory -Force $pastaExtraida | Out-Null
  Expand-Archive -Path $zipArq -DestinationPath $pastaExtraida -Force

  $itensTopo = @(Get-ChildItem -LiteralPath $pastaExtraida -Force)
  $origemCodigo = $pastaExtraida
  if ($itensTopo.Count -eq 1 -and $itensTopo[0].PSIsContainer) {
    $origemCodigo = $itensTopo[0].FullName
  }

  if (-not (Test-Path (Join-Path $origemCodigo 'cortelive\__init__.py')) -or -not (Test-Path (Join-Path $origemCodigo 'instalador\instalar.ps1'))) {
    throw 'O arquivo zip baixado não contém a estrutura válida do CorteLive.'
  }

  Emitir-Evento -Etapa 'backup' -Pct 60 -Msg "Salvando backup da versão $verAntes em versoes_antigas\..." -VersaoAtual $verAntes -VersaoNova $verNovaAlvo -Erro $null
  $hashReqAntes   = Hash-Sha256 (Join-Path $RAIZ 'requirements.txt')
  $hashVerPsAntes = Hash-Sha256 (Join-Path $RAIZ 'instalador\versoes.ps1')
  $ultimaAntes    = if (Test-Path $ARQ_ULTIMA) { (Get-Content $ARQ_ULTIMA -Raw -Encoding UTF8).Trim() } else { '' }
  $pastaBackupCriada = Salvar-Backup $RAIZ $verAntes
} catch {
  Remove-Item -Recurse -Force $TMP_UPDATE -ErrorAction SilentlyContinue
  Emitir-Evento -Etapa 'erro' -Pct 100 -Msg 'Atualização cancelada antes de alterar seus arquivos.' -VersaoAtual $verAntes -VersaoNova $verNovaAlvo -Erro $_.Exception.Message
  exit 1
}

# Fase 2: troca do codigo, instalador condicional e autoteste (qualquer falha dispara rollback automatico!)
try {
  Emitir-Evento -Etapa 'aplicando' -Pct 72 -Msg "Aplicando código da versão $verNovaAlvo (mantendo modelos, .venv, FFmpeg e cortes)..." -VersaoAtual $verAntes -VersaoNova $verNovaAlvo -Erro $null
  Aplicar-ArvoreCodigo $origemCodigo $RAIZ $PASTA_EM_USO $cfgAtual.repo

  if (-not (Test-Path (Join-Path $origemCodigo 'VERSION'))) {
    [IO.File]::WriteAllText((Join-Path $RAIZ 'VERSION'), "$verNovaAlvo`n", $utf8SemBom)
  }

  $hashReqDepois   = Hash-Sha256 (Join-Path $RAIZ 'requirements.txt')
  $hashVerPsDepois = Hash-Sha256 (Join-Path $RAIZ 'instalador\versoes.ps1')
  $mudouDependencias = ($hashReqAntes -ne $hashReqDepois) -or ($hashVerPsAntes -ne $hashVerPsDepois)

  if ($mudouDependencias) {
    Emitir-Evento -Etapa 'dependencias' -Pct 82 -Msg 'requirements.txt ou versoes.ps1 mudou; rodando instalar.ps1 -SemPerguntas...' -VersaoAtual $verAntes -VersaoNova $verNovaAlvo -Erro $null
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $RAIZ 'instalador\instalar.ps1') -SemPerguntas >> $LOG_UPDATE 2>&1
    if ($LASTEXITCODE -ne 0) {
      throw "instalar.ps1 -SemPerguntas falhou com código $LASTEXITCODE (veja atualizacao.log e instalacao_log.txt)."
    }
  }

  Emitir-Evento -Etapa 'validando' -Pct 92 -Msg "Testando inicialização da versão $verNovaAlvo..." -VersaoAtual $verNovaAlvo -VersaoNova $verNovaAlvo -Erro $null
  $testeNovo = Testar-Inicializacao $RAIZ $verNovaAlvo
  if (-not $testeNovo.ok) {
    throw $testeNovo.motivo
  }

  Remove-Item -Recurse -Force $TMP_UPDATE -ErrorAction SilentlyContinue
  $verFinal = Ler-Versao $RAIZ
  Emitir-Evento -Etapa 'concluido' -Pct 100 -Msg "Atualizado com sucesso de $verAntes para $verFinal." -VersaoAtual $verFinal -VersaoNova $verFinal -Erro $null
  exit 0
} catch {
  $erroNovaVersao = $_.Exception.Message
  Registrar-Log "FALHA AO APLICAR/INICIAR v${verNovaAlvo}: $erroNovaVersao -> iniciando rollback automatico"
  Emitir-Evento -Etapa 'rollback' -Pct 96 -Msg "A versão $verNovaAlvo falhou ao iniciar; voltando automaticamente para $verAntes..." -VersaoAtual $verAntes -VersaoNova $verNovaAlvo -Erro $erroNovaVersao

  try {
    Restaurar-Backup $RAIZ $pastaBackupCriada
    if ($ultimaAntes) {
      [IO.File]::WriteAllText($ARQ_ULTIMA, $ultimaAntes, $utf8SemBom)
    } else {
      Remove-Item $ARQ_ULTIMA -Force -ErrorAction SilentlyContinue
    }
    $testeRest = Testar-Inicializacao $RAIZ $verAntes
    Remove-Item -Recurse -Force $TMP_UPDATE -ErrorAction SilentlyContinue
    $verRestaurada = Ler-Versao $RAIZ
    if (-not $testeRest.ok) {
      throw "A validação pós-rollback reportou: $($testeRest.motivo)"
    }
    Emitir-Evento -Etapa 'erro' -Pct 100 -Msg "Atualização desfeita automaticamente: versão $verRestaurada restaurada." -VersaoAtual $verRestaurada -VersaoNova $verNovaAlvo -Erro "A versão $verNovaAlvo falhou ($erroNovaVersao). Rollback automático para $verRestaurada concluído."
    exit 2
  } catch {
    Remove-Item -Recurse -Force $TMP_UPDATE -ErrorAction SilentlyContinue
    Emitir-Evento -Etapa 'erro' -Pct 100 -Msg 'Falha crítica na atualização e no rollback automático.' -VersaoAtual (Ler-Versao $RAIZ) -VersaoNova $verNovaAlvo -Erro "Falha na versão $verNovaAlvo ($erroNovaVersao) e falha ao restaurar backup ($($_.Exception.Message))."
    exit 3
  }
}
