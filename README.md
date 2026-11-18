# cortelive

Acompanha uma live (Twitch, YouTube, Kick) e solta **cortes verticais legendados** (1080x1920) enquanto ela acontece.

## Instalar (uma vez)
Dê dois cliques em **instalar.cmd** (ou rode no CMD). Ele instala Python e FFmpeg pelo winget se faltarem, cria `.venv` e instala tudo.
Pronto: o comando `cortelive` fica em `.venv\Scripts\cortelive.exe`.

## Usar
```
cortelive https://twitch.tv/canal                 cortar a live agora
cortelive --arquivo video.mp4                     testar com um vídeo (lido em tempo real, como live)
cortelive marcar-cam https://twitch.tv/canal      dizer onde fica a cam do streamer (uma vez por canal)
```
Opções úteis:
- `--modelo cam-em-cima | jogo-fundo-desfocado | jogo-tela-cheia-crop` (layout do corte)
- `--legenda opus | karaoke | minimal` (estilo da legenda)
- `--duracao-min 30 --duracao-max 60` (tamanho do corte, em segundos)
- `--cam x,y,largura,altura` (cam em pixels, se não usou marcar-cam)
- `--minutos 30` (parar sozinho) · `--sem-chat` · `--detalhado`
- Forçar hardware: `--gpu amd`, `--encoder libx264`, `--fala whisper-cpp`

No começo ele mostra o que achou: placa, encoder (NVIDIA `h264_nvenc`, AMD `h264_amf`, Intel `h264_qsv`,
CPU por último) e o motor de fala.

## O que sai
`cortes\<data>_<canal>\` com, para cada corte:
- `corte_NNN_HHMMSS.mp4` (com legenda) e `..._sem_legenda.mp4`
- `.ass` (a legenda, editável)
- `.json` (início/fim na live, sinais, pontuação, título sugerido, motivo, latência)
- `sessao.json` e `logs\` com o log completo.

## Como ele escolhe o momento
Pico de volume contra o normal dos últimos 2 min + palavras quentes ("meu deus", "caralho", risada "kkkk"…)
+ pico do chat da Twitch (só leitura, anônimo). Se o **Ollama** estiver rodando no PC, ele dá a nota final e o
título; se não, vale a heurística. Corte começa ~22 s antes e termina ~14 s depois do momento, preso em pausa da fala.
Funciona 100% sem Ollama.

## Configuração
Tudo tem padrão. Para mudar, copie `cortelive\config.toml` (comentado em português) para a pasta onde você
roda o cortelive e altere só o que quiser.

## Placa AMD ou Intel
A fala roda na CPU (faster-whisper int8, modelo small): numa CPU de desktop atual, 30 s de fala levam ~4 s.
O vídeo sai pelo encoder da placa (h264_amf na AMD, h264_qsv na Intel), testado sozinho no começo; se falhar, usa a CPU.
Por que não a placa AMD na fala: o whisper.cpp oficial para Windows não tem versão Vulkan, e o Const-me/Whisper
(D3D11, roda em AMD) transcreve rápido mas não dá o tempo de cada palavra, que a legenda precisa.
Para testar o whisper.cpp mesmo assim: --fala whisper-cpp.

## Novo estilo de legenda / layout
Copie um arquivo de `cortelive\presets\legenda\` (ou `presets\modelo\`) com outro nome e mude os valores.
O nome do arquivo vira a opção (`--legenda meu-estilo`).

## Câmera do streamer
Não precisa fazer nada: no primeiro 1,5 min o programa olha 8 quadros, acha o rosto **parado no mesmo lugar** (a cam
do streamer; personagem do jogo se mexe) e a janelinha em volta dele (fundo do quarto parado, jogo se mexendo, ou a
moldura). Guarda por canal em `%LOCALAPPDATA%\cortelive\cams.json`. Se a confiança for baixa, ou se não achar,
usa o layout sem cam. Para acertar na mão: `cortelive marcar-cam <link> --rect x,y,largura,altura` (ou `--sem-cam`).

## Rodar sozinho por horas
- Live offline: espera ela começar (`esperar_live_min`). Live caiu: reconecta sozinho (2, 4, 8… até 60 s).
  Offline por `fim_apos_offline_min` (10 min): encerra a sessão e escreve o resumo.
- Watchdogs: captura sem vídeo novo em 40 s é reiniciada; transcrição travada em 90 s troca o motor;
  render acima de 5 min cancela só aquele corte. Cada corte roda num processo separado: erro em um não derruba o resto.
- Máquina lenta: a fila de cortes tem no máximo 3; passando disso, o momento mais fraco é descartado (com aviso no log).
- Buffer: só os últimos 5 min de live ficam em disco (`.buffer` da sessão), apagado no fim.
- Tudo vai para `cortes\<sessão>\log.txt`; no fim sai um RESUMO na tela e em `sessao.json`.