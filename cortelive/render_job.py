"""Roda um render_corte num processo separado (o principal pode cancelar se travar, e um crash não derruba a live).

Uso interno: python -m cortelive.render_job pedido.json  -> escreve pedido.json.res
v1.0.17: com job["original"] (caminho .mkv), grava também o original do corte (cortelive.original), no mesmo
processo e na mesma vaga da fila de render. Falha no original não perde o corte (vai em out["original_erro"]).
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
                                job["nome"], faixa=job.get("faixa_legenda"), faixa_cor=job.get("faixa_cor"),
                                recuo_sem_cam=job.get("recuo_sem_cam"), conferir_cam=bool(job.get("conferir_cam")),
                                modo=job.get("modo"), foco=job.get("foco") or None)
        out = {"ok": True, **r}
        if job.get("original"):
            try:
                from . import original
                out["original"] = original.guardar(job["video"], job["inicio"], job["fim"], job["original"])
            except Exception as e:
                out["original"] = None
                out["original_erro"] = str(e)[-600:]
    except BaseException as e:
        out = {"ok": False, "erro": str(e)[-1500:], "trace": traceback.format_exc()[-3000:]}
    p.with_suffix(".res").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
