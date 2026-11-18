"""Fila do autopost em disco (o painel e a CLI leem o mesmo arquivo).

<pasta_saida>/_autopost/fila.json      itens (um por corte)
<pasta_saida>/_autopost/postados.json  registro "hash do arquivo + rede" -> link (evita post duplicado)
<pasta da sessão>/_interno/<corte>.autopost.jsonl  log de cada etapa daquele corte (ver pastas.py)

Estados de um item: aguardando_aprovacao | na_fila | postando | postado | parcial | erro | rejeitado
Estados por rede:   pendente | enviando | postado | duplicado | erro
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path

ESTADOS = ("aguardando_aprovacao", "na_fila", "postando", "postado", "parcial", "erro", "rejeitado", "expirado")


def hash_arquivo(caminho) -> str:
    h = hashlib.sha256()
    with open(caminho, "rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


class Fila:
    def __init__(self, pasta):
        self.dir = Path(pasta) / "_autopost"
        self.dir.mkdir(parents=True, exist_ok=True)
        from ..pastas import ocultar
        ocultar(self.dir)              # quem abre a pasta de cortes vê só os vídeos
        self.arq = self.dir / "fila.json"
        self.reg = self.dir / "postados.json"
        self.trava = self.dir / ".trava"

    # ---------------- trava entre processos (sessão, CLI do painel) ----------------
    @contextlib.contextmanager
    def _travado(self, espera=15.0):
        t0 = time.time()
        while True:
            try:
                fd = os.open(self.trava, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                break
            except FileExistsError:
                try:   # trava esquecida por processo que morreu
                    if time.time() - self.trava.stat().st_mtime > 30:
                        self.trava.unlink(missing_ok=True)
                        continue
                except OSError:
                    pass
                if time.time() - t0 > espera:
                    raise TimeoutError("fila do autopost ocupada por outro processo")
                time.sleep(0.05)
        try:
            yield
        finally:
            self.trava.unlink(missing_ok=True)

    @staticmethod
    def _ler(arq, padrao):
        try:
            return json.loads(arq.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return padrao

    @staticmethod
    def _gravar(arq, dados):
        tmp = arq.with_suffix(".tmp")
        tmp.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")
        for i in range(20):
            try:
                os.replace(tmp, arq)
                return
            except PermissionError:      # Windows: alguém lendo no mesmo instante
                time.sleep(0.05 * (i + 1))
        os.replace(tmp, arq)

    # ---------------- itens ----------------
    def itens(self, estado=None) -> list[dict]:
        d = self._ler(self.arq, {"itens": {}})["itens"]
        lst = sorted(d.values(), key=lambda x: x.get("criado", 0))
        return [x for x in lst if not estado or x.get("estado") == estado]

    @contextlib.contextmanager
    def reservar_processamento(self):
        """Só um processo publica por pasta de cortes. O SO libera a trava se ele morrer."""
        with contextlib.closing(sqlite3.connect(self.dir / "processamento.sqlite3", timeout=0)) as db:
            try:
                db.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as erro:
                if erro.sqlite_errorcode not in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                    raise
                yield False
                return
            try:
                yield True
            finally:
                db.rollback()

    def pegar(self, id_) -> dict | None:
        d = self._ler(self.arq, {"itens": {}})["itens"]
        if id_ in d:
            return d[id_]
        achados = [v for k, v in d.items() if k.startswith(id_)]
        return achados[0] if len(achados) == 1 else None

    def criar(self, item: dict) -> tuple[dict, bool]:
        """-> (item, novo?). Mesmo arquivo (mesmo hash) não entra duas vezes."""
        with self._travado():
            d = self._ler(self.arq, {"itens": {}})
            if item["id"] in d["itens"]:
                return d["itens"][item["id"]], False
            item.setdefault("criado", time.time())
            d["itens"][item["id"]] = item
            self._gravar(self.arq, d)
            return item, True

    def atualizar(self, id_, **campos) -> dict:
        with self._travado():
            d = self._ler(self.arq, {"itens": {}})
            it = d["itens"].get(id_)
            if it is None:
                raise KeyError(id_)
            for k, v in campos.items():
                if k == "redes":
                    it.setdefault("redes", {})
                    for rede, st in v.items():
                        it["redes"].setdefault(rede, {}).update(st)
                else:
                    it[k] = v
            it["atualizado"] = time.time()
            self._gravar(self.arq, d)
            return it

    def trocar_caminhos(self, mapa: dict) -> int:
        """Migrador de pastas: aponta itens e registro para o caminho novo do MP4 (o hash não muda)."""
        n = 0
        with self._travado():
            d = self._ler(self.arq, {"itens": {}})
            for it in d["itens"].values():
                novo = mapa.get(str(Path(it.get("video", "")).resolve()))
                if novo:
                    it["video"], it["corte"], n = novo, Path(novo).stem, n + 1
            self._gravar(self.arq, d)
            r = self._ler(self.reg, {})
            for v in r.values():
                novo = mapa.get(str(Path(v.get("video", "")).resolve()))
                if novo:
                    v["video"] = novo
            self._gravar(self.reg, r)
        return n

    # ---------------- registro de postados (anti-duplicado) ----------------
    @staticmethod
    def _chave(sha, rede, dry):
        return f"{sha}:{rede}" + (":dry" if dry else "")

    def ja_postado(self, sha, rede, dry=False) -> dict | None:
        return self._ler(self.reg, {}).get(self._chave(sha, rede, dry))

    def ultimo_por_canal(self, rede, canal):
        sufixo = f":{rede}:{canal}"
        return max((item["quando"] for chave, item in self._ler(self.reg, {}).items()
                    if chave.endswith(sufixo)), default=0)

    def registrar_postado(self, sha, rede, link, video, dry=False):
        with self._travado():
            r = self._ler(self.reg, {})
            r[self._chave(sha, rede, dry)] = {"link": link, "video": str(video), "quando": time.time(),
                                              "dry_run": bool(dry)}
            self._gravar(self.reg, r)


def log_corte(video, evento: dict):
    """Uma linha JSON por etapa: <sessão>/_interno/<corte>.autopost.jsonl (formato antigo: ao lado do MP4)."""
    from ..pastas import log_autopost
    arq = log_autopost(video)
    with open(arq, "a", encoding="utf-8") as f:
        f.write(json.dumps({"t": round(time.time(), 2), **evento}, ensure_ascii=False) + "\n")
