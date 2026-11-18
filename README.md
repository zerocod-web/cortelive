# cortelive

Acompanha uma live (Twitch, YouTube, Kick) e solta **cortes verticais legendados** (1080x1920) enquanto ela acontece.

## Painel para amigos (1 arquivo só, sem terminal e sem administrador)
Na aba **Releases** do GitHub, basta baixar **`CorteLive.exe`** (~400 KB), colocar numa pasta própria (ex.: `C:\CorteLive`) e dar dois cliques:
1. **Primeira vez**: clique no botão **INSTALAR** — chama o `instalador\instalar.ps1` existente com progresso por etapa (1/6 a 6/6) e em `%`, sem pedir administrador.
2. **No dia a dia**: cole o link da live, escolha o modelo e a legenda nas miniaturas 9:16, clique em **CORTAR**, acompanhe o status ao vivo e abra os vídeos ou a pasta direto pela lista.
3. **Atualização automática com rollback**: ao abrir (e a cada 30 min), chama `instalador\atualizar.ps1 -Checar` e oferece o botão **Atualizar** (`-Aplicar`), trocando só o código e mantendo `.venv`, `modelos`, `ffmpeg`, `cortes` e configurações.
4. **Postar e Contas**: aba dedicada conectada às CLIs `cortelive.postar`, `cortelive.autopost` (fila com título/legenda editáveis e revisão) e `cortelive.autopost_navegador` (reserva desligada por padrão), mais os interruptores **Postar sozinho** e **Revisar antes**.

### Escolha de tecnologia do painel (por que funciona em qualquer Windows 10/11 limpo)
- **Executável nativo `.NET Framework 4.x` (`painel/CorteLive.cs` compilado com `csc.exe` do próprio Windows)**: todo Windows 10 e Windows 11 já traz o runtime `.NET Framework 4.8` de fábrica (diferente de Python, Node.js, Electron, Tauri/WebView2 SDK ou .NET 8, que exigiriam downloads extras antes de abrir a primeira tela ou gerariam executáveis de 70–150 MB).
- **Pacote auto-contido (`app.zip` embutido como recurso Win32/.NET)**: na primeira abertura em pasta vazia, `CorteLive.exe` extrai instantaneamente `painel\`, `instalador\` e `cortelive\` sem depender de nada instalado, mantendo manifesto `asInvoker` (nunca pede UAC/administrador).
- **Servidor HTTP em `127.0.0.1` (`TcpListener` loopback, sem alerta de Firewall) + janela dedicada Microsoft Edge `--app=` (com fallback para navegador padrão e ícone na bandeja)**: entrega interface moderna, acessível e responsiva em ~400 KB totais.

Para publicar novas versões no GitHub Releases, veja [COMO_LANCAR_VERSAO.md](COMO_LANCAR_VERSAO.md).

## Instalar pelo terminal (alternativa CMD)
Dê dois cliques em **instalar.cmd** (ou **INSTALAR_E_RODAR.bat**). Ele baixa Python 3.12 portátil, FFmpeg, cria `.venv` e baixa os modelos Whisper localmente.
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
`cortes\2026-11-17 22h35 - <canal>\` mostra só o que é para postar:
- `2026-11-17 22h36 - <canal> - <título do momento>.mp4` (com legenda; H.264 1080x1920, 30 fps,
  ~6 Mbps, AAC 128k: uns 30 MB para 40 s, leve para baixar no celular)
- `sem legenda\` com o mesmo corte sem legenda (para pôr a sua no CapCut, por exemplo)
- `_interno\` (pasta oculta): `.json` de cada corte (início/fim na live, sinais, pontuação, título, motivo,
  latência), `.ass` (a legenda, editável), `sessao.json`, `log.txt` e temporários.

Pastas do formato antigo (`2026-11-17_1628_canal` com `corte_NNN_HHMMSS.mp4`) continuam funcionando.
Para passar para o formato novo: `python -m cortelive.pastas migrar cortes` (mostra o plano) e depois
com `--aplicar`.

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