"""cortelive — acompanha uma live e solta cortes verticais legendados enquanto ela acontece."""
from pathlib import Path as _Path

_arq_versao = _Path(__file__).resolve().parent.parent / "VERSION"
__version__ = _arq_versao.read_text(encoding="utf-8-sig").strip() if _arq_versao.exists() else "0.1.0"
