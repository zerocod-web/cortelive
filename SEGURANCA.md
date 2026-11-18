# Segurança — CorteLive

## Onde ficam chaves e credenciais

- **Nunca no repositório:** chaves de API, credenciais de conta, sessões de navegador e arquivos de configuração pessoal jamais podem entrar no código, em presets, em exemplos ou no histórico do Git.
- **Uso local:** qualquer credencial do usuário fica **somente no computador dele** (por exemplo em `%LOCALAPPDATA%\cortelive` ou em pastas locais ignoradas pelo `.gitignore`), fora do controle de versão.
- **Automação no GitHub:** se algum fluxo de CI precisar de credencial, ela deve ficar exclusivamente nos **GitHub Actions Secrets** da conta/repositório.

## Camadas de proteção ativas

1. `.gitignore` bloqueia ambientes locais, configurações pessoais, chaves, sessões e pastas de contas.
2. Hook local `.githooks/pre-commit` roda o **Gitleaks** antes de cada commit (`git config core.hooksPath .githooks`) e barra qualquer tentativa de commitar segredos.
3. Workflow `.github/workflows/segredos.yml` roda o **Gitleaks** em todo `push` e `pull_request` no histórico inteiro.
4. **Secret scanning** e **Push protection** habilitados no repositório do GitHub.

## O que fazer se algum segredo vazar

1. **Revogue ou troque a chave na hora** diretamente no serviço de origem. Apagar o commit não invalida uma credencial que já foi exposta.
2. Se for sessão de conta, encerre todas as sessões conectadas imediatamente.
3. Remova o dado sensível do histórico do Git antes de novos envios e avise o responsável pelo repositório.
