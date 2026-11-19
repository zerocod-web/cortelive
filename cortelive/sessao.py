"""Uma sessão de live: captura, fala, momento, cortes. Várias podem rodar ao mesmo tempo
(ver CONTRATO_MULTI.md); transcrição e render são compartilhados via servicos.py.

Gerado a partir do antigo cli.rodar(); a lógica de cada etapa é a mesma.
"""
import concurrent.futures as cf
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import types
from pathlib import Path

from . import __version__, aprendizado, disco, hw, log, miniatura, pastas, triagem
from .cli import carregar_config, _hms, AQUI

ESTADOS = ("iniciando", "esperando live", "ao vivo", "reconectando", "encerrada", "erro")


class Sessao:
    def __init__(self, url=None, arquivo=None, canal=None, cfg=None, cam=None, minutos=None,
                 ao_status=None, terminal=False, detalhado=False, args=None):
        if args is None:
            args = types.SimpleNamespace(url=url, arquivo=arquivo, canal=canal, cam=cam, minutos=minutos,
                                         detalhado=detalhado, duracao_min=None, duracao_max=None, modelo=None,
                                         legenda=None, saida=None, sem_chat=False)
        self.args = args
        self.video_gravado = False
        self.cfg_extra = cfg or {}
        self.terminal = terminal
        self.ao_status = ao_status
        self.pasta = None
        self.resumo = None
        self.erro = None
        self._parar_ev = threading.Event()
        self._descartar_ev = threading.Event()
        self._fim_ev = threading.Event()
        self._cap = self._fl = None
        self._proc_render = None
        self._proc_lock = threading.Lock()
        self._descartados_parada = 0
        self._res, self._falhas = [], []
        self._ultimo_corte = None
        self._estado_forcado = "iniciando"
        self._th = None
        self._aviso_disco = ""        # frase de 'pouco espaço' (vazia = tudo certo)
        self._parou_disco = ""        # frase do motivo, se a gravação parou por disco quase cheio
        self._limpar_bruto = True
        self._job_atual = None        # corte em andamento: {"evento", "dur", "t0", "fase"} (para a linha de atividade)
        self._pendentes = None        # lista de cortes esperando render (mesma lista do renderizador)
        self._pronto_em = (0.0, "")   # (quando, título) do último corte pronto
        self._render_hist = []        # (duração do corte, segundos de render): estima o % do corte em andamento

    # --- API -----------------------------------------------------------------------------------
    def iniciar(self):
        self._th = threading.Thread(target=self._rodar, daemon=True, name=f"sessao-{self.args.canal or ''}")
        self._th.start()
        return self

    def parar(self, descartar_cortes=False):
        if descartar_cortes:
            self._descartar_ev.set()
            with self._proc_lock:
                pr = self._proc_render
            if pr is not None and pr.poll() is None:
                try:
                    pr.kill()
                except OSError:
                    pass
        self._parar_ev.set()
        cap = self._cap
        if cap and not cap.acabou.is_set():
            cap.solicitar_parada("parado pelo usuário")
        if self.args.canal:
            try:
                from . import servicos
                if servicos._trans is not None:
                    servicos._trans.cancelar(self.args.canal)
            except Exception:
                pass

    def aguardar(self, timeout=None):
        self._fim_ev.wait(timeout)
        return self.resumo

    def status(self):
        cap, fl = self._cap, self._fl
        estado = self._estado_forcado
        if estado not in ("encerrada", "erro") and cap is not None:
            estado = cap.estado
        return {"canal": self.args.canal, "pasta": str(self.pasta) if self.pasta else None, "estado": estado,
                "video_gravado": self.video_gravado,
                "fonte": self.args.url or (str(self.args.arquivo) if self.args.arquivo else None),
                "cortes": len(self._res), "falhas": len(self._falhas),
                "cortes_descartados_parada": self._descartados_parada,
                "live_s": round(cap.fim_live, 1) if cap else 0.0,
                "atraso_transcricao_s": round(max(0.0, cap.fim_live - fl.feito_ate), 1) if cap and fl else None,
                "ultimo_corte": self._ultimo_corte,
                "atividade": self.atividade(estado),
                # frase pronta para mostrar ao usuário (offline, sem internet, link inválido…); "" = tudo normal
                "aviso_disco": self._aviso_disco or None,
                "mensagem": (str(self.erro) if self.erro else (self._parou_disco or self._aviso_disco
                                                             or (cap.mensagem if cap else ""))),
                "motivo_fim": self._parou_disco or (self.resumo or {}).get("motivo_fim") or (str(self.erro) if self.erro else None)}

    def atividade(self, estado=None):
        """Uma frase curta do que esta live está fazendo agora (vai para o painel, perto de cada live)."""
        cap, fl = self._cap, self._fl
        if cap is None or fl is None:
            return "Preparando…"
        estado = estado or cap.estado
        if estado in ("encerrada", "erro"):
            return ""
        agora = time.time()
        job = self._job_atual
        if job:
            quando = _hms(job["evento"])
            quando = quando[3:] if quando.startswith("00:") else quando
            fase = job["fase"]
            if fase == "cortando":
                pct = self._pct_render(job, agora)
                return f"Achou um momento às {quando} — cortando ({pct}%)"
            if fase == "fila_render":
                try:
                    from . import servicos
                    st = servicos.render().status()
                    na_frente = max(0, st["rodando"] + st["esperando"] - 1) if st["rodando"] >= st["limite"] else 0
                except Exception:
                    na_frente = 0
                if na_frente:
                    return f"Achou um momento às {quando} — na fila para cortar ({na_frente} na frente)"
                return f"Achou um momento às {quando} — começando a cortar"
            if fase == "cam":
                return f"Achou um momento às {quando} — achando a câmera do streamer (só na 1ª vez)"
            return f"Achou um momento às {quando} — esperando a legenda ficar pronta"
        pend = self._pendentes
        if pend:
            return f"Na fila para cortar ({len(pend)} {'momento' if len(pend) == 1 else 'momentos'})"
        if self._pronto_em[0] and agora - self._pronto_em[0] < 45:
            return "Corte pronto: " + self._pronto_em[1]
        if estado == "reconectando":
            return "A live caiu — tentando voltar sozinho"
        if estado == "esperando live":
            return "Esperando a live começar"
        if estado != "ao vivo":
            return ""
        atraso = max(0.0, cap.fim_live - fl.feito_ate)
        return f"Ouvindo a fala (atraso {atraso:.0f} s)"

    def _pct_render(self, job, agora):
        """Estimativa do andamento do render: tempo gasto / tempo esperado (histórico desta live)."""
        decorrido = agora - job["t_render"]
        if self._render_hist:
            razao = sum(r for _, r in self._render_hist) / max(1e-6, sum(d for d, _ in self._render_hist))
        else:
            razao = 0.9
        total = max(8.0, job["dur"] * razao)
        return int(min(95, max(1, decorrido / total * 100)))

    # --- interno -------------------------------------------------------------------------------
    def _avisar(self):
        if self.ao_status:
            try:
                self.ao_status(self.status())
            except Exception:
                pass

    def _rodar(self):
        try:
            self.resumo = self._executar()
            self._estado_forcado = "encerrada"
        except BaseException as e:
            self.erro = e
            self._estado_forcado = "erro"
            try:
                log.aviso(f"A sessão parou por erro: {e}")
                import logging, traceback
                log.debug(traceback.format_exc())
            except Exception:
                pass
        finally:
            if self._cap:
                self._cap.parar.set()
                self._cap.join()
            if self.pasta and self._limpar_bruto:     # também no erro/parada: não deixa gravação bruta para trás
                try:
                    disco.limpar_bruto(self.pasta, pastas.interno(self.pasta))
                except Exception:
                    pass
            self._fim_ev.set()
            self._avisar()

    def _executar(self):
        from . import camauto, captura, destino, fala, momento, render, trecho, servicos
        args = self.args

        cfg = carregar_config(args)
        cfg.update(self.cfg_extra)
        momento.ajustar_cortes_longos(cfg)
        copias_destino = []
        for tipo, nome in (("modelo", cfg["modelo"]), ("legenda", cfg["legenda"])):
            if nome not in render.listar(tipo):
                raise RuntimeError(f"{tipo} '{nome}' não existe. Opções: {', '.join(render.listar(tipo))}")
        status_fonte = {}
        if args.url and not args.arquivo:
            url = captura.normalizar_url(args.url)
            self.video_gravado, problema = captura.classificar_link(url, status_extra=status_fonte)
            if problema:
                raise RuntimeError(problema)
        canal = args.canal or (Path(args.arquivo).stem if args.arquivo else captura.canal_da_url(args.url))
        args.canal = canal
        # pasta da sessão só com os vídeos prontos; o resto vai para _interno (oculta). Ver pastas.py.
        pasta = Path(cfg["pasta_saida"]) / pastas.nome_sessao(canal)
        pasta.mkdir(parents=True, exist_ok=True)
        dint = pastas.interno(pasta)
        self._limpar_bruto = bool(cfg.get("limpar_bruto", True))
        nivel, livre, frase = disco.avaliar(pasta, cfg)
        if nivel == "critico":                        # nem começa: o disco não aguenta gravar a live
            raise RuntimeError(frase)
        self._aviso_disco = frase
        arq_log = log.configurar(dint, args.detalhado, terminal=self.terminal)
        self.pasta = pasta
        t_sessao = time.time()

        log.info(f"cortelive {__version__} — cortes em {pasta.resolve()}")
        modo = servicos.modo()
        log.info("Modo detectado: " + modo.resumo())
        if modo.encoder == "libx264":
            log.aviso("Sem encoder de placa de vídeo: usando a CPU (mais lento).")
        log.info(f"Corte: {cfg['modelo']} | legenda: {cfg['legenda']} | {cfg['duracao_min']:.0f}-{cfg['duracao_max']:.0f} s")
        t = time.time()
        try:
            sv = servicos.transcricao()
        except Exception as e:
            raise RuntimeError(f"Não consegui carregar a transcrição ({modo.transcricao}): {e}. "
                               f"Tente: cortelive ... --fala faster-whisper-cpu")
        tr = servicos.Cliente(sv, canal, timeout=None)
        log.ok(f"Transcrição pronta ({modo.transcricao}, modelo {sv.nome_modelo}, compartilhada; {time.time() - t:.0f} s)")

        # câmera: argumento > marcada/memorizada do canal > detecção automática no começo
        cam, cam_status = None, "procurar"
        if args.cam:
            cam, cam_status = (tuple(int(v) for v in args.cam.split(",")) if isinstance(args.cam, str) else tuple(args.cam)), "informada"
        else:
            mem = camauto.da_memoria(canal, pasta.name)
            if mem:
                cam, cam_status = (tuple(mem["cam"]) if mem["cam"] else None), f"memória ({mem['origem']})"
            elif not cfg["detectar_cam"]:
                cam_status = "desligada"
        if cam_status != "procurar":
            log.info(f"Cam do streamer: {cam if cam else 'sem cam'} ({cam_status})")

        buf = (dint / ".buffer").resolve()    # absoluto: o render roda em outro processo/pasta
        shutil.rmtree(buf, ignore_errors=True)
        buf.mkdir()
        fila = queue.Queue()
        cap = captura.Captura(buf, url=args.url, arquivo=args.arquivo, seg_s=cfg["segmento_s"],
                              buffer_min=cfg["buffer_min"], ao_chegar=fila.put, travou_s=cfg["travou_captura_s"],
                              esperar_live_min=cfg["esperar_live_min"], fim_apos_offline_min=cfg["fim_apos_offline_min"],
                              video_gravado=self.video_gravado,
                              youtube_processando=status_fonte.get("youtube") == "post_live")
        fl = fala.Fala(tr)
        sin = momento.Sinais(cfg)
        chat = None
        if args.url and not self.video_gravado and "twitch.tv" in args.url and cfg["usar_chat"]:
            chat = momento.ChatTwitch(captura.canal_da_url(args.url), sin, lambda: cap.fim_live)
            chat.start()

        # ---------------- render em fila limitada, cada corte num processo separado ----------------
        pendentes = []                     # jobs esperando (lista protegida por lock)
        self._pendentes = pendentes
        lk = threading.Lock()
        tem_job = threading.Event()
        acabar_render = threading.Event()
        resultados, falhas, descartados = [], [], []
        n_corte = [0]

        aprend = [aprendizado.Base.carregar(cfg["pasta_saida"]), time.time()]

        def enfileirar(job):
            # v1.0.17: nota do GPT ajustada pelo que o Gabriel rejeitou antes ("Não quero"), no máximo ±2
            if time.time() - aprend[1] > 1800:
                aprend[:] = [aprendizado.Base.carregar(cfg["pasta_saida"]), time.time()]
            try:
                job["sinais"] = aprend[0].aplicar(canal, job["sinais"])
            except Exception as e:
                log.debug(f"aprendizado: {e}")
            aj = job["sinais"].get("ajuste_aprendizado")
            if aj:
                log.info(f"Nota do GPT ajustada de {job['sinais']['nota_gpt_original']} para "
                         f"{job['sinais']['nota_gpt']} pelo que você rejeitou ({aj['motivo']}).")
            # v1.0.17: corte repetido (mais da metade do tempo em comum com outro desta live) -> fica o de nota maior
            with lk:
                rival = triagem.repetido(job, pendentes)
                if rival is not None and triagem.valor(job) <= triagem.valor(rival):
                    cap.prender(job["segs"], False)
                    log.info(f"Momento em {_hms(job['evento'])} repete o corte em fila de {_hms(rival['evento'])} "
                             f"(nota igual ou maior); fica só o da fila.")
                    return
                if rival is not None:
                    pendentes.remove(rival)
                    cap.prender(rival["segs"], False)
                    log.info(f"Momento em {_hms(job['evento'])} repete o corte em fila de {_hms(rival['evento'])} "
                             f"com nota maior; fica só o novo.")
                pronto_igual = triagem.repetido(job, [r for r in resultados if isinstance(r, dict)])
                if pronto_igual is not None and triagem.valor(job) <= triagem.valor(pronto_igual):
                    cap.prender(job["segs"], False)
                    log.info(f"Momento em {_hms(job['evento'])} repete o corte pronto \"{pronto_igual.get('arquivo')}\" "
                             f"(nota igual ou maior); não corto de novo.")
                    return
                if pronto_igual is not None:
                    job["substitui_pronto"] = pronto_igual.get("arquivo")
                pendentes.append(job)
                if len(pendentes) > cfg["fila_max_cortes"]:
                    fraco = min(pendentes, key=lambda j: j["nota"])
                    pendentes.remove(fraco)
                    cap.prender(fraco["segs"], False)
                    descartados.append(_hms(fraco["evento"]))
                    log.aviso(f"Máquina atrasada: descartei o momento mais fraco ({_hms(fraco['evento'])}, "
                              f"nota {fraco['nota']:.1f}) para a fila não crescer.")
            tem_job.set()

        class _CorteDescartado(Exception):
            pass

        def _trocar_repetido(velho_nome, novo_nome):
            """O corte novo repete um pronto com nota menor: o pronto vai para a Lixeira do Windows (dá para restaurar)."""
            velho = pasta / str(velho_nome)
            if not velho.is_file() or velho.name == novo_nome:
                return
            try:
                from painel import lixeira
                lixeira.reciclar(velho)
                log.info(f"\"{velho.name}\" foi para a Lixeira: o corte novo \"{novo_nome}\" cobre o mesmo momento "
                         f"com nota maior.")
            except Exception as e:
                log.aviso(f"Corte repetido \"{velho.name}\" mantido (não consegui enviar à Lixeira: {e}).")

        def _limpar_parciais_corte(nome_base):
            for parcial in dint.glob(f"{nome_base}*"):
                try:
                    parcial.unlink(missing_ok=True)
                except OSError:
                    pass

        def _rodar_render_proc(pedido_arq):
            cmd = [sys.executable, "-m", "cortelive.render_job", str(pedido_arq)]
            if self._job_atual:
                self._job_atual["fase"] = "cortando"
                self._job_atual["t_render"] = time.time()
            if os.name == "nt":
                cmd = [sys.executable, "-m", "cortelive.processos", *cmd]
            proc = subprocess.Popen(
                cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                cwd=str(AQUI.parent), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            with self._proc_lock:
                self._proc_render = proc
            try:
                if self._descartar_ev.is_set():
                    try:
                        proc.kill()
                    except OSError:
                        pass
                    proc.wait()
                    raise _CorteDescartado()
                prazo = time.time() + cfg["travou_render_s"]
                while proc.poll() is None:
                    if self._descartar_ev.wait(0.2):
                        try:
                            proc.kill()
                        except OSError:
                            pass
                        proc.wait()
                        raise _CorteDescartado()
                    if time.time() >= prazo:
                        try:
                            proc.kill()
                        except OSError:
                            pass
                        proc.wait()
                        raise subprocess.TimeoutExpired(cmd, cfg["travou_render_s"])
                return proc.returncode
            finally:
                with self._proc_lock:
                    self._proc_render = None

        def renderizador():
            while True:
                if self._descartar_ev.is_set():
                    with lk:
                        restantes = list(pendentes)
                        pendentes.clear()
                        tem_job.clear()
                    for j in restantes:
                        cap.prender(j["segs"], False)
                    if restantes:
                        self._descartados_parada += len(restantes)
                        log.aviso(f"Live parada: {len(restantes)} corte(s) na fila descartado(s).")
                    return
                with lk:
                    job = pendentes.pop(0) if pendentes else None
                    if not job:
                        tem_job.clear()
                if not job:
                    if acabar_render.is_set() or self._descartar_ev.is_set():
                        return
                    tem_job.wait(0.4)
                    continue
                try:
                    resultados.append(fazer_corte(job))
                except _CorteDescartado:
                    self._descartados_parada += 1
                    log.aviso(f"Corte em andamento ({_hms(job['evento'])}) descartado porque a live foi parada.")
                except Exception as e:
                    falhas.append({"momento": _hms(job["evento"]), "erro": str(e)[:300]})
                    log.aviso(f"Corte do momento {_hms(job['evento'])} falhou e foi pulado: {str(e)[:200]}")
                finally:
                    cap.prender(job["segs"], False)

        def fazer_corte(job):
            ini, fim = job["ini"], job["fim"]
            self._job_atual = {"evento": job["evento"], "dur": fim - ini, "t0": time.time(), "fase": "legenda",
                               "t_render": time.time()}
            try:
                return _fazer_corte(job)
            finally:
                self._job_atual = None

        def _fazer_corte(job):
            ini, fim = job["ini"], job["fim"]
            n_corte[0] += 1
            nome = f"corte_{n_corte[0]:03d}_{_hms(job['evento']).replace(':', '')}"
            t0 = time.time()
            # legenda completa: se o corte pega um trecho que o watchdog pulou, espera ele ser retranscrito
            while fl.buraco_em(ini, fim) and time.time() - t0 < 120:
                if self._descartar_ev.wait(0.5):
                    raise _CorteDescartado()
                if self._parar_ev.is_set():
                    break
            if fl.buraco_em(ini, fim):
                log.aviso(f"{nome}: trecho ainda sem transcrição após 2 min; legenda pode ter falha.")
            job["palavras"] = fl.palavras_entre(ini, fim)
            # cam ainda sendo medida (primeiro 1,5 min): espera, para o corte não sair sem cam à toa
            while cam_status == "procurar" and time.time() - t0 < 150 and not cap.acabou.is_set():
                self._job_atual["fase"] = "cam"
                if self._descartar_ev.wait(0.5):
                    raise _CorteDescartado()
                if self._parar_ev.is_set():
                    break
            if self._descartar_ev.is_set():
                raise _CorteDescartado()
            bruto = buf / f"{nome}_trecho.mkv"
            base = trecho.juntar(job["segs"], bruto)
            pedido = buf / f"{nome}.json"
            res_arq = pedido.with_suffix(".res")
            # legenda queimada sem vírgula/ponto (mantém ? e !); palavra que vira só pontuação sai
            pal_rel = [{"w": t, "start": w["s"] - base, "end": w["e"] - base} for w in job["palavras"]
                       if (t := re.sub(r"[.,;:…\"“”]+", "", w["w"]).strip())]
            pedido.write_text(json.dumps({"video": str(bruto.resolve()), "inicio": ini - base, "fim": fim - base,
                                          "palavras": pal_rel, "modelo": cfg["modelo"], "legenda": cfg["legenda"],
                                          "cam": list(cam) if cam else None, "saida_dir": str(dint.resolve()),
                                          "nome": nome, "faixa_legenda": cfg.get("faixa_legenda", "rasgada"),
                                          "faixa_cor": cfg.get("faixa_cor", "vermelha"),
                                          "recuo_sem_cam": cfg.get("recuo_sem_cam"),
                                          # v1.0.16: cam automática é conferida em cada corte (render.rever_cam)
                                          # memória (de outra live) também: a cam pode ter mudado de lugar
                                          "conferir_cam": cam_status != "informada"},
                                         ensure_ascii=False), encoding="utf-8")
            self._job_atual["fase"] = "fila_render"
            try:
                rc = servicos.render().executar(_rodar_render_proc, pedido)
            except _CorteDescartado:
                bruto.unlink(missing_ok=True)
                pedido.unlink(missing_ok=True)
                res_arq.unlink(missing_ok=True)
                _limpar_parciais_corte(nome)
                raise
            except subprocess.TimeoutExpired:
                bruto.unlink(missing_ok=True)
                _limpar_parciais_corte(nome)
                raise RuntimeError(f"render passou de {cfg['travou_render_s']} s (watchdog cancelou)")
            except BaseException:
                bruto.unlink(missing_ok=True)
                raise
            if not res_arq.exists():
                bruto.unlink(missing_ok=True)
                raise RuntimeError(f"render morreu sem resposta (código {rc})")
            r = json.loads(res_arq.read_text(encoding="utf-8"))
            pedido.unlink(missing_ok=True); res_arq.unlink(missing_ok=True)
            if not r.get("ok"):
                bruto.unlink(missing_ok=True)
                log.debug(r.get("trace", ""))
                raise RuntimeError(r.get("erro", "erro desconhecido no render"))
            if r.get("caiu_sem_cam"):
                log.info(f"Modo do corte: {r.get('modelo')} — {r['caiu_sem_cam']}")
            pronto = time.time()
            if self._job_atual:
                self._render_hist = (self._render_hist + [(fim - ini, pronto - self._job_atual["t_render"])])[-6:]
            # render sai em _interno/corte_NNN_*; aqui ganha o nome legível e vai para a pasta visível
            dsl = pasta / pastas.SEM_LEGENDA
            legivel = pastas.unico(pasta, pastas.nome_corte(canal, job["titulo"], pronto, reserva=nome),
                                   tambem=(dsl, dint))
            final = pasta / f"{legivel}.mp4"
            os.replace(r["mp4"], final)
            if r.get("aviso"):
                log.aviso(f"Atenção: {r['aviso']} Arquivo: {final.name}")
            sem_leg = None
            if r.get("mp4_sem_legenda") and Path(r["mp4_sem_legenda"]).exists() and r["mp4_sem_legenda"] != r["mp4"]:
                dsl.mkdir(exist_ok=True)
                sem_leg = dsl / f"{legivel}.mp4"
                os.replace(r["mp4_sem_legenda"], sem_leg)
            ass = None
            if r.get("ass") and Path(r["ass"]).exists():
                ass = dint / f"{legivel}.ass"
                os.replace(r["ass"], ass)
            # v1.0.16: o trecho bruto do corte fica guardado ao lado do .json para o editor de foco do painel refazer
            # o corte (marcar câmera e gameplay à mão). Sai junto quando o corte vai para a Lixeira.
            refazer = None
            if cfg.get("guardar_trecho_corte", True):
                try:
                    os.replace(bruto, dint / f"{legivel}.mkv")
                    refazer = {"video": f"{legivel}.mkv", "inicio": round(ini - base, 3), "fim": round(fim - base, 3),
                               "palavras": pal_rel, "modelo": cfg["modelo"], "legenda": cfg["legenda"],
                               "faixa_legenda": cfg.get("faixa_legenda", "rasgada"),
                               "faixa_cor": cfg.get("faixa_cor", "vermelha"),
                               "recuo_sem_cam": cfg.get("recuo_sem_cam"), "nome": nome}
                except OSError:
                    refazer = None
            bruto.unlink(missing_ok=True)
            if r.get("cam_revista"):
                log.info(f"{legivel}: {r['cam_revista']}")
            info = {
                "arquivo": final.name, "nome_interno": nome,
                "sem_legenda": f"{pastas.SEM_LEGENDA}/{sem_leg.name}" if sem_leg else None,
                "legenda": ass.name if ass else None, "aviso": r.get("aviso"),
                "inicio_na_live": round(ini, 2), "fim_na_live": round(fim, 2),
                "inicio_hms": _hms(ini), "fim_hms": _hms(fim), "duracao": round(fim - ini, 2),
                "momento_na_live": round(job["evento"], 2), "momento_hms": _hms(job["evento"]),
                "pontuacao": round(job["nota"], 2), "nota_juiz": job["sinais"].get("nota_gpt"),
                "juiz": job["sinais"].get("juiz", "heurística"),
                "sinais": job["sinais"], "motivos": job["motivos"],
                "titulo_sugerido": job["titulo"], "motivo": job["motivo"],
                "modelo": cfg["modelo"], "modelo_usado": r.get("modelo"), "estilo_legenda": cfg["legenda"],
                "cam": r["cam_usada"] if "cam_usada" in r else (list(cam) if cam else None),
                "cam_revista": r.get("cam_revista"), "refazer": refazer, "encoder": r.get("encoder"),
                "latencia_s": {"momento_ate_mp4": round(pronto - job["chegou_evento"], 1),
                               "decisao_ate_mp4": round(pronto - job["decidido"], 1),
                               "render": round(pronto - t0, 1)},
                "transcricao": " ".join(w["w"] for w in job["palavras"]),
            }
            (dint / f"{legivel}.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
            miniatura.gerar(final, dint, legivel, fim - ini)     # v1.0.17: quadro do meio + tira da prévia
            if job.get("substitui_pronto"):
                _trocar_repetido(job["substitui_pronto"], final.name)
            self._ultimo_corte = final.name
            self._pronto_em = (time.time(), job["titulo"])
            try:
                copia = destino.disparar(final, cfg)
                if copia is not None:
                    copias_destino.append(copia)
            except (OSError, RuntimeError):
                log.aviso(f"Não consegui enfileirar {final.name} para o Drive. Você pode enviar pelo painel.")
            log.corte(f"CORTE {n_corte[0]}: {final.name} ({fim - ini:.0f} s, nota {job['nota']:.1f}) — "
                      f"\"{job['titulo']}\" — pronto {info['latencia_s']['momento_ate_mp4']:.0f} s após o momento")
            return info

        th_r = threading.Thread(target=log.herdar(renderizador), daemon=True)
        th_r.start()
        self._cap, self._fl, self._res, self._falhas = cap, fl, resultados, falhas
        if self._parar_ev.is_set():
            cap.motivo_fim = "parado pelo usuário"
            cap.estado = "encerrada"
            cap.parar.set()
            cap.acabou.set()
        else:
            cap.start()
        mensagem_fonte = "Analisando o vídeo gravado." if self.video_gravado else "Acompanhando a live."
        log.info(mensagem_fonte + (" Para parar: Ctrl+C." if self.terminal else ""))

        from .gpt import Juiz
        juiz = Juiz(log.aviso)
        sel = momento.Seletor(cfg)
        hist_notas = {}
        quentes_titulo = list(cfg["palavras_quentes"]) + list(cfg.get("palavras_apoio", []))

        def aceitar(dec, notas=None):
            s, nota, mot, sinais, j = dec["pico"], dec["nota"], dec["mot"], dec["sinais"], dec["janela"]
            partes = j.get("partes") or [j]
            preparados = []
            for idx, jj in enumerate(partes):
                ini, fim = jj["ini"], jj["fim"]
                # corte só em silêncio (dentro da pausa entre palavras), também quando o GPT escolheu o trecho
                if notas is not None or jj.get("lim_ini") or jj.get("lim_fim"):
                    aa = max(0.0, ini - 1)
                    aud = fl.audio_trecho(aa, fim + 1)
                    ini = momento.ajustar_no_vale(ini, aud, aa, limites=jj["lim_ini"])
                    fim = momento.ajustar_no_vale(fim, aud, aa, limites=jj["lim_fim"])
                segs = cap.trecho(ini, fim)
                if not segs or segs[0].ini > ini + 0.5 or segs[-1].fim < fim - 0.5:
                    log.aviso(f"Momento em {_hms(s)} caiu num buraco da captura; pulado.")
                    return False
                preparados.append((ini, fim, segs))
            if dec["substitui"]:     # clímax muito mais forte que o corte ainda esperando render: troca
                with lk:
                    velho = pendentes.pop() if pendentes else None
                if velho:
                    cap.prender(velho["segs"], False)
                    log.info(f"Momento forte em {_hms(s)} (nota {nota:.1f}) substitui o corte em fila de "
                             f"{_hms(velho['evento'])} (nota {velho['nota']:.1f}).")
            antes_fim = sel.ultimo_fim
            for idx, (ini, fim, segs) in enumerate(preparados):
                pal = fl.palavras_entre(ini, fim)
                motivos = mot
                if notas is not None:
                    motivos = [m for k in range(int(ini), int(fim)) if k in notas
                               and notas[k][0] >= cfg["limiar_nota"] * 0.6 for m in notas[k][1]] or mot
                titulo = momento.titulo_heuristico(
                    " ".join(w["w"] for w in pal), motivos, pal, s, quentes_titulo)
                if len(preparados) > 1:      # contexto longo demais para um corte: Parte 1 / Parte 2 (título e legenda do post)
                    titulo = f"{titulo[:56].rstrip()} - Parte {idx + 1}"
                motivo = "; ".join(dict.fromkeys(motivos))
                seg_ev = next((x for x in segs if x.ini <= s < x.fim), segs[-1])
                dec["fim"] = fim
                cap.prender(segs, True)
                log.info(f"Momento bom em {_hms(s)} ({', '.join(dict.fromkeys(mot))}). Cortando {_hms(ini)}–{_hms(fim)}"
                         f"{f' (parte {idx + 1} de {len(preparados)})' if len(preparados) > 1 else ''}…")
                enfileirar({"ini": ini, "fim": fim, "evento": float(s), "segs": segs, "palavras": pal, "nota": nota,
                            "sinais": sinais, "motivos": list(dict.fromkeys(motivos)), "ultimo_fim_antes": antes_fim,
                            "titulo": titulo, "motivo": motivo, "chegou_evento": seg_ev.chegou, "decidido": time.time()})
            return True

        def avaliar(final=False):
            disponivel = fl.feito_ate
            min_ini = max(0.0, sel.ultimo_fim + cfg["intervalo_min_entre_cortes"], max(cap.emendas, default=-1.0) + 0.3)
            anterior = juiz.revisado_ate
            def marcas(a, b):
                """Risada, grito e chat por segundo, para o GPT ver o que a fala sozinha não mostra."""
                ns = sin.notas(max(0.0, a), b, fl.palavras_entre(a - 2, b))
                return {s: v[1] for s, v in ns.items() if v[1] and v[0] >= cfg["limiar_nota"] * 0.6}

            modo_selecao, decisao = juiz.selecionar(
                fl.palavras, disponivel, min_ini, momento.duracao_minima(cfg), momento.duracao_maxima(cfg), final,
                alvo_min=cfg["duracao_min"], marcas=marcas)
            if modo_selecao == "gpt":
                if juiz.revisado_ate > anterior:
                    log.debug(f"GPT revisou a transcrição até {_hms(juiz.revisado_ate)}: "
                              + ("escolheu um corte." if decisao else "sem corte nesta janela."))
                if decisao and aceitar(decisao):
                    sel.ultimo_fim = decisao["fim"]
                    sel.ultima_nota = decisao["nota"]
                return
            sel.avaliado_ate = max(sel.avaliado_ate, juiz.revisado_ate - cfg["depois_do_momento"] - 2)
            limite = disponivel if final else disponivel - cfg["depois_do_momento"] - 2
            a0 = sel.avaliado_ate
            if limite - a0 < 1:
                return
            notas_ate = int(min(disponivel, limite + 10)) - 1
            notas = sin.notas(max(0, a0 - 10), min(disponivel, limite + 10), fl.palavras_entre(a0 - 12, disponivel))
            janela = [(notas[s][0], s) for s in range(int(a0), int(limite)) if s in notas]
            for v, s in janela:
                hist_notas[s] = v
            for s in [s for s in hist_notas if s < limite - 600]:
                del hist_notas[s]
            # limiar adaptativo: live "calma" (ex.: narração constante) passa muito tempo sem bater o limiar fixo;
            # depois de N min sem corte, vale o topo das notas dos últimos 10 min (com piso).
            limiar = cfg["limiar_nota"]
            sem_corte = limite - max(sel.ultimo_fim, 0)
            if cfg["minutos_sem_corte_para_adaptar"] and sem_corte > cfg["minutos_sem_corte_para_adaptar"] * 60 \
                    and len(hist_notas) > 240:
                import numpy as np
                limiar = max(cfg["limiar_minimo"], min(limiar, float(np.percentile(list(hist_notas.values()), 97))))
            if janela:
                mx = max(janela)
                log.debug(f"notas {_hms(a0)}-{_hms(limite)}: máx {mx[0]:.2f} em {_hms(mx[1])} {notas[mx[1]][2]} "
                          f"limiar {limiar:.2f}")

            def pendente():
                with lk:
                    return (pendentes[-1]["nota"], pendentes[-1]["ultimo_fim_antes"]) if pendentes else None

            sel.passo(notas, fl.palavras, disponivel, limite, notas_ate, limiar, list(cap.emendas),
                      lambda decisao: aceitar(decisao, notas), pendente, final)

        exe = cf.ThreadPoolExecutor(max_workers=1, initializer=log.usar, initargs=(log.atual(),))
        reinicios_fala = [0]
        buracos_preenchidos = []

        def transcrever(fim, final=False):
            nonlocal exe
            if self._parar_ev.is_set():
                return
            # live com momento já detectado (corte esperando legenda/render) passa na frente na fila da fala
            (sv.prioritarias.add if (self._pendentes or self._job_atual) else sv.prioritarias.discard)(canal)
            gatilho = buf / "TRAVAR_TRANSCRICAO"     # só para teste de robustez: simula motor travado
            if gatilho.exists():
                gatilho.unlink(missing_ok=True)
                log.debug("teste: transcrição travada de propósito")
                fut = exe.submit(lambda: (time.sleep(cfg["travou_transcricao_s"] + 60), fl.avancar(fim, final)))
            else:
                fut = exe.submit(fl.avancar, fim, final)
            prazo = time.time() + cfg["travou_transcricao_s"]
            try:
                while True:
                    if self._parar_ev.is_set():
                        exe.shutdown(wait=False, cancel_futures=True)
                        return
                    restante = prazo - time.time()
                    if restante <= 0:
                        raise cf.TimeoutError()
                    try:
                        fut.result(timeout=min(0.3, restante))
                        return
                    except cf.TimeoutError:
                        if time.time() >= prazo:
                            raise
            except cf.TimeoutError:
                reinicios_fala[0] += 1
                log.aviso(f"Transcrição travou (> {cfg['travou_transcricao_s']} s). Reiniciando o motor; "
                          f"o trecho até {_hms(fim)} fica sem legenda.")
                fl.pular_ate(fim)
                exe.shutdown(wait=False, cancel_futures=True)
                exe = cf.ThreadPoolExecutor(max_workers=1, initializer=log.usar, initargs=(log.atual(),))
            except InterruptedError:
                return
            except Exception as e:
                if self._parar_ev.is_set():
                    return
                log.aviso(f"Erro na transcrição (trecho pulado): {e}")
                fl.pular_ate(fim)

        def preencher_buracos():
            """Prioridade menor que o ao vivo: só roda quando não há pedaço novo esperando."""
            nonlocal exe
            while fl.buracos and fila.empty():
                fut = exe.submit(fl.preencher_buraco)
                try:
                    r = fut.result(timeout=cfg["travou_transcricao_s"])
                except Exception as e:
                    log.aviso(f"Retranscrição do trecho pulado falhou ({e}); tento de novo depois.")
                    if isinstance(e, cf.TimeoutError):
                        exe.shutdown(wait=False, cancel_futures=True)
                        exe = cf.ThreadPoolExecutor(max_workers=1, initializer=log.usar, initargs=(log.atual(),))
                    return
                if r:
                    a, b, n = r
                    if n < 0:
                        log.aviso(f"Trecho {_hms(a)}–{_hms(b)} já saiu do buffer; ficou sem legenda.")
                    elif not fl.buracos or fl.buracos[0][0] > b - 1:
                        log.ok(f"Trecho pulado retranscrito: {_hms(a)}–{_hms(b)} (+{n} palavras).")
                    buracos_preenchidos.append([round(a, 1), round(b, 1), n])

        # câmera automática: 3 quadros espaçados no primeiro minuto
        amostras_cam = []

        def tentar_cam(seg):
            nonlocal cam, cam_status
            if cam_status != "procurar":
                return
            if not amostras_cam or seg.ini - amostras_cam[-1][0] >= 6:
                amostras_cam.append((seg.ini, Path(seg.arquivo)))
                cap.prender([seg], True)
            if len(amostras_cam) == 8:
                try:
                    m_cam = render.carregar("modelo", cfg["modelo"])
                    prop = m_cam.get("largura", 1080) / m_cam.get("altura_cam", 640)
                    cam = camauto.detectar([a for _, a in amostras_cam], prop)
                    log.debug(f"camauto: {camauto.ULTIMO}")
                except Exception as e:
                    log.debug(f"camauto: {e}")
                    cam = None
                cam_status = "detectada" if cam else "não achada"
                camauto.salvar(canal, cam, "automática", pasta.name)
                with cap.lock:
                    cap.presos -= {i for i, s in cap.segs.items() if Path(s.arquivo) in {a for _, a in amostras_cam}}
                log.info(f"Cam do streamer: {'achei em ' + ','.join(map(str, cam)) if cam else ('confiança baixa (' + camauto.ULTIMO.get('motivo', '?') + ') — usando o enquadramento definido pelo modelo de corte')}"
                         f" (guardado para o canal '{canal}')")

        prox_disco = [0.0]

        def vigiar_disco():
            """Avisa (painel) abaixo do limite de aviso; abaixo do crítico para com segurança."""
            if time.time() < prox_disco[0]:
                return
            prox_disco[0] = time.time() + cfg.get("disco_checar_s", 15)
            nv, lv, fr = disco.avaliar(pasta, cfg)
            if nv == "critico":
                self._parou_disco = fr
                self._aviso_disco = fr
                log.aviso(fr)
                # descarta o corte em andamento (não precisa de disco para terminar); cortes prontos ficam
                self.parar(descartar_cortes=True)
                cap.motivo_fim = "disco quase cheio"
            elif fr != self._aviso_disco:
                self._aviso_disco = fr
                if fr:
                    log.aviso(fr)
            self._avisar()

        try:
            while not self._parar_ev.is_set():
                vigiar_disco()
                try:
                    seg = fila.get(timeout=0.25)
                except queue.Empty:
                    seg = None
                if self._parar_ev.is_set():
                    break
                if seg:
                    try:
                        aud = fala.audio_de(seg.arquivo)
                    except Exception as e:
                        log.aviso(f"Pedaço {seg.idx} ilegível ({e}); pulado.")
                        continue
                    fl.novo_segmento(seg, aud)
                    self._avisar()
                    sin.novo_audio(seg.ini, aud)
                    tentar_cam(seg)
                    if not self._parar_ev.is_set() and (
                        seg.fim - fl.feito_ate >= sv.bloco_s or (fila.empty() and seg.fim - fl.feito_ate >= sv.bloco_s / 2)
                    ):
                        transcrever(seg.fim)
                        if self._parar_ev.is_set():
                            break
                        try:
                            avaliar()
                        except Exception as e:
                            log.aviso(f"Erro avaliando momentos (seguindo): {e}")
                        if not self._parar_ev.is_set() and fl.buracos and fila.empty():
                            preencher_buracos()
                if args.minutos and time.time() - t_sessao > args.minutos * 60 and not cap.parar.is_set():
                    log.info(f"Tempo pedido ({args.minutos:g} min) acabou.")
                    cap.solicitar_parada(f"tempo pedido ({args.minutos:g} min) acabou")
                if cap.acabou.is_set() and fila.empty():
                    if not self._parar_ev.is_set():
                        transcrever(cap.fim_live, final=True)
                        preencher_buracos()
                        avaliar(final=True)
                    break
        except KeyboardInterrupt:
            log.info("Parando (Ctrl+C)… terminando os cortes em andamento.")
            cap.solicitar_parada("parado pelo usuário")
        acabar_render.set()
        tem_job.set()
        th_r.join()
        if chat:
            chat.parar.set()
        exe.shutdown(wait=False, cancel_futures=True)
        if self._limpar_bruto:
            livres = disco.limpar_bruto(pasta, dint)
            log.debug(f"limpeza do bruto: {livres / 1024 ** 2:.0f} MB liberados")
        else:
            log.debug("limpeza do bruto desligada (limpar_bruto = false): gravação mantida em _interno/.buffer")
        feitas, pendentes_copia = cf.wait(copias_destino, timeout=120)
        if pendentes_copia:
            log.aviso("Ainda há cópias em andamento. Confira o Drive e reenvie pelo painel se faltar algum corte.")
        if any(copia.exception() for copia in feitas):
            log.aviso("Uma cópia para o Drive foi interrompida. Você pode tentar novamente pelo painel.")

        if cap.erro_fatal and not resultados:
            raise RuntimeError(cap.erro_fatal)

        # ---------------- resumo ----------------
        rt = sum(b for _, b in fl.tempo_trans) / max(1e-6, sum(a for a, _ in fl.tempo_trans))
        lat = [r["latencia_s"]["momento_ate_mp4"] for r in resultados]
        resumo = {
            "canal": canal, "fonte": args.url or str(args.arquivo), "motivo_fim": cap.motivo_fim,
            "duracao_sessao_s": round(time.time() - t_sessao, 1), "live_acompanhada_s": round(cap.fim_live, 1),
            "cortes": len(resultados), "falhas": falhas, "descartados_por_fila": descartados,
            "cortes_descartados_parada": self._descartados_parada,
            "quedas_da_captura": [{"no_ponto_s": a, "fora_s": b} for a, b in cap.quedas],
            "reinicios_transcricao": reinicios_fala[0],
            "mensagens_chat": chat.total if chat else None,
            "trocas_de_modelo": sv.trocas,
            "transcricao_por_modelo": {m: {"rodadas": len(v), "fator_tempo_real": round(sum(b for _, b in v) / max(1e-6, sum(a for a, _ in v)), 3)}
                                       for m, v in fl.por_modelo.items()},
            "trechos_retranscritos": buracos_preenchidos, "trechos_sem_transcricao": fl.buracos or [],
            "latencia_momento_ate_mp4_s": {"media": round(sum(lat) / len(lat), 1) if lat else None,
                                           "min": min(lat) if lat else None, "max": max(lat) if lat else None},
            "transcricao_fator_tempo_real": round(rt, 3), "palavras": len(fl.palavras),
            "modo": modo.resumo(), "juiz": "heurística", "cam": list(cam) if cam else None,
            "lista_cortes": [{k: r[k] for k in ("arquivo", "inicio_hms", "duracao", "pontuacao", "titulo_sugerido")}
                             for r in resultados],
        }
        (dint / "sessao.json").write_text(json.dumps(resumo, ensure_ascii=False, indent=2), encoding="utf-8")
        log.info("=" * 60)
        log.info(f"RESUMO — {resumo['motivo_fim'] or 'fim'}")
        log.info(f"Live acompanhada: {_hms(cap.fim_live)} | sessão: {_hms(resumo['duracao_sessao_s'])}")
        log.info(f"Cortes prontos: {len(resultados)} | falharam: {len(falhas)} | descartados (fila): {len(descartados)}")
        if lat:
            log.info(f"Tempo do momento até o MP4 pronto: média {resumo['latencia_momento_ate_mp4_s']['media']} s "
                     f"(de {min(lat)} a {max(lat)} s)")
        if cap.quedas:
            log.info(f"A captura caiu {len(cap.quedas)}x e voltou sozinha.")
        log.info(f"Transcrição usou {rt * 100:.0f}% do tempo real.")
        log.info(f"Pasta: {pasta.resolve()}  |  log: {arq_log}")
        return resumo

