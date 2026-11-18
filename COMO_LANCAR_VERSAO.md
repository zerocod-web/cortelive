# Como lançar uma versão nova para os seus amigos

Você **não precisa programar** nem montar nenhum arquivo `.zip` na mão. Quando você lança uma versão nova, o próprio GitHub monta o pacote, cria o selo de segurança e avisa o painel dos seus amigos automaticamente (sem apagar os modelos nem os cortes que eles já têm no PC).

---

## Jeito fácil (recomendado): dois cliques em `lancar_versao.cmd`

1. Abra a pasta do CorteLive no seu PC e dê **dois cliques em `lancar_versao.cmd`**.
2. Ele vai mostrar qual é a versão atual (por exemplo, `0.1.0`) e perguntar:
   - **Qual é o número da nova versão?**
     Você pode só apertar **Enter** para aceitar a sugestão automática (ex.: `0.1.1`) ou digitar outro número (ex.: `0.2.0`).
   - **O que mudou nesta versão?**
     Escreva uma frase curta para seus amigos saberem a novidade (ex.: `Melhorei a legenda`) ou só aperte **Enter**.
3. Antes de enviar qualquer coisa para a internet, ele mostra um resumo na tela e pergunta:
   - **`Publicar esta versão no GitHub agora? (S/N)`**
   - Digite **`S`** e aperte **Enter**.
4. **Pronto!** O GitHub recebe a etiqueta da versão nova, monta o arquivo `cortelive_amigos.zip` em cerca de 1 a 2 minutos e libera a atualização para todo mundo.

---

## Como escolher o número da versão?

Use sempre três números separados por ponto (sem letras):
- Mudança pequena ou conserto rápido: aumente o **último** número (`0.1.0` → `0.1.1` → `0.1.2`).
- Novidade visível ou opção nova no painel: aumente o número **do meio** e zere o último (`0.1.2` → `0.2.0`).
- Mudança gigante no programa todo: aumente o **primeiro** número (`0.2.0` → `1.0.0`).

---

## Jeito manual (se um dia quiser fazer sem o atalho)

Toda a regra do lançamento é apenas: **o número dentro do arquivo `VERSION` precisa ser igual à etiqueta `v...` criada no GitHub**.

1. Abra o arquivo `VERSION` no Bloco de Notas, troque o número (por exemplo, de `0.1.0` para `0.1.1`) e salve.
2. Envie a alteração para o GitHub e crie uma etiqueta (*tag* / *Release*) com `v` na frente do mesmo número (`v0.1.1`).
3. O robô do GitHub confere se `v0.1.1` bate com o `VERSION`, gera o `cortelive_amigos.zip` + `.sha256` e publica sozinho na aba **Releases**.

---

## Como saber se já ficou no ar?

1. Abra a página do seu repositório no GitHub.
2. Clique na aba **Actions** no topo:
   - Bolinha amarela girando = ainda está montando o pacote (leva ~1 a 2 minutos).
   - Certinho verde = **já está publicado!** Seus amigos já recebem o aviso de atualização ao abrir o painel.
   - X vermelho = algo deu errado (por exemplo, a etiqueta não batia com o arquivo `VERSION`). Nesse caso, os seus amigos continuam na versão anterior normalmente, sem quebrar nada. É só rodar o `lancar_versao.cmd` com o próximo número (ex.: `0.1.2`).

## Quem está na 1.0.4 ou 1.0.5 e o botão Atualizar falha com "arquivo Cookies em uso"

O botão Atualizar roda o `instalador\atualizar.ps1` **já instalado** na máquina. Na 1.0.4 e na 1.0.5 esse
script não protegia a pasta `webview_app` (perfil do Edge do painel), tentava copiá-la no backup e o Edge aberto
travava o arquivo `Network\Cookies`. Isso não tem como ser consertado de fora: o script antigo roda antes de
qualquer arquivo novo. A correção vale a partir da 1.0.6 (a pasta `webview_*` nunca entra em backup nem em troca).

Para quem está preso: fechar o CorteLive (botão direito no ícone ao lado do relógio, Sair) e rodar o
`CorteLive-Setup.exe` da versão nova por cima, na mesma pasta. O Setup preserva `webview_app`, modelos, cortes,
configurações e histórico. Depois disso o botão Atualizar funciona normalmente.
