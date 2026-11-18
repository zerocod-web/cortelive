# API local do CorteLive

O navegador acessa as rotas no host C# local. O host mantém a instalação, o progresso, o início e o fim da sessão, o seletor de arquivo, os estáticos, as rotas do navegador de reserva e a atualização. O motor mantém o processo de corte e oferece as rotas complementares de cortes, miniatura, abertura, Postiz, fila e configuração de postagem.

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

## Drive e marca de postagem

As rotas copiam os arquivos para uma pasta sincronizada pelo Google Drive para computador. O motor só confirma que a cópia local terminou. A confirmação de sincronização fica no aplicativo que gerencia a pasta. O CorteLive usa o caminho local e dispensa login, chaves e tokens próprios. A detecção também pode sugerir pastas do OneDrive e do Dropbox.

| Método e rota | Requisição e resposta |
| --- | --- |
| GET `/api/drive/detectar` | `{ok, opcoes, mensagem}`. Cada opção contém `nome`, `pasta_raiz` e `sugestao` (caminho completo para a subpasta `CorteLive`). `opcoes:[]` permite informar um caminho manualmente. |
| GET `/api/drive/config` | `{ok, destino_ativo, destino_pasta, destino_txt, destino_mover_postados}`. |
| POST `/api/drive/config` | Recebe um ou mais campos de preferência e responde com as opções salvas e `mensagem`. A pasta deve ter caminho absoluto, ficar fora da pasta de saída dos cortes e existir ou ter uma pasta pai disponível. A subpasta sugerida é criada no primeiro envio. |
| POST `/api/drive/enviar` | `{caminho}` → `{ok, destino, mensagem}`. Envio manual do corte pronto, inclusive com a cópia automática desligada. Aguarde a resposta antes de liberar o botão. |
| POST `/api/cortes/postado` | `{caminho, postado?, rede?}` → `{ok, postado, postagem, destino, mensagem}`. `postado` é booleano e o padrão é `true`. Omitir `rede` ou enviar `null` altera a marca geral. Redes aceitas: `youtube`, `instagram` e `tiktok`. |

Use o `caminho` do vídeo principal devolvido em `GET /api/cortes`. As mutações retornam 404 para cortes de fora da pasta de saída, arquivos internos e versões sem legenda. Uma opção inválida ou pasta indisponível retorna 400. Se ocorrer falha de cópia ou gravação, o motor devolve 502 com mensagem em português. A marca pode ser salva mesmo se a pasta do Drive estiver indisponível. Nesse caso, a resposta de sucesso traz `postagem.aviso` e o mesmo texto em `mensagem`. Repetir a ação tenta mover a cópia novamente.

O motor devolve `postado`, `postagem` e `destino` também em cada item de `GET /api/cortes`. Use o booleano `postado` para o filtro. Ele é verdadeiro se a marca geral estiver ativa ou se as três redes estiverem marcadas. Desmarcar a marca geral preserva as marcas individuais. `postagem` contém `{postado, quando, origem, redes}`. Cada rede traz `{postado, quando, origem}`. `quando` usa hora local no formato `AAAA-MM-DDTHH:MM:SS` ou `null`. Marcar registra a ação no painel e, se habilitado, move apenas a cópia no destino. A operação pode receber 409 se a mesma pasta estiver ocupada.

`destino` é `null` antes de haver histórico ou um objeto com `estado` (`"copiado"` ou `"erro"`) e `quando`, além de `caminho`, `txt` e `erro` quando aplicáveis. As cópias ficam em `<destino_pasta>/<data, hora e canal da live>/`. Se a organização estiver ativa, as cópias marcadas ficam em `postados/` dentro da pasta da live.

| Campo | Padrão | Efeito |
| --- | --- | --- |
| `destino_ativo` | `false` | Copia os próximos cortes prontos em segundo plano. |
| `destino_pasta` | `""` | Caminho completo da pasta sincronizada no Windows. |
| `destino_txt` | `false` | Inclui um TXT com os textos para as redes. |
| `destino_mover_postados` | `true` | Organiza as cópias marcadas na subpasta `postados`. |

O host Windows precisa encaminhar `/api/drive/detectar`, `/api/drive/config`, `/api/drive/enviar` e `/api/cortes/postado` ao motor pelos transportes descritos acima. Para o envio manual, dê ao processo tempo para copiar um MP4 inteiro. Use os campos de `GET /api/cortes` do motor ao montar a lista do painel. O menu e os botões do navegador chamam apenas a origem do host.

Pelo terminal, use `cortelive enviar-drive "<corte.mp4>"` ou `cortelive marcar-postado "<corte.mp4>"`. A marca aceita `--rede` e `--desmarcar`.

## Postagem

| Método e rota | Requisição e resposta |
| --- | --- |
| GET `/api/postar/status` | `{ok, disponivel, em_breve, contas}`. `contas` contém o resultado JSON do Postiz. |
| POST `/api/postar/conectar-postiz` | `{chave}` → `{ok:true, mensagem}`. A chave vai só pelo stdin do processo. O motor também aceita `/api/postar/conectar` com `{chave, rede:"postiz"}`. |
| POST `/api/postar/enviar` | `{video, titulo?, descricao?, redes}` → `{ok, eventos, saida}`. Envio manual após ação explícita. |
| GET `/api/autopost/fila` | `{ok, disponivel, itens}`. Cada item traz `agendado_para` em UTC quando há envio confirmado pelo Postiz. |
| POST `/api/autopost/gerar` | `{video, enfileirar?, canais_postiz?}` → `{ok, metadados, canal, video}` ou `{ok, novo, item}` ao enfileirar. |
| POST `/api/autopost/editar` | `{id, titulo?, descricao?, tiktok?, instagram?, tags?, canais_postiz?}` → `{ok, item}`. Campo omitido mantém o valor. |
| POST `/api/autopost/aprovar` | `{id}` → `{ok, item}` com `estado:"na_fila"`. Libera o envio ao Postiz no próximo ciclo. |
| POST `/api/autopost/rejeitar` | `{id}` → `{ok, item}`. |
| POST `/api/autopost/editar-agendado` | `{id, titulo?, descricao?, tiktok?, instagram?, tags?}` → `{ok, item}`. Edita os textos dos canais futuros, no mesmo horário. `tags` é uma string separada por vírgulas para o YouTube. |
| POST `/api/autopost/cancelar` | `{id}` → `{ok, item}`. Cancela os agendamentos futuros confirmados do corte. Pode repetir após falha parcial, pois o motor guarda os cancelamentos concluídos. |
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
| `cortes_longos` | `false` | Booleano. Ligado: todo corte novo tem no mínimo 61 s (teto: o atual, ou 90 s se o atual for menor que 61 s); trecho que não chega a 61 s é descartado, nunca esticado. Vale para as lives iniciadas depois de salvar. |
| `max_posts_dia` | `48` | Inteiro de 1 a 500, por canal do Postiz, em janela móvel de 24 horas. |
| `intervalo_min_posts` | `30` | Inteiro de 0 a 240 minutos entre publicações no mesmo canal do Postiz. Zero desliga o intervalo. |
| `canais_postiz` | `[]` | IDs dos canais padrão. Lista vazia usa todos os canais ativos das redes do corte. |
| `expirar_cortes_horas` | `48` | Inteiro positivo. Prazo para a fila local processar o corte, contado a partir da entrada. O horário já confirmado no Postiz pode cair depois desse prazo. |
| `rodizio` | `false` | Booleano. Quando ligado, escolhe um canal por rede, priorizando o canal com o último horário mais antigo. |
| `usar_horarios_postiz` | `false` | Booleano. Usa a próxima vaga sugerida pelo Postiz como horário mínimo do canal. Se a consulta falhar, usa o cálculo local. |

O motor lê `postar_max_dia`, `postar_intervalo_min`, `postar_canais` e `postar_rodizio` salvos por versões anteriores quando a chave atual correspondente está ausente. O próximo POST válido grava os nomes atuais.

Use em `canais_postiz` os IDs devolvidos por `GET /api/postar/status` em `contas.postiz.canais[].id`. Ao enfileirar um corte em `/api/autopost/gerar`, omitir o campo faz a agenda usar a preferência geral quando for escolher os destinos. Enviar uma lista não vazia substitui a preferência para aquele corte. Enviar `null` seleciona todos os canais ativos das redes do corte, inclusive em itens antigos que já gravaram esse valor. As rotas de geração e edição por corte rejeitam `[]`. O rodízio escolhe dentro do conjunto selecionado. A edição de canais pode retornar 409 se o envio começou ou outro ciclo estiver em execução.

O ciclo ordena pela `pontuacao` e usa o corte mais antigo para desempate. Em modo de revisão, só transfere para o Postiz após aprovação. O motor calcula as datas a partir do histórico local por canal e envia os horários futuros no mesmo ciclo. `proxima_tentativa` é a hora Unix para repetir um envio que pôde ser adiado, por exemplo por limite de chamadas. Quando a resposta da criação é inconclusiva, o estado é `confirmacao_pendente`. Consulte o calendário do Postiz para confirmar o envio. O motor guarda a vaga e evita repetir uma criação possivelmente aceita.

`item.agendado_para` é a primeira data confirmada em formato ISO 8601 UTC, ou `null` quando nenhum horário está confirmado. Em itens antigos o campo pode estar ausente. Em `item.redes[rede].canais[id]`, um agendado traz `estado:"agendado"`, `agendado_para`, `post_id` e `link`. Use esses horários para mostrar as datas por canal e formate no fuso local. `agendado` indica o aceite do Postiz. A confirmação de publicação é consultada no calendário do Postiz.

Para a interface Windows, o host precisa encaminhar `/api/autopost/editar-agendado`, `/api/autopost/cancelar` e os ciclos em segundo plano ao motor. Na fila, ler `item.agendado_para` e ligar as ações às novas rotas. As duas mutações devolvem 409 se a fila estiver ocupada ou o corte estiver sem agendamento futuro disponível. Uma falha de edição em vários canais pode ocorrer após alguns terem sido atualizados. Repetir a edição reenvia os mesmos IDs para os canais que ainda estiverem agendados.

**Contrato do Postiz.** `POST /posts` documenta `type:"now"` para envio imediato, `"draft"` para rascunho e `"schedule"` com `date` ISO 8601 UTC para horário definido. A consulta `GET /find-slot/{id}` devolve uma sugestão por canal. A documentação descreve a consulta sem garantia de reserva ou aplicação das regras de intervalo, cota e rodízio do CorteLive. A cota local abrange o histórico gravado pelo CorteLive, incluindo os horários que já reservou. Agendamentos criados fora do app ficam a cargo do calendário do Postiz. O Postiz Cloud documenta 100 chamadas ao endpoint de criação por hora. [Criação](https://docs.postiz.com/public-api/posts/create) · [Próxima vaga](https://docs.postiz.com/public-api/integrations/find-slot) · [Limites](https://docs.postiz.com/public-api/introduction)

O cancelamento usa `DELETE /posts/{id}`. O Postiz apaga o grupo associado, por isso o CorteLive cria um grupo por canal. A documentação pública oferece atualização de configurações e mudança de estado. Para editar o texto, o motor confirma o agendamento por `GET /posts` e usa o comportamento de atualização por `posts[].value[].id` do código oficial atual do Postiz. A edição preserva a data e exige que a resposta confirme o mesmo ID. A compatibilidade com versões self-hosted mais antigas depende da implementação daquela versão. Ajuste de data, vídeo e canais de um post confirmado ficam no calendário do Postiz. [Cancelar](https://docs.postiz.com/public-api/posts/delete) · [Listar](https://docs.postiz.com/public-api/posts/list) · [Configurações](https://docs.postiz.com/public-api/posts/settings) · [Estado](https://docs.postiz.com/public-api/posts/change-status) · [Código oficial da atualização por ID](https://github.com/gitroomhq/postiz-app/blob/main/libraries/nestjs-libraries/src/database/prisma/posts/posts.repository.ts)

## Histórico

| Método e rota | Requisição e resposta |
| --- | --- |
| POST `/api/historico/apagar` | `{}` → `{ok, apagados, bytes_liberados, pulados, mensagem}`. Apaga `log.txt` e `sessao.json` das sessões em `cortes/` (em `_interno/` ou no formato antigo) e os arquivos `.log`, `.txt` e `.jsonl` de `logs/` parados há mais de 1 hora. Nunca apaga vídeos, legendas, `.autopost.jsonl` nem a fila. Sessão sem `sessao.json` e com log recente (live em andamento) é pulada e contada em `pulados`. O host responde 409 enquanto houver live ou instalação rodando. |

## Atualização

| Método e rota | Requisição e resposta |
| --- | --- |
| GET `/api/atualizacao/checar` | Resultado final do atualizador, com `etapa`, `pct`, `msg`, `versao_atual`, `versao_nova`, `erro` e `exit_code` quando disponível. |
| POST `/api/atualizacao/aplicar` | `{}` → resultado final do atualizador com os mesmos campos. Use `exit_code:2` para avisar que a versão anterior foi restaurada automaticamente. |
| POST `/api/atualizacao/voltar` | `{}` → resultado final da restauração manual com os mesmos campos. |

O host lê o resultado de [`instalador/atualizar.ps1`](../CONTRATO_ATUALIZACAO.md). O navegador pode continuar com o polling da Mesa de Corte enquanto aguarda a resposta de atualização.
