"""Roda um render_corte num processo separado (o principal pode cancelar se travar, e um crash não derruba a live).

Uso interno: python -m cortelive.render_job pedido.json  -> escreve pedido.json.res
"""
import json
import sys
import traceback
from pathlib import Path


def main():
    p = Path(sys.argv[1])
    job = json.loads(p.read_text(encoding="utf-8"))
    try:
        from . import render
        r = render.render_corte(job["video"], job["inicio"], job["fim"], job["palavras"], job["modelo"],
                                job["legenda"], tuple(job["cam"]) if job["cam"] else None, job["saida_dir"],
                                job["nome"], faixa=job.get("faixa_legenda"), faixa_cor=job.get("faixa_cor"))
        out = {"ok": True, **r}
    except BaseException as e:
        out = {"ok": False, "erro": str(e)[-1500:], "trace": traceback.format_exc()[-3000:]}
    p.with_suffix(".res").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
