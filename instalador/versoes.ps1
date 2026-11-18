# Versoes fixadas e checksums oficiais (conferidos em 17/11/2026 na API do GitHub / Hugging Face).
# Para atualizar: troque URL + SHA256 juntos, sempre de fonte oficial.

# Repositorio publico do CorteLive no GitHub (configurado em um lugar so; obtido via `gh repo view --json owner`).
$REPO_GITHUB = 'zerocod-web/cortelive'

$PY = @{
  Nome   = 'Python 3.12.16 portatil (python-build-standalone 20261110, astral-sh)'
  Url    = 'https://github.com/astral-sh/python-build-standalone/releases/download/20261110/cpython-3.12.16+20261110-x86_64-pc-windows-msvc-install_only.tar.gz'
  Sha256 = 'e23fe5b16926de1d81661b1a97352e89fb167549c55a6825796ccd0f32ec7f1b'
  Arq    = 'python.tar.gz'
}

$FFMPEG = @{
  Nome   = 'FFmpeg essentials 2026-11-16 (gyan.dev, espelho oficial GyanD/codexffmpeg)'
  Url    = 'https://github.com/GyanD/codexffmpeg/releases/download/2026-11-16-git-d35e13b9e0/ffmpeg-2026-11-16-git-d35e13b9e0-essentials_build.zip'
  Sha256 = '21d3b7f6e8498fc4325e29a4291fa3fb23445655a98b2ea9ae1713eedee81fbb'
  Arq    = 'ffmpeg.zip'
}

# AMD/Intel: a fala roda no processador (faster-whisper small int8), sem whisper.cpp (decisao de 17/11/2026).
