# Servidor Intermediário de Tokens (`servidor_tokens`) — CorteLive

## Por que este servidor existe?

O repositório do CorteLive é público e o programa vai para o computador dos amigos. Qualquer segredo gravado no código ou na configuração do PC de um amigo poderia ser extraído.

- **Meta (Instagram)** e **TikTok** exigem o segredo confidencial do aplicativo na hora de trocar o código de login (`code`) por `access_token` e na hora de renovar (`refresh_token` / `fb_exchange_token`).
- Este **Cloudflare Worker** gratuito faz apenas essa ponte: os segredos moram criptografados nos servidores da Cloudflare, **nunca** vão para o Git e **nunca** vão para o PC dos amigos.
- O token de acesso final de cada amigo volta direto para o PC dele e é salvo em `%LOCALAPPDATA%\CorteLive\contas\<rede>.bin` criptografado com **DPAPI do Windows** — o Worker **não guarda banco de dados nem armazena tokens de ninguém**.

---

## Pré-requisitos

1. Conta gratuita na [Cloudflare](https://dash.cloudflare.com/sign-up) (o plano grátis inclui até 100.000 requisições por dia, mais do que suficiente para todos os amigos).
2. [Node.js 18+](https://nodejs.org/) instalado apenas na máquina de quem vai publicar o Worker uma vez (os amigos **não** precisam de Node.js).

---

## Passo a passo de Deploy (em 3 minutos)

Abra o PowerShell nesta pasta (`cortelive\postar\servidor_tokens\`) e rode:

### 1. Fazer login na Cloudflare
```powershell
npx wrangler login
```

### 2. Cadastrar os segredos (eles NÃO vão para nenhum arquivo!)
Cada comando abaixo pede para você colar o segredo no terminal e envia criptografado direto para a Cloudflare:

```powershell
npx wrangler secret put META_APP_SECRET
npx wrangler secret put TIKTOK_CLIENT_SECRET
```

*(Opcional: se também quiser passar a renovação/troca do YouTube pelo Worker em vez de cliente Desktop local)*:
```powershell
npx wrangler secret put YOUTUBE_CLIENT_SECRET
```

### 3. (Opcional) Travar os IDs públicos no `wrangler.toml`
Se quiser impedir que qualquer outro aplicativo use o seu Worker, descomente `META_APP_ID` e `TIKTOK_CLIENT_KEY` em `[vars]` no `wrangler.toml` (esses IDs são públicos — já aparecem na URL de login do navegador).

### 4. Publicar o Worker
```powershell
npx wrangler deploy
```

Ao terminar, o Wrangler mostrará uma URL parecida com:
```text
https://cortelive-servidor-tokens.<seu-subdominio>.workers.dev
```

### 5. Conferir se subiu
Abra no navegador:
```text
https://cortelive-servidor-tokens.<seu-subdominio>.workers.dev/health
```
Deve aparecer `{"ok":true,"servico":"cortelive-servidor-tokens","redes":["instagram","tiktok","youtube"]}`.

### 6. Configurar a URL no CorteLive
No arquivo `%LOCALAPPDATA%\CorteLive\postar.toml`, preencha apenas a URL pública do Worker e os IDs públicos dos apps (sem nenhum segredo de TikTok/Meta!):

```toml
[servidor_tokens]
url = "https://cortelive-servidor-tokens.<seu-subdominio>.workers.dev"

[instagram]
client_id = "SEU_APP_ID_META"

[tiktok]
client_key = "SEU_CLIENT_KEY_TIKTOK"
```

---

## Contrato HTTP do Worker

### `POST /oauth/trocar`
Entrada JSON:
```json
{
  "rede": "tiktok",
  "client_id": "SEU_CLIENT_KEY_TIKTOK",
  "code": "CODIGO_RECEBIDO_NO_LOOPBACK",
  "redirect_uri": "http://127.0.0.1:8765/callback",
  "code_verifier": "VERIFIER_PKCE_S256"
}
```
> Por segurança, o Worker rejeita qualquer `redirect_uri` que não seja `http://127.0.0.1` ou `http://localhost`.

### `POST /oauth/renovar`
Entrada JSON:
```json
{
  "rede": "tiktok",
  "client_id": "SEU_CLIENT_KEY_TIKTOK",
  "refresh_token": "REFRESH_TOKEN_SALVO_NO_COFRE_DPAPI"
}
```
