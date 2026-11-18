# Contrato para várias lives (multi.py)

Dono: `sessao.py`, `servicos.py`, `fala.py`, `carga.py`, `cli.py` = ajudante da transcrição/render.
`multi.py` (orquestração, painel, `--lista`) = outro ajudante. Este arquivo é a interface entre os dois.

## 1. Sessão de uma live — `cortelive.sessao.Sessao`

```python
from cortelive.sessao import Sessao

s = Sessao(
    url=None,               # link da live (Twitch/YouTube/Kick)  — ou —
    arquivo=None,           # vídeo lido a 1x (teste)
    canal=None,             # nome; padrão: tirado do link/arquivo. Define pasta, chat e cam memorizada
    cfg=None,               # dict que sobrepõe o config.toml (ex.: {"modelo": "jogo-fundo-desfocado"})
    cam=None,               # (x, y, w, h) em px; None = memória do canal ou detecção automática
    minutos=None,           # parar sozinha depois de N min
    ao_status=None,         # callback(dict) chamado a cada mudança de estado/corte (ver status())
    terminal=False,         # True = imprime o log na tela (modo de uma live só). Em multi, deixe False
)
s.iniciar()                 # sobe a thread da sessão e volta na hora
s.parar()                   # pede parada limpa (termina cortes em andamento, escreve sessao.json)
s.aguardar(timeout=None)    # espera terminar; devolve o resumo (dict do sessao.json)
s.status()                  # dict, barato, pode chamar a cada segundo:
#   {"canal", "pasta", "estado", "cortes", "falhas", "live_s", "atraso_transcricao_s",
#    "ultimo_corte", "motivo_fim"}
#   estado: "iniciando" | "esperando live" | "ao vivo" | "reconectando" | "encerrada" | "erro"
```

- Cada sessão tem pasta própria `cortes\<data>_<canal>\`, `log.txt` próprio, buffer próprio,
  reconexão/espera de live offline/watchdogs próprios. Uma sessão caindo ou dando erro não afeta as outras
  (exceção dentro da sessão → estado "erro" + motivo_fim; nada sobe para quem chamou).
- `terminal=False`: nada é impresso; o painel do multi lê `status()`/`ao_status`.
- Mesmo canal duas vezes: o multi deve evitar (pasta e cam são por canal).

## 2. Transcrição compartilhada — `cortelive.servicos.transcricao()`

```python
from cortelive import servicos
tr = servicos.transcricao()          # singleton: UM modelo na GPU para todas as sessões
palavras = tr.enviar(sessao_id, audio, t0, timeout=None)
#   audio: np.float32 mono 16 kHz; t0: tempo (s) da live no início do áudio
#   -> [{"w","s","e","p"}] no tempo da live. Bloqueia até ficar pronto. Thread-safe.
#   Fila justa: uma requisição por sessão por vez, em rodízio (live com muita fala não atrasa as outras).
tr.status()   # {"modelo", "fila", "fator_tempo_real", "sessoes": {id: atraso_medio_s}, "trocas": [...]}
servicos.desligar()                  # no fim do programa
```
- A degradação automática (turbo → small → blocos maiores, e volta) fica DENTRO do serviço e olha a carga
  total (fila + tempo gasto de todas as sessões). As sessões não fazem nada.
- As sessões já usam isso sozinhas; o multi só precisa do `status()` para o painel.

## 3. Fila global de render — `cortelive.servicos.render()`

```python
fr = servicos.render()               # singleton; limite de encodes simultâneos = config "encodes_simultaneos" (padrão 2)
fr.status()                          # {"rodando": n, "esperando": n, "limite": n}
```
- As sessões já mandam seus cortes por aqui; o multi não precisa chamar nada além do status.
- NVENC das GeForce aceita poucas sessões ao mesmo tempo; o render de cada corte usa 1 encode.

## 4. Configuração global

`servicos.configurar(cfg_dict)` antes da primeira sessão, se quiser mudar `encodes_simultaneos` ou o modelo.
Sem chamar, vale o `config.toml`.
