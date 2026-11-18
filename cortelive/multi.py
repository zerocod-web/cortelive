"""cortelive com VÁRIAS lives ao mesmo tempo.

  cortelive <url1> <url2> ...
  cortelive --lista lives.txt          (uma URL por linha; '#' comenta; relido a cada 30 s)
  cortelive --arquivo a.mp4 --arquivo b.mp4 [url ...]

Cada live é uma `sessao.Sessao` (CONTRATO_MULTI.md): pasta, log, cam, reconexão e watchdogs próprios;
transcrição (um modelo na GPU) e fila de render são compartilhadas via `servicos`. Este módulo só
orquestra: lê a lista, supervisiona (reinicia só a sessão que deu erro ou travou), desenha o painel
e, no Ctrl+C, para todas e mostra o resumo por live.

Teste de robustez: criar o arquivo `DERRUBAR_<canal>` na pasta atual derruba aquela sessão
(estado "erro"), para provar que as outras seguem e que o supervisor a reinicia.
"""
import argparse
import collections
import os
import sys
import threading
import time
import types
from pathlib import Path

RELER_LISTA_S = 30
PAINEL_S = 2
TRAVOU_S = 300          # "ao vivo" sem a live avançar por esse tempo = sessão travada → reinicia
ESPERA_REINICIO = (5, 15, 30, 60, 120)
FECHAR_S = 240          # Ctrl+C: tempo para todas terminarem os cortes em andamento

_REPASSA_VALOR = ("--modelo", "--legenda", "--saida", "--duracao-min", "--duracao-max", "--minutos",
                  "--gpu", "--encoder", "--fala", "--modelo-whisper")
_REPASSA_FLAG = ("--sem-chat", "--detalhado")


def ler_lista(caminho):
    """URLs do arquivo: ignora vazias, espaços nas pontas e comentários ('#' no começo ou no meio)."""
    try:
        txt = Path(caminho).read_text(encoding="utf-8-sig")
    except OSError:
        return None
    urls = []
    for linha in txt.splitlines():
        u = linha.split("#", 1)[0].strip()
        if u and u not in urls:
            urls.append(u)
    return urls


def nome_canal(fonte, arquivo):
    if arquivo:
        return Path(fonte).stem
    from .captura import canal_da_url
    return canal_da_url(fonte)


def _hms(s):
    s = int(max(0, s))
    return f"{s // 3600:02d}:{s // 60 % 60:02d}:{s % 60:02d}"


def criar_sessao(fonte, arquivo, canal, a):
    """Fábrica (os testes trocam por uma sessão simulada)."""
    from .sessao import Sessao
    args = types.SimpleNamespace(
        url=None if arquivo else fonte, arquivo=fonte if arquivo else None, canal=canal, cam=None,
        minutos=a.minutos, detalhado=a.detalhado, duracao_min=a.duracao_min, duracao_max=a.duracao_max,
        modelo=a.modelo, legenda=a.legenda, saida=a.saida, sem_chat=a.sem_chat)
    return Sessao(args=args, terminal=False)


class Live:
    """Uma entrada (URL ou arquivo) e as execuções da sessão dela (uma nova a cada reinício)."""

    def __init__(self, fonte, arquivo, canal, a):
        self.fonte, self.arquivo, self.canal, self.a = fonte, arquivo, canal, a
        self.s = None
        self.reinicios = 0
        self.cortes_antes = 0
        self.resumos, self.pastas = [], []
        self.encerrar = False          # tirada da lista / Ctrl+C: não reiniciar
        self.terminou = False
        self.proximo_inicio = None
        self.nota = ""
        self.derrubada = False
        self._fechada = None
        self._live_s, self._t_avanco = -1.0, time.time()

    def iniciar(self):
        self.s = criar_sessao(self.fonte, self.arquivo, self.canal, self.a)
        self.s.iniciar()
        self.proximo_inicio, self.nota, self.derrubada = None, "", False
        self._live_s, self._t_avanco = -1.0, time.time()

    def st(self):
        try:
            return self.s.status() if self.s else {}
        except Exception as e:
            return {"estado": "erro", "motivo_fim": str(e)}

    def cortes(self):
        aberta = self.s is not None and self.s is not self._fechada
        return self.cortes_antes + ((self.st().get("cortes") or 0) if aberta else 0)

    def derrubar(self, motivo="derrubada de propósito (teste)"):
        self.derrubada = True      # a sessão sai "encerrada" quando pedimos parar; vale como queda
        self.s.erro = RuntimeError(motivo)
        self.s._estado_forcado = "erro"
        self.s.parar()

    def fechar_execucao(self):
        if self.s is None or self.s is self._fechada:
            return                     # já contada
        self._fechada = self.s
        st = self.st()
        self.cortes_antes += st.get("cortes") or 0
        if st.get("pasta") and st["pasta"] not in self.pastas:
            self.pastas.append(st["pasta"])
        if getattr(self.s, "resumo", None):
            self.resumos.append(self.s.resumo)


class Supervisor:
    def __init__(self, urls, arquivos, lista, a, painel=True, travou_s=TRAVOU_S):
        self.a, self.lista, self.painel, self.travou_s = a, lista, painel, travou_s
        self.lives = {}
        self.t0 = time.time()
        self.eventos = collections.deque(maxlen=8)
        self.velhas = []               # sessões abandonadas (travadas) ainda terminando
        self._lista_ultima = None
        for f in arquivos:
            self.adicionar(f, arquivo=True)
        for u in urls:
            self.adicionar(u)
        if lista:
            self.reler_lista(primeira=True)

    def evento(self, txt):
        self.eventos.append(f"{time.strftime('%H:%M:%S')} {txt}")
        if not self.painel:
            print(self.eventos[-1], flush=True)

    def adicionar(self, fonte, arquivo=False):
        lv = self.lives.get(fonte)
        if lv and not lv.encerrar:
            return
        canal = nome_canal(fonte, arquivo)
        usados = {l.canal for l in self.lives.values() if not l.encerrar}
        base, n = canal, 2
        while canal in usados:                 # mesmo canal duas vezes: pasta/cam são por canal
            canal, n = f"{base}_{n}", n + 1
        lv = Live(fonte, arquivo, canal, self.a)
        self.lives[fonte] = lv
        lv.iniciar()
        self.evento(f"+ {canal} adicionada")

    def remover(self, fonte):
        lv = self.lives.get(fonte)
        if lv and not lv.encerrar:
            lv.encerrar = True
            if lv.s:
                lv.s.parar()
            self.evento(f"- {lv.canal} saiu da lista (encerrando)")

    def reler_lista(self, primeira=False):
        urls = ler_lista(self.lista)
        if urls is None:
            if primeira:
                self.evento(f"! lista {self.lista} não encontrada")
            return
        if urls == self._lista_ultima:
            return
        self._lista_ultima = urls
        da_lista = {k for k, l in self.lives.items() if not l.arquivo and not l.encerrar}
        for u in urls:
            self.adicionar(u)
        for u in da_lista - set(urls):
            self.remover(u)

    def vigiar(self):
        agora = time.time()
        for lv in list(self.lives.values()):
            if lv.terminou:
                continue
            if Path(f"DERRUBAR_{lv.canal}").exists():
                Path(f"DERRUBAR_{lv.canal}").unlink(missing_ok=True)
                if lv.s and not lv.s._fim_ev.is_set():
                    lv.derrubar()
                    self.evento(f"! {lv.canal}: derrubada de propósito (teste)")
            if lv.proximo_inicio is not None:          # esperando para reiniciar
                if lv.encerrar:
                    lv.terminou = True
                elif agora >= lv.proximo_inicio:
                    lv.reinicios += 1
                    lv.iniciar()
                    self.evento(f"~ {lv.canal} reiniciada (#{lv.reinicios})")
                continue
            st = lv.st()
            estado = st.get("estado")
            fim = lv.s._fim_ev.is_set() if lv.s else True
            if not fim:
                live_s = st.get("live_s") or 0
                if live_s > lv._live_s:
                    lv._live_s, lv._t_avanco = live_s, agora
                elif estado == "ao vivo" and agora - lv._t_avanco > self.travou_s and not lv.encerrar:
                    self.evento(f"! {lv.canal} travada ({self.travou_s} s sem avançar): reiniciando só ela")
                    lv.s.parar()
                    self.velhas.append(lv.s)
                    lv.fechar_execucao()
                    lv.proximo_inicio = agora + ESPERA_REINICIO[0]
                    lv.nota = "travou"
                continue
            lv.fechar_execucao()
            if lv.encerrar:
                lv.terminou = True
            elif estado == "erro" or lv.derrubada:
                espera = ESPERA_REINICIO[min(lv.reinicios, len(ESPERA_REINICIO) - 1)]
                lv.proximo_inicio, lv.nota = agora + espera, f"volta em {espera}s"
                self.evento(f"! {lv.canal} caiu ({(st.get('motivo_fim') or '')[:60]}); reinicio em {espera}s")
            elif lv.arquivo or self.a.minutos:
                lv.terminou = True
                self.evento(f"= {lv.canal} terminou ({lv.cortes()} cortes)")
            else:                                       # live acabou: volta a esperar a próxima
                lv.proximo_inicio, lv.nota = agora + 60, "esperando a próxima live"
                self.evento(f"= {lv.canal} encerrou a live; volto a esperar em 60 s")

    def linhas_painel(self):
        dur = time.time() - self.t0
        ativas = [l for l in self.lives.values() if not l.terminou]
        L = [f"cortelive — {len(ativas)} live(s) ativas — {_hms(dur)}"
             + (f" — lista: {self.lista}" if self.lista else "") + " — Ctrl+C para parar"]
        L.append(self._linha_servicos())
        L.append(f"{'CANAL':<20}{'STATUS':<26}{'CORTES':>7}{'ATRASO':>9}{'LIVE':>10}{'REINÍC.':>9}")
        for lv in self.lives.values():
            st = lv.st() if lv.proximo_inicio is None else {}
            if lv.terminou:
                status = "encerrada" if not lv.encerrar else "parada"
            elif lv.proximo_inicio is not None:
                status = f"caiu ({lv.nota})"
            else:
                status = {"esperando live": "offline (esperando)", "encerrada": "encerrando"}.get(
                    st.get("estado"), st.get("estado") or "?")
            at = st.get("atraso_transcricao_s")
            L.append(f"{lv.canal[:19]:<20}{status[:25]:<26}{lv.cortes():>7}"
                     f"{(f'{at:.0f} s' if at is not None else '—'):>9}{_hms(st.get('live_s') or 0):>10}"
                     f"{lv.reinicios:>9}")
        if self.eventos:
            L.append("")
            L += list(self.eventos)[-5:]
        return L

    def _linha_servicos(self):
        try:
            from . import servicos
            m = servicos.modo().resumo()
            t = servicos.transcricao().status()
            r = servicos.render().status()
            return (f"placa: {m} | transcrição: {t.get('modelo')} fila {t.get('fila')} "
                    f"{(t.get('fator_tempo_real') or 0) * 100:.0f}% do tempo real | "
                    f"render: {r.get('rodando')}/{r.get('limite')} (+{r.get('esperando')} na fila)")
        except Exception as e:
            return f"placa: ? ({str(e)[:60]})"

    def desenhar(self):
        if self.painel:
            sys.stdout.write("\x1b[H\x1b[J" + "\n".join(self.linhas_painel()) + "\n")
            sys.stdout.flush()

    def rodar(self):
        if self.painel and os.name == "nt":
            os.system("")      # liga os códigos ANSI no CMD
        ult_lista = time.time()
        try:
            while True:
                self.vigiar()
                if self.lista and time.time() - ult_lista >= RELER_LISTA_S:
                    self.reler_lista()
                    ult_lista = time.time()
                self.desenhar()
                if (not self.lista or self.a.minutos) and all(l.terminou for l in self.lives.values()):
                    break
                time.sleep(PAINEL_S)
        except KeyboardInterrupt:
            pass
        return self.fechar()

    def fechar(self):
        print("\nParando todas as lives… terminando os cortes em andamento.", flush=True)
        for lv in self.lives.values():
            lv.encerrar = True
            if lv.s:
                lv.s.parar()
        limite = time.time() + FECHAR_S
        try:
            for lv in self.lives.values():
                if lv.s:
                    lv.s.aguardar(max(0.1, limite - time.time()))
        except KeyboardInterrupt:
            pass        # segundo Ctrl+C: não espera mais
        for lv in self.lives.values():
            if lv.s and lv.proximo_inicio is None and not lv.terminou:
                lv.fechar_execucao()
            lv.terminou = True
        try:
            from . import servicos
            servicos.desligar()
        except Exception:
            pass
        print("=" * 70)
        print("RESUMO POR LIVE")
        total = 0
        for lv in self.lives.values():
            n = sum(r.get("cortes", 0) for r in lv.resumos) if lv.resumos else lv.cortes_antes
            total += n
            lat = [r["latencia_momento_ate_mp4_s"]["media"] for r in lv.resumos
                   if (r.get("latencia_momento_ate_mp4_s") or {}).get("media") is not None]
            extra = f" | latência média {sum(lat) / len(lat):.0f} s" if lat else ""
            print(f"- {lv.canal}: {n} cortes | reinícios {lv.reinicios}{extra}")
            for p in lv.pastas:
                print(f"    pasta: {p}")
        print(f"Total: {total} cortes em {len(self.lives)} live(s).", flush=True)
        return 0


def _args(argv):
    p = argparse.ArgumentParser(prog="cortelive", description="Corta VÁRIAS lives ao mesmo tempo.")
    p.add_argument("urls", nargs="*", help="links das lives")
    p.add_argument("--lista", help="arquivo com uma URL por linha (relido a cada 30 s)")
    p.add_argument("--arquivo", action="append", default=[], help="vídeo simulando live (pode repetir)")
    p.add_argument("--sem-painel", action="store_true", help="só eventos, sem redesenhar a tela")
    for f in _REPASSA_VALOR:
        p.add_argument(f, type=float if f in ("--duracao-min", "--duracao-max", "--minutos") else str)
    for f in _REPASSA_FLAG:
        p.add_argument(f, action="store_true")
    return p.parse_args(argv)


def main(argv):
    a = _args(argv)
    for f in a.arquivo:
        if not Path(f).exists():
            print(f"ERRO: Arquivo não encontrado: {f}", file=sys.stderr)
            return 1
    if not (a.urls or a.arquivo or a.lista):
        print("Uso: cortelive <url1> <url2> ... | --lista lives.txt | --arquivo a.mp4 --arquivo b.mp4")
        return 2
    from . import cli, hw, servicos
    cli.checar_ambiente()
    if a.encoder:
        os.environ["CORTELIVE_ENCODER"] = a.encoder
    cfg = cli.carregar_config(a)
    servicos.configurar(cfg, hw.detectar(a.encoder, a.fala, a.modelo_whisper, None, a.gpu))
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    return Supervisor(a.urls, a.arquivo, a.lista, a, painel=not a.sem_painel).rodar()


def e_multi(argv):
    """True quando o pedido é para várias lives (o __main__ decide por aqui)."""
    if argv and argv[0] == "marcar-cam":
        return False
    if "--lista" in argv or argv.count("--arquivo") > 1:
        return True
    try:
        import contextlib
        import io
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            a = _args(argv)
    except SystemExit:
        return False
    return len(a.urls) + len(a.arquivo) > 1
