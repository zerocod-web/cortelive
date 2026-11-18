# Guia de Postagem Oficial — CorteLive

Este guia explica, em português simples, como funciona a postagem oficial dos cortes no **YouTube Shorts**, **Instagram Reels** e **TikTok** — tanto no modo **Direto** (`--via direto`, padrão atual) quanto via **Postiz** (`--via postiz`).

> **Divisão de responsabilidades:**
> - Este pacote (`cortelive.postar`) cuida exclusivamente das conexões seguras, cofre DPAPI e envio dos vídeos sob comando.
> - O subcomando `sugerir` fica disponível como reserva local gratuita, mas **não há nenhum gancho automático no fim do corte aqui**: o modo *"postar ao cortar"* e a geração principal de metadados por rede ficam com o módulo `cortelive.autopost`.

---

## 1. Onde cada segredo mora

O repositório do CorteLive é público e o aplicativo roda no computador dos amigos. Por isso, **nenhuma chave confidencial de desenvolvedor vai para o GitHub nem para o PC dos amigos**:

| Credencial / Segredo | Onde mora | Vai para o repositório? | Vai para o PC do amigo? |
| :--- | :--- | :--- | :--- |
| **Código-fonte e `config.exemplo.toml`** | Pasta do projeto | Sim (apenas placeholders de exemplo) | Sim (sem nenhum segredo) |
| **Segredo do App da Meta (Instagram)** (`META_APP_SECRET`) | Variável criptografada do Cloudflare Worker (`cortelive\postar\servidor_tokens\`, via `npx wrangler secret put`) | **Não** | **Não** (proibido e bloqueado na config local) |
| **Segredo do App do TikTok** (`TIKTOK_CLIENT_SECRET`) | Variável criptografada do Cloudflare Worker (`cortelive\postar\servidor_tokens\`, via `npx wrangler secret put`) | **Não** | **Não** (proibido e bloqueado na config local) |
| **Cliente OAuth do YouTube** (`Desktop app` + PKCE `S256`) | `%LOCALAPPDATA%\CorteLive\postar.toml` (ou opcionalmente no `servidor_tokens`). O Google trata o cliente *Desktop app* como não confidencial, protegido por PKCE `S256`. | **Não** | Apenas o `client_id` (e, se usado localmente fora do Worker, credencial Desktop em `%LOCALAPPDATA%`, fora do repo) |
| **Tokens OAuth pessoais de cada amigo** (`access_token` e `refresh_token` de YouTube, Instagram e TikTok) | `%LOCALAPPDATA%\CorteLive\contas\<rede>.bin`, criptografados com **DPAPI do Windows** (`CryptProtectData`) | **Não** | Sim, somente os tokens da própria conta do amigo, cifrados para o usuário Windows dele |
| **API Key pessoal do Postiz** (`--via postiz`) | `%LOCALAPPDATA%\CorteLive\contas\postiz.bin`, criptografada com **DPAPI do Windows** (`CryptProtectData`) | **Não** | Sim, somente a chave que o próprio amigo colou no `conectar postiz`, cifrada via DPAPI |

---

## 2. Opção A (Padrão atual): Envio Direto (`--via direto`) + `servidor_tokens`

### 2.1. O que funciona na hora vs. o que depende de aprovação das redes

| Rede | Funciona SEM aprovação / auditoria? | O que depende de aprovação / verificação? | Observação importante sobre privacidade |
| :--- | :--- | :--- | :--- |
| **YouTube (Google Cloud)** | **Sim, para até 100 amigos** cadastrados na lista de *Usuários de teste (Test users)* da tela OAuth. Cota gratuita inicial: **6 vídeos/dia** no projeto (10.000 unidades/dia; 1.600 por envio). | Publicar direto como **Público** via API e liberar login para qualquer pessoa fora da lista de teste exigem a **Verificação de API do Google** (gratuita). | Enquanto o app não for verificado pelo Google, vídeos enviados via API saem travados como **Privados** (mudança manual em 2 cliques no YouTube Studio). |
| **TikTok (TikTok for Developers)** | **Sim, no modo Sandbox** para contas adicionadas em *Target Users*. | Postar direto como **Público (`PUBLIC_TO_EVERYONE`)** e abrir para qualquer conta exigem a **Auditoria gratuita (App Audit)** do *Content Posting API*. | Antes da auditoria aprovada, a API só aceita `SELF_ONLY` (**Privado** — use `--privado` ou `forcar_privado = true`). |
| **Instagram (Meta for Developers)** | **Sim, no Modo de Desenvolvimento** para você e amigos convidados como **Testadores do App / Testadores do Instagram**. | Liberar para desconhecidos exige **App Review** + Verificação de Empresa na Meta. | A conta do Instagram de quem posta **precisa ser Profissional** (*Criador* ou *Comercial*, gratuito) e **vinculada a uma Página do Facebook**. O upload local usa o protocolo oficial Resumable (`rupload.facebook.com`) sem precisar de servidor de arquivos. |

### 2.2. Publicar o `servidor_tokens` (uma única vez pelo mantenedor)

Siga o passo a passo completo em [`cortelive\postar\servidor_tokens\README.md`](cortelive/postar/servidor_tokens/README.md):
1. Crie os apps em [developers.facebook.com/apps](https://developers.facebook.com/apps/) (Meta/Instagram) e [developers.tiktok.com](https://developers.tiktok.com/) (Login Kit + Content Posting API), cadastrando `http://127.0.0.1:8765/callback` como Redirect URI.
2. Grave os segredos **somente** no Cloudflare Worker gratuito:
   ```powershell
   cd cortelive\postar\servidor_tokens
   npx wrangler login
   npx wrangler secret put META_APP_SECRET
   npx wrangler secret put TIKTOK_CLIENT_SECRET
   npx wrangler deploy
   ```
3. No Google Cloud ([console.cloud.google.com](https://console.cloud.google.com/)), ative a **YouTube Data API v3** e crie uma credencial OAuth do tipo **App para computador (Desktop app)** (com escopos `youtube.upload` e `youtube.readonly`).
4. Copie `cortelive\postar\config.exemplo.toml` para `%LOCALAPPDATA%\CorteLive\postar.toml` e preencha apenas a URL pública do Worker (`[servidor_tokens] url`) e os IDs públicos (`[youtube] client_id`, `[instagram] client_id`, `[tiktok] client_key`).

---

## 3. Opção B: Provedor Postiz (`--via postiz`)

Com `--via postiz`, o amigo conecta YouTube, Instagram e TikTok direto pelo painel web do Postiz e cola apenas a **API Key** dele no CorteLive (que fica protegida com DPAPI em `%LOCALAPPDATA%\CorteLive\contas\postiz.bin`).

### 3.1. Preço real e limites depois dos 7 dias grátis (Postiz Cloud)

Fonte oficial lida em [`https://postiz.com/pricing`](https://postiz.com/pricing):

- **Não existe plano gratuito permanente** no Postiz Cloud (`platform.postiz.com`).
- Todos os planos começam com **7 dias grátis ($0)** para testar e incluem **posts ilimitados por mês**, acesso à **Public API**, CLI, servidor MCP e Webhooks.
- Passados os 7 dias grátis, a cobrança segue o limite de **canais conectados** (cada perfil/conta social conectada conta como 1 canal):

| Plano Postiz Cloud | Preço mensal | Preço no plano anual | Limite de canais | Destaques e limites mensais |
| :--- | :--- | :--- | :--- | :--- |
| **Standard** | **$29/mês** | **$23/mês** | **5 canais** | Posts ilimitados/mês, API + CLI + Webhooks, 3 vídeos IA/mês, 60 min de clipping IA/mês (1 usuário, sem membros de equipe) |
| **Team** | **$39/mês** | **$31/mês** | **10 canais** | Posts ilimitados/mês, membros de equipe ilimitados, 100 imagens IA/mês, 10 vídeos IA/mês, 120 min de clipping IA/mês |
| **Pro** | **$49/mês** | **$39/mês** | **30 canais** | Posts ilimitados/mês, membros ilimitados, 300 imagens IA/mês, 30 vídeos IA/mês, 300 min de clipping IA/mês |
| **Ultimate** | **$99/mês** | **$79/mês** | **100 canais** | Posts ilimitados/mês, membros ilimitados, 500 imagens IA/mês, 60 vídeos IA/mês, 600 min de clipping IA/mês |

*(Além da cota de canais do plano, a Public API possui rate-limit técnico de **100 requisições/hora no Cloud** e **90 requisições/hora por padrão no self-host** no endpoint de criação de posts).*

### 3.2. Opção Self-Host (Grátis, código aberto)

Fontes oficiais lidas em [`https://docs.postiz.com/self-host/installation/overview`](https://docs.postiz.com/self-host/installation/overview) e [`https://docs.postiz.com/self-host/installation/system-requirements`](https://docs.postiz.com/self-host/installation/system-requirements):

- **Custo do software:** **$0 (gratuito)**, open-source sob licença AGPL-3.0, sem trava de canais (repositório oficial Docker Compose: `github.com/gitroomhq/postiz-docker-compose`).
- **O que você precisa fornecer:**
  1. Um servidor Linux (VPS ou máquina dedicada) rodando **Docker Compose** com PostgreSQL 14+, Redis 6+ e Temporal — mínimo suportado de **2 vCPU, 2 GB de RAM e 20 GB de disco** (para 1 pessoa com uso leve), sendo recomendado **4 vCPU, 8 GB de RAM e 50 GB de disco**.
  2. Um domínio com proxy reverso HTTPS (Caddy, Nginx ou Traefik).
  3. **Suas próprias chaves de desenvolvedor** cadastradas e aprovadas em cada rede social (Google Cloud, Meta e TikTok) — diferente do Postiz Cloud, o self-host não traz os apps das redes pré-aprovados.
- **Como apontar o CorteLive para o seu Postiz self-host:** em `%LOCALAPPDATA%\CorteLive\postar.toml`, defina:
  ```toml
  [postiz]
  base_url = "https://seu-postiz.seudominio.com/api/public/v1"
  ```

### 3.3. Como conectar e usar o Postiz no CorteLive

1. Na sua conta do Postiz, conecte seus canais de YouTube, Instagram e TikTok.
2. Copie sua chave em **Settings → Public API** e rode:
   ```cmd
   python -m cortelive.postar conectar postiz --chave SUA_API_KEY
   ```
   *(Ou rode apenas `python -m cortelive.postar conectar postiz` para colar a chave sem exibi-la no histórico do terminal).*
3. Confira os canais conectados na sua conta Postiz:
   ```cmd
   python -m cortelive.postar status --via postiz
   ```
4. Envie um corte usando o Postiz:
   ```cmd
   python -m cortelive.postar enviar --via postiz --video cortes\sessao\corte_001.mp4 --redes youtube,instagram,tiktok --privado
   ```

### 3.4. Documentação oficial da Public API consultada (`docs.postiz.com`)

- Visão geral, cabeçalho `Authorization: <api-key>`, rate limits e erros: `https://docs.postiz.com/public-api/introduction`
- Listar canais conectados (`GET /public/v1/integrations`): `https://docs.postiz.com/public-api/integrations/list`
- Upload multipart de vídeo MP4 (`POST /public/v1/upload`): `https://docs.postiz.com/public-api/uploads/upload-file`
- Status de processamento de mídia (`GET /public/v1/media/{id}/status`): `https://docs.postiz.com/public-api/uploads/media-status`
- Criar/publicar post (`POST /public/v1/posts`): `https://docs.postiz.com/public-api/posts/create`
- Configurações por plataforma:
  - YouTube (`__type: "youtube"`): `https://docs.postiz.com/public-api/providers/youtube`
  - Instagram Business (`__type: "instagram"`): `https://docs.postiz.com/public-api/providers/instagram`
  - Instagram Standalone (`__type: "instagram-standalone"`): `https://docs.postiz.com/public-api/providers/instagram-standalone`
  - TikTok (`__type: "tiktok"`): `https://docs.postiz.com/public-api/providers/tiktok`

---

## 4. Referência rápida da CLI (contrato JSON com o Painel e `autopost`)

```cmd
REM Modo direto (padrao)
python -m cortelive.postar conectar youtube
python -m cortelive.postar conectar instagram
python -m cortelive.postar conectar tiktok
python -m cortelive.postar status
python -m cortelive.postar enviar --video cortes\sessao\corte_001.mp4 --redes youtube,instagram,tiktok --privado

REM Modo Postiz (--via postiz)
python -m cortelive.postar conectar postiz --chave SUA_API_KEY
python -m cortelive.postar status --via postiz
python -m cortelive.postar enviar --via postiz --video cortes\sessao\corte_001.mp4 --redes youtube,instagram,tiktok --privado

REM Simulacao sem postar nada (--dry-run) e sugestao reserva local
python -m cortelive.postar enviar --video cortes\sessao\corte_001.mp4 --redes youtube,instagram,tiktok --via postiz --dry-run
python -m cortelive.postar sugerir --video cortes\sessao\corte_001.mp4
```

Cada evento de `enviar` sai em uma linha JSONL com flush imediato:
```json
{"rede": "youtube", "etapa": "preparando", "pct": 5}
{"rede": "youtube", "etapa": "enviando", "pct": 70}
{"rede": "youtube", "etapa": "concluido", "pct": 100, "link": "https://..."}
```
