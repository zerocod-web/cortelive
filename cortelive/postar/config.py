"""Carregamento de configuracao do modulo cortelive.postar (sem segredos de TikTok/Meta no PC)."""
from __future__ import annotations

import copy
import os
import tomllib
from pathlib import Path
from typing import Any

AQUI = Path(__file__).resolve().parent
RAIZ_REPO = AQUI.parents[1]
REDES_SUPORTADAS = ("youtube", "instagram", "tiktok")
PROVEDORES_VIA = ("direto", "postiz")
CHAVES_COFRE_VALIDAS = (*REDES_SUPORTADAS, "postiz")

PLACEHOLDERS_PREFIXOS = (
    "SEU_CLIENT_",
    "SEU_APP_",
    "SEU-WORKER",
    "HTTPS://SEU-WORKER",
    "COLE_AQUI",
    "SUA_API_KEY",
    "SUA_CHAVE",
    "EXEMPLO_",
    "",
)

# Campos de segredo confidencial que NUNCA podem existir na config local de TikTok ou Meta
_CAMPOS_SEGREDO_PROIBIDOS_PC = ("client_secret", "app_secret")


def pasta_dados_local() -> Path:
    """Retorna a pasta %LOCALAPPDATA%\\CorteLive (sempre fora do repositorio)."""
    override = os.environ.get("CORTELIVE_DADOS_DIR", "").strip()
    if override:
        base = Path(override).expanduser().resolve()
    else:
        local_app = os.environ.get("LOCALAPPDATA", "").strip()
        if local_app:
            base = Path(local_app).resolve() / "CorteLive"
        else:
            base = (Path.home() / "AppData" / "Local" / "CorteLive").resolve()
    garantir_fora_do_repo(base)
    return base


def pasta_contas() -> Path:
    """Retorna %LOCALAPPDATA%\\CorteLive\\contas\\ validando que fica fora do repo."""
    override = os.environ.get("CORTELIVE_CONTAS_DIR", "").strip()
    if override:
        alvo = Path(override).expanduser().resolve()
    else:
        alvo = (pasta_dados_local() / "contas").resolve()
    garantir_fora_do_repo(alvo)
    alvo.mkdir(parents=True, exist_ok=True)
    return alvo


def garantir_fora_do_repo(caminho: Path, raiz_repo: Path | None = None) -> None:
    """Impede que tokens ou segredos de conta sejam gravados dentro do repositorio."""
    raiz = (raiz_repo or RAIZ_REPO).resolve()
    alvo = caminho.resolve()
    try:
        alvo.relative_to(raiz)
    except ValueError:
        return
    raise ValueError(
        f"Caminho inseguro '{alvo}': dados sensiveis de contas devem ficar fora da pasta do projeto '{raiz}'."
    )


def _mesclar_dict(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    saida = copy.deepcopy(base)
    for chave, valor in extra.items():
        if isinstance(valor, dict) and isinstance(saida.get(chave), dict):
            saida[chave] = _mesclar_dict(saida[chave], valor)
        else:
            saida[chave] = copy.deepcopy(valor)
    return saida


def validar_sem_segredo_tiktok_meta(cfg: dict[str, Any]) -> None:
    """Recusa qualquer tentativa de colocar segredo de TikTok ou Meta na config do PC."""
    for rede in ("instagram", "tiktok"):
        bloco = cfg.get(rede)
        if not isinstance(bloco, dict):
            continue
        for campo in _CAMPOS_SEGREDO_PROIBIDOS_PC:
            valor = str(bloco.get(campo) or "").strip()
            if valor and not _eh_placeholder(valor):
                raise ValueError(
                    f"Por seguranca, '{campo}' de '{rede}' nao pode ficar na configuracao do PC. "
                    f"Guarde os segredos de Meta/TikTok somente nas variaveis do Cloudflare Worker "
                    f"(cortelive\\postar\\servidor_tokens) e configure apenas [servidor_tokens].url."
                )
            bloco.pop(campo, None)


def carregar_config_postar(caminho_extra: Path | str | None = None) -> dict[str, Any]:
    """Carrega config padrao do exemplo e sobrepoe com arquivos locais se existirem."""
    exemplo = AQUI / "config.exemplo.toml"
    cfg = tomllib.loads(exemplo.read_text(encoding="utf-8-sig"))

    candidatos: list[Path] = []
    try:
        candidatos.append(pasta_dados_local() / "postar.toml")
    except ValueError:
        pass
    candidatos.append(AQUI / "config.toml")
    candidatos.append(Path.cwd() / "postar.toml")

    env_cfg = os.environ.get("CORTELIVE_POSTAR_CONFIG", "").strip()
    if env_cfg:
        candidatos.append(Path(env_cfg).expanduser())
    if caminho_extra:
        candidatos.append(Path(caminho_extra).expanduser())

    vistos: set[Path] = set()
    for arq in candidatos:
        try:
            resolvido = arq.resolve()
        except OSError:
            continue
        if resolvido in vistos or not resolvido.is_file():
            continue
        vistos.add(resolvido)
        dados = tomllib.loads(resolvido.read_text(encoding="utf-8-sig"))
        cfg = _mesclar_dict(cfg, dados)

    # Normaliza apenas IDs publicos (client_key <-> client_id no TikTok; app_id no Instagram)
    tk = cfg.setdefault("tiktok", {})
    if not tk.get("client_key") and tk.get("client_id"):
        tk["client_key"] = tk["client_id"]
    if not tk.get("client_id") and tk.get("client_key"):
        tk["client_id"] = tk["client_key"]

    ig = cfg.setdefault("instagram", {})
    if not ig.get("client_id") and ig.get("app_id"):
        ig["client_id"] = ig["app_id"]

    st = cfg.setdefault("servidor_tokens", {})
    env_worker = os.environ.get("CORTELIVE_SERVIDOR_TOKENS_URL", "").strip()
    if env_worker:
        st["url"] = env_worker
    elif not st.get("url") and cfg.get("servidor_tokens_url"):
        st["url"] = str(cfg["servidor_tokens_url"]).strip()

    prov = str(cfg.get("provedor_padrao") or "direto").strip().lower()
    cfg["provedor_padrao"] = prov if prov in PROVEDORES_VIA else "direto"

    validar_sem_segredo_tiktok_meta(cfg)
    return cfg


def _eh_placeholder(valor: Any) -> bool:
    if not isinstance(valor, str):
        return True
    limpo = valor.strip()
    if not limpo:
        return True
    maiusc = limpo.upper()
    if "SEU-WORKER" in maiusc or "SEU_SUBDOMINIO" in maiusc:
        return True
    return any(maiusc.startswith(p) for p in PLACEHOLDERS_PREFIXOS if p)


def resolver_provedor_via(via: str | None, cfg: dict[str, Any]) -> str:
    """Resolve o provedor ('direto' por padrao ou 'postiz')."""
    escolhido = str(via or cfg.get("provedor_padrao") or "direto").strip().lower()
    if escolhido not in PROVEDORES_VIA:
        raise ValueError(f"Provedor --via invalido '{escolhido}'. Opcoes: {', '.join(PROVEDORES_VIA)}.")
    return escolhido


def client_id_rede(cfg: dict[str, Any], rede: str) -> str:
    """Extrai e valida apenas o identificador publico (client_id / client_key) da rede."""
    if rede not in REDES_SUPORTADAS:
        raise ValueError(f"Rede desconhecida '{rede}'. Escolha entre: {', '.join(REDES_SUPORTADAS)}.")
    validar_sem_segredo_tiktok_meta(cfg)
    bloco = cfg.get(rede, {}) or {}
    chave_id = "client_key" if rede == "tiktok" else "client_id"
    cid = str(bloco.get(chave_id) or bloco.get("client_id") or "").strip()
    if _eh_placeholder(cid):
        raise ValueError(
            f"App de desenvolvedor de '{rede}' ainda nao configurado. "
            f"Copie cortelive\\postar\\config.exemplo.toml para %LOCALAPPDATA%\\CorteLive\\postar.toml "
            f"e preencha [{rede}].{chave_id} (veja LEIA-ME_POSTAGEM.md)."
        )
    return cid


def segredo_desktop_youtube(cfg: dict[str, Any]) -> str:
    """Retorna o segredo opcional de Desktop App do YouTube (tratado pelo Google como nao confidencial).

    Nunca fica no repositorio; se ausente na config local (%LOCALAPPDATA%\\CorteLive\\postar.toml),
    o fluxo Desktop App do YouTube envia apenas client_id + PKCE (ou usa servidor_tokens se configurado).
    """
    env_sec = os.environ.get("CORTELIVE_YOUTUBE_DESKTOP_SECRET", "").strip()
    if env_sec and not _eh_placeholder(env_sec):
        return env_sec
    bloco = cfg.get("youtube", {}) or {}
    bruto = str(bloco.get("desktop_secret") or bloco.get("client_secret") or "").strip()
    return "" if _eh_placeholder(bruto) else bruto


def url_servidor_tokens(cfg: dict[str, Any], obrigatorio: bool = True) -> str:
    """Obtém e valida a URL do Cloudflare Worker intermediario de tokens."""
    validar_sem_segredo_tiktok_meta(cfg)
    bloco = cfg.get("servidor_tokens") or {}
    url = str(bloco.get("url") or cfg.get("servidor_tokens_url") or "").strip().rstrip("/")
    if _eh_placeholder(url):
        if not obrigatorio:
            return ""
        raise ValueError(
            "URL do servidor_tokens nao configurada. "
            "TikTok e Instagram/Meta nao guardam segredo no PC: configure [servidor_tokens].url "
            "em %LOCALAPPDATA%\\CorteLive\\postar.toml apontando para o seu Cloudflare Worker "
            "(veja cortelive\\postar\\servidor_tokens\\README.md)."
        )
    if not (url.startswith("https://") or url.startswith("http://127.0.0.1") or url.startswith("http://localhost")):
        raise ValueError(
            f"URL insegura em [servidor_tokens].url ('{url}'): use https:// (ou http://127.0.0.1 apenas em testes locais)."
        )
    return url
