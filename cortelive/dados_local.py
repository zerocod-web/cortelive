"""Pastas de dados do CorteLive fora do repositório (%LOCALAPPDATA%/CorteLive)."""
from __future__ import annotations

import os
from pathlib import Path

RAIZ_REPO = Path(__file__).resolve().parents[1]


def garantir_fora_do_repo(caminho: Path, raiz_repo: Path | None = None) -> None:
    """Impede que dados locais (contas, conexões) sejam gravados dentro do repositório."""
    raiz = (raiz_repo or RAIZ_REPO).resolve()
    alvo = caminho.resolve()
    try:
        alvo.relative_to(raiz)
    except ValueError:
        return
    raise ValueError(
        f"Caminho inseguro '{alvo}': dados locais devem ficar fora da pasta do projeto '{raiz}'."
    )


def pasta_dados_local() -> Path:
    """Retorna a pasta %LOCALAPPDATA%/CorteLive (sempre fora do repositório)."""
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
    """Retorna %LOCALAPPDATA%/CorteLive/contas validando que fica fora do repositório."""
    override = os.environ.get("CORTELIVE_CONTAS_DIR", "").strip()
    if override:
        alvo = Path(override).expanduser().resolve()
    else:
        alvo = (pasta_dados_local() / "contas").resolve()
    garantir_fora_do_repo(alvo)
    alvo.mkdir(parents=True, exist_ok=True)
    return alvo
