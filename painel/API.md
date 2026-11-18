# API local do CorteLive

O navegador acessa as rotas no host C# local. O host mantém a instalação, o progresso, o início e o fim da sessão, o seletor de arquivo, os estáticos, as rotas do navegador de reserva e a atualização. O motor mantém o processo de corte e oferece as rotas complementares de cortes, miniatura, abertura, Postiz, fila e configuração de postagem.

O host pode chamar `motor_painel.py --api-local --raiz <pasta>`, com cwd na pasta da instalação. Escreva `{"metodo":"GET","rota":"/api/cortes","corpo":{}}` em stdin UTF-8 e feche o stream. O motor responde com um envelope `{"status":200,"dados":{"ok":true,"cortes":[]}}`. Para a miniatura, o envelope traz `status`, `tipo:"image/jpeg"` e `base64`. O host repassa o status e o JSON ou decodifica a imagem. Para preencher `estado.cortes`, leia o campo `dados.cortes`. Evite registrar pedidos ou respostas brutas.

Para transporte HTTP entre processos, inicie `motor_painel.py --servir --porta 0 --raiz <pasta>` com um segredo aleatório em `CORTELIVE_PAINEL_TOKEN`. O motor escuta em `127.0.0.1` e escreve uma linha `{"porta":12345}` em stdout. Envie `X-CorteLive-Token` nas chamadas do host ao motor por loopback. O navegador usa somente a origem do host.

Use JSON UTF-8 em POST com corpo. Nas rotas complementares, erros retornam `{"ok":false,"erro":"mensagem"}` com status 400, 403, 404, 405, 502 ou 504.

## Instalação e corte

Use polling de `GET /api/estado` durante corte e instalação. O snapshot pode ser consultado novamente depois de uma reconexão.

| Método e rota | Requisição e resposta |
| --- | --- |
| GET `/api/ping` | `{ok, versao}`. |
| GET `/api/estado` | `{ok, versao, pasta_raiz, instalado, modo_descricao, instalacao, sessao, cortes}`. |
| POST `/api/instalar` | `{}` → `{ok:true}`. Começa a instalação em segundo plano. |
| GET `/api/log-instalacao` | `{ok, log}`. |
| POST `/api/cortar` | `{fonte, modelo?, legenda?}` → `{ok:true}`. `fonte` é link HTTP(S) ou caminho de um vídeo local. Padrões `cam-em-cima` e `opus`. |
| POST `/api/parar` | `{}` → `{ok:true}`. Solicita o encerramento após os cortes em andamento. |
| GET `/api/cortes` | `{ok, cortes:[...]}`, do mais recente para o mais antigo. |
| POST `/api/abrir-video` | `{caminho}` → `{ok:true}`. Abre um MP4 da pasta de cortes no reprodutor do Windows. |
| POST `/api/abrir-pasta` | `{caminho?}` → `{ok:true}`. Abre a pasta do corte ou a pasta de saída quando vazio. |
| POST `/api/escolher-arquivo` | `{}` → `{ok, caminho}`. Caminho vazio ao cancelar o seletor local. |
| POST `/api/heartbeat`, `/api/janela-fechada` | Retornam `{ok:true}`. O ciclo de vida da janela é controlado pelo host. |

- `instalacao`: `rodando`, `etapa` (0 ocioso, 1–6 etapas, 7 concluído), `porcentagem` (0–100), `titulo_etapa`, `detalhe`, `erro`, `o_que_fazer`, `log_recente`.
- `sessao`: `rodando`, `estado`, `canal`, `live_s`, `atraso_s`, `cortes_sessao`, `ultima_mensagem`, `detalhe_servicos`, `erro`, `log_recente`, `log_completo`. `rodando` continua verdadeiro durante a finalização.
- Cada corte: `titulo`, `duracao` (segundos), `miniatura` (URL local autenticada), `caminho` (MP4), `pasta`, `caminho_sem_legenda`, `nome_arquivo`, `canal`, `data_hora`, `inicio_hms`, `motivo`, `modelo`. Dados ausentes como duração ou versão sem legenda podem vir nulos. Use `miniatura` como `img.src`. A URL `GET /api/miniatura?caminho=...` entrega JPEG ou 404.

## Postagem

| Método e rota | Requisição e resposta |
| --- | --- |
| GET `/api/postar/status` | `{ok, disponivel, em_breve, contas}`. `contas` contém o resultado JSON do Postiz. |
| POST `/api/postar/conectar-postiz` | `{chave}` → `{ok:true, mensagem}`. A chave vai só pelo stdin do processo. O motor também aceita `/api/postar/conectar` com `{chave, rede:"postiz"}`. |
| POST `/api/postar/enviar` | `{video, titulo?, descricao?, redes}` → `{ok, eventos, saida}`. Envio manual após ação explícita. |
| GET `/api/autopost/fila` | `{ok, disponivel, itens}`. |
| POST `/api/autopost/gerar` | `{video, enfileirar?, canais_postiz?}` → `{ok, metadados, canal, video}` ou `{ok, novo, item}` ao enfileirar. |
| POST `/api/autopost/editar` | `{id, titulo?, descricao?, tiktok?, instagram?, tags?, canais_postiz?}` → `{ok, item}`. Campo omitido mantém o valor. |
| POST `/api/autopost/aprovar` | `{id}` → `{ok, item}` com `estado:"na_fila"`. Libera publicação no próximo ciclo, conforme prioridade e limites. |
| POST `/api/autopost/rejeitar` | `{id}` → `{ok, item}`. |
| POST `/api/autopost/processar` | `{}` → `{ok, itens}`. Executa um ciclo da agenda. No transporte `--api-local` o host deve disparar o ciclo em segundo plano a cada 30 segundos enquanto o app estiver aberto. No transporte `--servir`, o motor já executa esse ciclo. |
| GET/POST `/api/config-postagem` | GET devolve as preferências abaixo. POST recebe qualquer subconjunto válido e devolve as preferências atualizadas. Só o POST grava em `painel_config.json`. |
| GET `/api/autopost-navegador/status` | `{ok, disponivel, ...}` com o estado da integração local de reserva. |
| POST `/api/autopost-navegador/conectar` | `{rede}` → resultado JSON da CLI da reserva, quando habilitada. |

As chamadas de CLI de postagem têm prazo limitado e não impedem o polling de estado em paralelo. O painel deve manter o botão da ação ocupada desabilitado até receber a resposta.

| Campo | Padrão | Valores |
| --- | --- | --- |
| `postar_sozinho` | `false` | Booleano. |
| `revisar_antes` | `true` | Booleano. |
| `reserva_navegador` | `false` | Booleano. |
| `max_posts_dia` | `48` | Inteiro de 1 a 500, por rede, em janela móvel de 24 horas. Cada publicação em um canal consome uma vaga da rede. |
| `intervalo_min_posts` | `30` | Inteiro de 0 a 240 minutos entre publicações na mesma rede. Zero desliga o intervalo. |
| `canais_postiz` | `[]` | IDs dos canais padrão. Lista vazia usa todos os canais ativos das redes do corte. |
| `expirar_cortes_horas` | `48` | Inteiro positivo. Prazo contado a partir da entrada do corte na fila. |
| `rodizio` | `false` | Booleano. Quando ligado, escolhe um canal por rede usando o que publicou há mais tempo. |

O motor lê `postar_max_dia`, `postar_intervalo_min`, `postar_canais` e `postar_rodizio` salvos por versões anteriores quando a chave atual correspondente está ausente. O próximo POST válido grava os nomes atuais.

Use em `canais_postiz` os IDs devolvidos por `GET /api/postar/status` em `contas.postiz.canais[].id`. Ao enfileirar um corte em `/api/autopost/gerar`, omitir o campo faz a agenda usar a preferência geral quando for escolher os destinos. Enviar uma lista não vazia substitui a preferência para aquele corte. Enviar `null` seleciona todos os canais ativos das redes do corte, inclusive em itens antigos que já gravaram esse valor. As rotas de geração e edição por corte rejeitam `[]`. O rodízio escolhe dentro do conjunto selecionado. A edição de canais pode retornar 409 se o envio começou ou outro ciclo estiver em execução.

O ciclo ordena pela `pontuacao` do momento e usa o corte mais antigo para desempate. Quando falta vaga, a rede fica `pendente` e seu `proxima_tentativa` indica a hora Unix para tentar de novo. O estado global fica `na_fila` até os destinos terminarem ou muda para `expirado` quando o prazo acabar. Cada rede pode incluir `canais` com estado e link por ID. A agenda continua quando o aplicativo reabre. O Postiz Cloud tem um orçamento local compartilhado de 100 chamadas de criação de post por hora, incluindo novas tentativas. [Documentação do Postiz](https://docs.postiz.com/public-api/introduction)

## Atualização

| Método e rota | Requisição e resposta |
| --- | --- |
| GET `/api/atualizacao/checar` | Resultado final do atualizador, com `etapa`, `pct`, `msg`, `versao_atual`, `versao_nova`, `erro` e `exit_code` quando disponível. |
| POST `/api/atualizacao/aplicar` | `{}` → resultado final do atualizador com os mesmos campos. Use `exit_code:2` para avisar que a versão anterior foi restaurada automaticamente. |
| POST `/api/atualizacao/voltar` | `{}` → resultado final da restauração manual com os mesmos campos. |

O host lê o resultado de [`instalador/atualizar.ps1`](../CONTRATO_ATUALIZACAO.md). O navegador pode continuar com o polling da Mesa de Corte enquanto aguarda a resposta de atualização.
