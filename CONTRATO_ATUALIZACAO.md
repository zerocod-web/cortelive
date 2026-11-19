# Contrato de Atualização Automática (`CONTRATO_ATUALIZACAO.md`)

Este documento define o contrato exato entre o **Painel do CorteLive** (ou linha de comando) e o motor de atualização (`instalador\atualizar.ps1` / `atualizar.cmd`).

---

## 1. Configuração em um único lugar

- **Versão instalada:** arquivo `VERSION` único na raiz (ex.: `0.1.0`, sem prefixo `v`).
- **Repositório público no GitHub:** variável `$REPO_GITHUB` no topo de [`instalador/versoes.ps1`](instalador/versoes.ps1) (`<dono>/cortelive`).
  - Se `$REPO_GITHUB` ainda estiver com `<dono>/cortelive` num ambiente de desenvolvimento que já tenha `.git` / `gh`, o script tenta descobrir `<dono>/<repo>` automaticamente via `git remote get-url origin` ou `gh repo view --json owner,name`.
  - No GitHub Actions ([`.github/workflows/release.yml`](.github/workflows/release.yml)), `empacotar.ps1` substitui `<dono>/cortelive` automaticamente por `$env:GITHUB_REPOSITORY` dentro do `cortelive_amigos.zip` gerado.

---

## 2. Como chamar

O painel pode invocar diretamente o script PowerShell (recomendado) ou o wrapper `.cmd`:

```powershell
# 1) Checar se há versão nova (consulta API pública sem token, com timeout e cache)
powershell -NoProfile -ExecutionPolicy Bypass -File "instalador\atualizar.ps1" -Checar

# 2) Aplicar atualização (baixa zip, confere SHA-256, faz backup, troca só o código, testa e faz rollback automático se falhar)
powershell -NoProfile -ExecutionPolicy Bypass -File "instalador\atualizar.ps1" -Aplicar

# 3) Voltar manualmente para a versão anterior guardada em versoes_antigas\
powershell -NoProfile -ExecutionPolicy Bypass -File "instalador\atualizar.ps1" -Voltar
```

Equivalente via `atualizar.cmd` (quando chamado com argumentos não executa `pause`; sem argumentos executa `-Aplicar` e pausa ao final a menos que `CORTELIVE_SEM_PAUSA=1`):

```cmd
atualizar.cmd -Checar
atualizar.cmd -Aplicar
atualizar.cmd -Voltar
```

Parâmetros auxiliares opcionais em `instalador\atualizar.ps1`:
- `-Canal <estavel|beta>`: canal de lançamento (padrão: `estavel`, que consulta `/releases/latest` e ignora pre-releases; `beta`, exclusivo do `CorteLive Dev.exe`, consulta `/releases?per_page=15` e inclui pre-releases ordenadas por SemVer 2.0).
- `-Raiz <caminho>`: sobrescreve a pasta raiz da instalação (padrão: pai de `instalador\`).
- `-SemCache`: ignora o cache local ao consultar releases.
- `-TimeoutSeg <int>`: timeout HTTP em segundos (padrão: `15`).
- `-ApiBase <url>` / `-Repo <dono/repo>`: sobrescritas diretas para testes automatizados.

---

## 3. Formato de saída (stdout = JSON Lines UTF-8)

Toda a saída em `stdout` consiste **exclusivamente** em linhas JSON UTF-8 (NDJSON, um objeto por linha, sem BOM), sempre com as mesmas **6 chaves**:

```json
{"etapa":"disponivel","pct":100,"msg":"Nova versão 0.1.1 disponível.","versao_atual":"0.1.0","versao_nova":"0.1.1","erro":null}
```

### Campos de cada linha

| Campo | Tipo | Descrição |
|---|---|---|
| `etapa` | `string` | Identificador da etapa atual (ver tabela abaixo). |
| `pct` | `int` (`0..100`) | Progresso percentual da operação (`100` na linha final). |
| `msg` | `string` | Mensagem curta em português pronta para exibir na barra de progresso do painel. |
| `versao_atual` | `string` | Versão atualmente ativa na pasta (`VERSION` sem `v`, ex.: `"0.1.0"`). Na conclusão de `-Aplicar` passa a ser a versão nova (`"0.1.1"`); em rollback volta a ser `"0.1.0"`. |
| `versao_nova` | `string \| null` | Versão mais recente encontrada ou alvo da operação (ex.: `"0.1.1"`), ou `null` quando desconhecida (ex.: início da checagem ou erro de rede). |
| `erro` | `string \| null` | `null` em etapas normais; string em português explicando a causa quando há falha ou rollback automático. |

Logs detalhados de rede e do `instalar.ps1` vão para `atualizacao.log` na raiz (nunca sujam o `stdout`).

### Valores de `etapa`

| Modo | `etapa` | `pct` típico | Significado |
|---|---|---|---|
| `-Checar` | `checando` | `15` | Consultando a API de releases (ou cache). |
| `-Checar` | `disponivel` | `100` | Há versão mais nova (`versao_nova > versao_atual`). Exit `0`. |
| `-Checar` | `em_dia` | `100` | Já está na versão mais recente (`versao_nova <= versao_atual`). Exit `0`. |
| `-Aplicar` | `checando` | `5` | Consultando a última release publicada. |
| `-Aplicar` | `em_dia` | `100` | Nada para atualizar. Exit `0`. |
| `-Aplicar` | `baixando` | `20` | Baixando `cortelive_amigos.zip` e `cortelive_amigos.zip.sha256`. |
| `-Aplicar` | `verificando` | `45` | Conferindo hash SHA-256 do `.zip` contra o `.sha256` publicado. |
| `-Aplicar` | `backup` | `60` | Salvando o código atual em `versoes_antigas\v<versao_atual>\`. |
| `-Aplicar` | `aplicando` | `72` | Substituindo apenas o código (preservando modelos, `.venv`, FFmpeg, cortes e configs). |
| `-Aplicar` | `dependencias` | `82` | `requirements.txt` ou `instalador\versoes.ps1` mudou → rodando `instalar.ps1 -SemPerguntas`. |
| `-Aplicar` | `validando` | `92` | Testando inicialização da nova versão. |
| `-Aplicar` | `rollback` | `96` | Nova versão falhou ao aplicar/iniciar → restaurando automaticamente o backup anterior. |
| `-Aplicar` / `-Voltar` | `concluido` | `100` | Atualização ou rollback manual finalizado com sucesso. Exit `0`. |
| `-Voltar` | `rollback` | `20` | Restaurando backup de `versoes_antigas\`. |
| `-Voltar` | `dependencias` | `65` | Sincronizando bibliotecas caso `requirements.txt`/`versoes.ps1` difiram. |
| `-Voltar` | `validando` | `85` | Validando inicialização da versão restaurada. |
| Todos | `erro` | `100` | Operação encerrada com falha (`erro` preenchido; código de saída != `0`). |

### Códigos de saída (`exit code`)

- `0`: Sucesso (`disponivel`, `em_dia` ou `concluido`).
- `1`: Erro sem alteração da instalação (ex.: offline, timeout, SHA-256 recusado, sem backup para `-Voltar`).
- `2`: Nova versão falhou ao iniciar em `-Aplicar`, mas o **rollback automático restaurou a versão anterior com sucesso** (`versao_atual` volta à versão anterior funcional).
- `3`: Falha crítica tanto na atualização quanto na tentativa de restaurar o backup.

---

## 4. Garantias de preservação e segurança

1. **SHA-256 obrigatório:** `-Aplicar` exige o asset `.sha256` na Release e valida o hash antes de tocar em qualquer arquivo da pasta. Se faltar ou divergir, descarta o download temporário e encerra com código `1`.
2. **Intocados na atualização e no rollback (mesmo conteúdo e mesmo `mtime`):**
   - Pastas pesadas e dados: `.venv\`, `python\`, `ffmpeg\`, `ferramentas\`, `modelos\`, `cortes\`, `versoes_antigas\`, `.buffer\`, `logs\`.
   - Configurações locais do usuário: `config.toml` (na raiz), `usuario.toml`, `lives.txt`, `painel_config.json`, `instalador\modo.txt`, arquivos `*.local.*`, além de `%LOCALAPPDATA%\cortelive\` e `%LOCALAPPDATA%\CorteLive\`.
   - Nota: `cortelive\config.toml` é o template padrão do código e **é** atualizado; as customizações do usuário em `config.toml` da raiz permanecem intactas.
3. **Backup e retenção:** backups ficam em `versoes_antigas\v<versao>\` com `versoes_antigas\ultima.txt` apontando para a versão imediatamente anterior (mantém até as 3 últimas versões de código, poucos KB/MB).
4. **Cache da API do GitHub:** salvo em `instalador\cache_release.local.json` com TTL padrão de 15 minutos (`900` s) + `ETag` (`If-None-Match` → `304 Not Modified`), evitando estourar o limite público de 60 req/h por IP do GitHub sem token.

---

## 5. Variáveis de ambiente (incluindo testes locais)

| Variável | Padrão | Uso |
|---|---|---|
| `CORTELIVE_API_BASE` | `https://api.github.com` | Troca a URL base da API de releases (ex.: `http://127.0.0.1:18900` em servidor HTTP local de teste). |
| `CORTELIVE_RELEASE_API_URL` | *(vazio)* | Alternativa: URL base ou URL completa terminando em `/releases/latest`. |
| `CORTELIVE_REPO` | `$REPO_GITHUB` de `versoes.ps1` | Sobrescreve `<dono>/cortelive` (ex.: `teste/cortelive`). |
| `CORTELIVE_TIMEOUT_S` | `15` | Timeout HTTP em segundos. |
| `CORTELIVE_CACHE_TTL_S` | `900` | Tempo de cache do `-Checar` em segundos (`0` desativa o atalho por TTL). |
| `CORTELIVE_CANAL` | `estavel` | Define o canal padrão (`estavel` ou `beta`) caso `-Canal` não seja passado na linha de comando. |
| `CORTELIVE_SEM_PAUSA` | *(vazio)* | Quando definido (`1`), impede o `pause` final em `atualizar.cmd` sem argumentos. |
