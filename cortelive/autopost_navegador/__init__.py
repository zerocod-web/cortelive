"""Postagem pelo NAVEGADOR, com a sessão logada do próprio usuário (sem API, sem senha guardada).

  python -m cortelive.autopost_navegador conectar youtube|instagram|tiktok
  python -m cortelive.autopost_navegador status [--json]
  python -m cortelive.autopost_navegador enviar --video ARQ --titulo T --descricao D \
        --redes youtube,instagram,tiktok [--privado] [--dry-run] [--ate-antes-de-publicar]

Cada rede tem o seu perfil de navegador em %LOCALAPPDATA%\\CorteLive\\navegador\\<rede>, fora do
repositório. O usuário entra na conta UMA vez, à mão, pela janela que o `conectar` abre; o
CorteLive não vê nem guarda senha. Saída de `enviar`: uma linha JSON por evento,
{rede, etapa, pct, link|erro}, o mesmo contrato de `python -m cortelive.postar enviar`.
"""
import os
from pathlib import Path

REDES = ("youtube", "instagram", "tiktok")


def pasta_base() -> Path:
    raiz = os.environ.get("CORTELIVE_NAVEGADOR_DIR") or os.path.join(
        os.environ.get("LOCALAPPDATA") or str(Path.home()), "CorteLive", "navegador")
    p = Path(raiz)
    p.mkdir(parents=True, exist_ok=True)
    return p


def pasta_perfil(rede: str) -> Path:
    p = pasta_base() / rede
    p.mkdir(parents=True, exist_ok=True)
    return p
