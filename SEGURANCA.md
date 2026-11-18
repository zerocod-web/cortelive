# Seguranca — CorteLive

## Onde ficam chaves e credenciais

- **Nunca no repositorio:** chaves de API, credenciais de conta, sessoes de navegador e arquivos de configuracao pessoal jamais podem entrar no codigo, em presets, em exemplos ou no historico do Git.
- **Uso local:** qualquer credencial do usuario fica **somente no computador dele** (por exemplo em `%LOCALAPPDATA%\cortelive` ou em pastas locais ignoradas pelo `.gitignore`), fora do controle de versao.
- **Automacao no GitHub:** se algum fluxo de CI precisar de credencial, ela deve ficar exclusivamente nos **GitHub Actions Secrets** da conta/repositorio.

## Camadas de protecao ativas

1. `.gitignore` bloqueia ambientes locais, configuracoes pessoais, chaves, sessoes e pastas de contas.
2. Hook local `.githooks/pre-commit` roda o **Gitleaks** antes de cada commit (`git config core.hooksPath .githooks`) e barra qualquer tentativa de commitar segredos.
3. Workflow `.github/workflows/segredos.yml` roda o **Gitleaks** em todo `push` e `pull_request` no historico inteiro.
4. **Secret scanning** e **Push protection** habilitados no repositorio do GitHub.

## O que fazer se algum segredo vazar

1. **Revogue ou troque a chave na hora** diretamente no servico de origem. Apagar o commit nao invalida uma credencial que ja foi exposta.
2. Se for sessao de conta, encerre todas as sessoes conectadas imediatamente.
3. Remova o dado sensivel do historico do Git antes de novos envios e avise o responsavel pelo repositorio.
