# API local do CorteLive

O navegador acessa as rotas no host C# local. O host mantém a instalação, o progresso, o início e o fim da sessão, o seletor de arquivo, os estáticos e a atualização. O motor mantém o processo de corte e oferece as rotas complementares de cortes, miniatura, abertura, Drive e preferências de corte.

O host pode chamar `motor_painel.py --api-local --raiz <pasta>`, com cwd na pasta da instalação. Escreva `{"metodo":"GET","rota":"/api/cortes","corpo":{}}` em stdin UTF-8 e feche o stream. O motor responde com um envelope `{"status":200,"dados":{"ok":true,"cortes":[]}}`. Para a miniatura, o envelope traz `status`, `tipo:"image/jpeg"` e `base64`. O host repassa o status e o JSON ou decodifica a imagem. Para preencher `estado.cortes`, leia o campo `dados.cortes`. Evite registrar pedidos ou respostas brutas.

Para transporte HTTP entre processos, inicie `motor_painel.py --servir --porta 0 --raiz <pasta>` com um segredo aleatório em `CORTELIVE_PAINEL_TOKEN`. O motor escuta em `127.0.0.1` e escreve uma linha `{"porta":12345}` em stdout. Envie `X-CorteLive-Token` nas chamadas do host ao motor por loopback. O navegador usa somente a origem do host.

Use JSON UTF-8 em POST com corpo. Nas rotas complementares, erros retornam `{"ok":false,"erro":"mensagem"}` com status 400, 403, 404, 405, 409, 502 ou 504.

## Instalação e corte

Use polling de `GET /api/estado` durante corte e instalação. O snapshot pode ser consultado novamente depois de uma reconexão.

| Método e rota | Requisição e resposta |
| --- | --- |
| GET `/api/ping` | `{ok, versao}`. |
| GET `/api/estado` | `{ok, versao, pasta_raiz, instalado, modo_descricao, instalacao, sessao, cortes}`. |
| POST `/api/instalar` | `{}` → `{ok:true}`. Começa a instalação em segundo plano. |
| GET `/api/log-instalacao` | `{ok, log}`. |
| POST `/api/cortar` | `{fonte, modelo?, legenda?}` → `{ok:true}`. `fonte` é link HTTP(S) ou caminho de um vídeo local. Cada fonte usa o modelo e a legenda enviados, inclusive durante uma sessão ativa. Padrões `cam-em-cima` e `opus`. |
| POST `/api/parar` | `{}` → `{ok:true}`. Solicita o encerramento após os cortes em andamento. |
| GET `/api/cortes` | `{ok, cortes:[...]}`, do mais recente para o mais antigo. |
| POST `/api/abrir-video` | `{caminho}` → `{ok:true}`. Abre um MP4 da pasta de cortes no reprodutor do Windows. |
| POST `/api/abrir-pasta` | `{caminho?}` → `{ok:true}`. Abre a pasta do corte ou a pasta de saída quando vazio. |
| POST `/api/escolher-arquivo` | `{}` → `{ok, caminho}`. Caminho vazio ao cancelar o seletor local. |
| POST `/api/heartbeat`, `/api/janela-fechada` | Retornam `{ok:true}`. O ciclo de vida da janela é controlado pelo host. |

- `instalacao`: `rodando`, `etapa` (0 ocioso, 1–6 etapas, 7 concluído), `porcentagem` (0–100), `titulo_etapa`, `detalhe`, `erro`, `o_que_fazer`, `log_recente`.
- `sessao`: `rodando`, `estado`, `canal`, `live_s`, `atraso_s`, `cortes_sessao`, `ultima_mensagem`, `detalhe_servicos`, `erro`, `log_recente`, `log_completo`. `rodando` continua verdadeiro durante a finalização.
- Cada corte: `titulo`, `duracao` (segundos), `miniatura` (URL local autenticada), `caminho` (MP4), `pasta`, `caminho_sem_legenda`, `nome_arquivo`, `canal`, `data_hora`, `inicio_hms`, `motivo`, `modelo`. Dados ausentes como duração ou versão sem legenda podem vir nulos. Use `miniatura` como `img.src`. A URL `GET /api/miniatura?caminho=...` entrega JPEG ou 404.

## Drive

As rotas copiam os arquivos para uma pasta sincronizada pelo Google Drive para computador. O motor só confirma que a cópia local terminou. A confirmação de sincronização fica no aplicativo que gerencia a pasta. O CorteLive usa o caminho local e dispensa login, chaves e tokens próprios. A detecção também pode sugerir pastas do OneDrive e do Dropbox.

| Método e rota | Requisição e resposta |
| --- | --- |
| GET `/api/drive/detectar` | `{ok, opcoes, mensagem}`. Cada opção contém `nome`, `pasta_raiz` e `sugestao` (caminho completo para a subpasta `CorteLive`). `opcoes:[]` permite informar um caminho manualmente. |
| GET `/api/drive/config` | `{ok, destino_ativo, destino_pasta}`. |
| POST `/api/drive/config` | Recebe um ou mais campos de preferência e responde com as opções salvas e `mensagem`. A pasta deve ter caminho absoluto, ficar fora da pasta de saída dos cortes e existir ou ter uma pasta pai disponível. A subpasta sugerida é criada no primeiro envio. |
| POST `/api/drive/enviar` | `{caminho}` → `{ok, destino, mensagem}`. Envio manual do corte pronto, inclusive com a cópia automática desligada. Aguarde a resposta antes de liberar o botão. |

Use o `caminho` do vídeo principal devolvido em `GET /api/cortes`. O envio retorna 404 para cortes de fora da pasta de saída, arquivos internos e versões sem legenda. Uma opção inválida ou pasta indisponível retorna 400. Se ocorrer falha de cópia, o motor devolve 502 com mensagem em português. A operação pode receber 409 se a mesma pasta estiver ocupada.

O motor devolve `destino` também em cada item de `GET /api/cortes`. `destino` é `null` antes de haver histórico ou um objeto com `estado` (`"copiado"` ou `"erro"`) e `quando`, além de `caminho` e `erro` quando aplicáveis. As cópias ficam em `<destino_pasta>/<data, hora e canal da live>/`.

| Campo | Padrão | Efeito |
| --- | --- | --- |
| `destino_ativo` | `false` | Copia os próximos cortes prontos em segundo plano. |
| `destino_pasta` | `""` | Caminho completo da pasta sincronizada no Windows. |

O host Windows precisa encaminhar `/api/drive/detectar`, `/api/drive/config` e `/api/drive/enviar` ao motor pelos transportes descritos acima. Para o envio manual, dê ao processo tempo para copiar um MP4 inteiro. Use os campos de `GET /api/cortes` do motor ao montar a lista do painel. O menu e os botões do navegador chamam apenas a origem do host.

Pelo terminal, use `cortelive enviar-drive "<corte.mp4>"`.

## Preferências de corte

| Método e rota | Requisição e resposta |
| --- | --- |
| GET/POST `/api/config-postagem` | GET devolve as preferências abaixo. POST recebe qualquer subconjunto válido e devolve as preferências atualizadas. Só o POST grava em `painel_config.json`. O nome da rota ficou por compatibilidade; o CorteLive não publica nada nas redes. |

| Campo | Padrão | Valores |
| --- | --- | --- |
| `cortes_longos` | `false` | Booleano. Ligado: todo corte novo tem no mínimo 61 s (teto: o atual, ou 90 s se o atual for menor que 61 s); trecho que não chega a 61 s é descartado, nunca esticado. Vale para as lives iniciadas depois de salvar. |
| `duracao_minima_modo` | `"61"` | Texto: `"livre"`, `"30"`, `"45"` ou `"61"` (mínimo em segundos). Se a história não cabe no mínimo escolhido sobe 30 → 45 → 61 s; se nem 61 s comporta, divide em duas partes ("Parte 1" e "Parte 2"). Nada abaixo do mínimo sai. `cortes_longos` é derivado (`true` se não for livre). |

## Histórico

| Método e rota | Requisição e resposta |
| --- | --- |
| POST `/api/historico/apagar` | `{}` → `{ok, apagados, bytes_liberados, pulados, mensagem}`. Apaga `log.txt` e `sessao.json` das sessões em `cortes/` (em `_interno/` ou no formato antigo) e os arquivos `.log`, `.txt` e `.jsonl` de `logs/` parados há mais de 1 hora. Nunca apaga vídeos nem legendas. Sessão sem `sessao.json` e com log recente (live em andamento) é pulada e contada em `pulados`. O host responde 409 enquanto houver live ou instalação rodando. |

## Atualização

| Método e rota | Requisição e resposta |
| --- | --- |
| GET `/api/atualizacao/checar` | Resultado final do atualizador, com `etapa`, `pct`, `msg`, `versao_atual`, `versao_nova`, `erro` e `exit_code` quando disponível. |
| POST `/api/atualizacao/aplicar` | `{}` → resultado final do atualizador com os mesmos campos. Use `exit_code:2` para avisar que a versão anterior foi restaurada automaticamente. |
| POST `/api/atualizacao/voltar` | `{}` → resultado final da restauração manual com os mesmos campos. |

O host lê o resultado de [`instalador/atualizar.ps1`](../CONTRATO_ATUALIZACAO.md). O navegador pode continuar com o polling da Mesa de Corte enquanto aguarda a resposta de atualização.
