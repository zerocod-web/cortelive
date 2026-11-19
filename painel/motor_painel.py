"""Ponte entre o Painel do CorteLive e o motor existente (cortelive.sessao / cortelive.multi).

Roda dentro do .venv local, usa a mesma configuração e detecção de hardware do cortar.cmd,
emite linhas JSON de telemetria (@@STATUS@@{...}) a cada segundo e aceita 'parar' via stdin
para encerrar de forma limpa terminando os cortes que já estão renderizando.
"""
import argparse
import queue
import uuid
import base64
import hmac
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
import types
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

RAIZ_PAINEL = Path(__file__).resolve().parents[1]
LIMITE_CORPO = 65536
PARADA_APOS_S = 3600   # log sem mexer há 1 h = live terminada ou parada
PADRAO_POSTAGEM = {"cortes_longos": False}
DURACAO_MODOS = ("livre", "30", "45", "61")     # duração mínima dos cortes; padrão: mínimo 1 minuto (61 s)
DURACAO_PADRAO = "61"
def ler_json(caminho: Path):
    try:
        dados = json.loads(caminho.read_text(encoding="utf-8-sig"))
        return dados if isinstance(dados, dict) else {}
    except (OSError, ValueError):
        return {}


def ler_config_postagem(raiz: Path):
    return ler_json(raiz / "painel_config.json")


def config_postagem(raiz: Path):
    dados = ler_config_postagem(raiz)
    config = {
        chave: dados[chave] if isinstance(dados.get(chave), bool) else padrao
        for chave, padrao in PADRAO_POSTAGEM.items()
    }
    modo = str(dados.get("duracao_minima_modo", DURACAO_PADRAO))
    modo = modo if modo in DURACAO_MODOS else DURACAO_PADRAO
    config["duracao_minima_modo"] = modo
    config["cortes_longos"] = modo != "livre"        # compatível com o interruptor antigo
    from cortelive import original
    config["originais_dias"] = original.dias_config(dados)
    return config


class ErroApi(Exception):
    def __init__(self, status, mensagem):
        super().__init__(mensagem)
        self.status = status


def campo(corpo, nome, padrao=""):
    valor = corpo.get(nome, padrao)
    if not isinstance(valor, str) or "\0" in valor:
        raise ErroApi(400, f"Campo inválido: {nome}.")
    return valor


class ApiPainel:
    """Rotas complementares. Instalação, sessão e atualização continuam no host."""

    def __init__(self, raiz):
        self.raiz = Path(raiz).resolve()
        self.python = self.raiz / ".venv" / "Scripts" / "python.exe"
        self.trava_config = threading.Lock()
        self.trava_exclusao = threading.Lock()
        self.limpeza_originais_em = 0.0

    def ambiente(self):
        env = os.environ.copy()
        env.pop("CORTELIVE_PAINEL_TOKEN", None)
        env.update(PYTHONUTF8="1", PYTHONPATH=str(self.raiz), HF_HOME=str(self.raiz / "modelos"))
        env["PATH"] = os.pathsep.join((str(self.raiz / "ffmpeg" / "bin"),
                                       str(self.python.parent), env.get("PATH", "")))
        return env

    def config_motor(self):
        cfg = {}
        for arq in (self.raiz / "cortelive" / "config.toml", self.raiz / "config.toml"):
            try:
                cfg.update(tomllib.loads(arq.read_text(encoding="utf-8-sig")))
            except FileNotFoundError:
                continue
            except (OSError, ValueError):
                raise ErroApi(400, "Confira o arquivo de configuração do CorteLive.") from None
        saida = cfg.get("pasta_saida", "cortes")
        if not isinstance(saida, str) or not saida.strip():
            raise ErroApi(400, "A pasta de saída do CorteLive é inválida.")
        cfg["pasta_saida"] = str((self.raiz / saida).resolve())
        return cfg

    def pasta_cortes(self):
        return Path(self.config_motor()["pasta_saida"])

    def dentro_dos_cortes(self, texto, video=True):
        pasta = self.pasta_cortes()
        caminho = (pasta / texto).resolve()
        if not caminho.is_relative_to(pasta):
            raise ErroApi(404, "Corte não encontrado.")
        if caminho != pasta and any(p.startswith((".", "_")) for p in caminho.relative_to(pasta).parts):
            raise ErroApi(404, "Corte não encontrado.")
        if video and (caminho.suffix.lower() != ".mp4" or not caminho.is_file()):
            raise ErroApi(404, "Vídeo não encontrado.")
        return caminho

    def limpar_originais(self, forcar=False):
        """Apaga os originais de corte vencidos (prazo das configurações). No máximo a cada 10 min."""
        from cortelive import original
        agora = time.time()
        if not forcar and agora - self.limpeza_originais_em < 600:
            return 0, 0
        self.limpeza_originais_em = agora
        try:
            dias = original.dias_config(ler_config_postagem(self.raiz))
            return original.limpar_vencidos(self.pasta_cortes(), dias, agora)
        except (OSError, ErroApi):
            return 0, 0

    def listar_cortes(self):
        from cortelive import destino, pastas
        from painel import foco

        self.limpar_originais()
        pasta = self.pasta_cortes()
        candidatos = []
        for diretorio, subpastas, arquivos in os.walk(pasta):
            subpastas[:] = [
                nome for nome in subpastas
                if not nome.startswith((".", "_")) and nome.casefold() != pastas.SEM_LEGENDA.casefold()
                and not (Path(diretorio) / nome).is_junction()
            ]
            for nome in arquivos:
                if Path(nome).suffix.lower() != ".mp4" or nome.lower().endswith("_sem_legenda.mp4"):
                    continue
                video = Path(diretorio) / nome
                try:
                    video = video.resolve()
                    if video.is_relative_to(pasta) and not any(
                        p.startswith((".", "_")) for p in video.relative_to(pasta).parts
                    ):
                        candidatos.append((video.stat().st_mtime, video))
                except OSError:
                    continue
        itens = []
        for mtime, video in sorted(candidatos, reverse=True)[:60]:
            meta = pastas.meta_do_video(video)
            dados = ler_json(meta) if meta else {}
            nota_gpt = dados.get("nota_juiz")
            if dados.get("juiz") not in ("api", "chatgpt") or type(nota_gpt) is not int or not 0 <= nota_gpt <= 10:
                nota_gpt = None
            sem_legenda = next((p for p in (
                video.parent / pastas.SEM_LEGENDA / video.name,
                video.with_stem(video.stem + "_sem_legenda"),
            ) if p.is_file()), None)
            itens.append({
                "titulo": dados.get("titulo_sugerido") or video.stem.split(" - ", 2)[-1],
                "duracao": dados.get("duracao"),
                "nota_juiz": nota_gpt,
                "miniatura": "/api/miniatura?caminho=" + quote(str(video), safe=""),
                "caminho": str(video), "pasta": str(video.parent),
                "caminho_sem_legenda": str(sem_legenda) if sem_legenda else None,
                "nome_arquivo": video.name, "canal": pastas.canal_da_pasta(video.parent.name),
                "data_hora": datetime.fromtimestamp(mtime).strftime("%d/%m %H:%M"),
                "inicio_hms": dados.get("momento_hms") or dados.get("inicio_hms"),
                "motivo": dados.get("motivo"), "modelo": dados.get("modelo_usado") or dados.get("modelo"),
                "modo": foco.modo_atual(dados) if meta else None,
                "tem_original": bool(meta and any(
                    isinstance((dados.get(k) or {}).get("video"), str)
                    and (meta.parent / Path(dados[k]["video"]).name).is_file() for k in ("refazer", "foco_fonte"))),
                "destino": destino.estado_copia(video),
            })
        return itens

    def excluir_corte(self, corpo):
        from cortelive import pastas
        from painel import lixeira

        with self.trava_exclusao:
            texto = campo(corpo, "caminho")
            video = self.dentro_dos_cortes(texto)
            meta = pastas.meta_do_video(video)
            dados = ler_json(meta) if meta else {}
            fonte = ler_json(pastas.sessao_json_do_video(video)).get("fonte")
            if (Path(texto) != video or dados.get("arquivo") != video.name
                    or video.parent.name.casefold() == pastas.SEM_LEGENDA.casefold()
                    or (isinstance(fonte, str) and "://" not in fonte
                        and (self.raiz / fonte).resolve() == video)):
                raise ErroApi(400, "Escolha um MP4 gerado pelo CorteLive.")
            try:
                lixeira.reciclar(video)
            except (OSError, subprocess.SubprocessError):
                raise ErroApi(409, "Não foi possível enviar o corte à Lixeira. Confira se ele está em uso e se a unidade tem Lixeira.") from None
            if meta:   # trecho bruto guardado para o editor de foco (v1.0.16): só serve com o MP4
                for chave in ("refazer", "foco_fonte"):
                    nome = (dados.get(chave) or {}).get("video")
                    if nome and Path(nome).name == nome and nome.lower().endswith(".mkv"):
                        try:
                            (meta.parent / nome).unlink(missing_ok=True)
                        except OSError:
                            pass
        return {"mensagem": "O MP4 do corte foi enviado à Lixeira."}

    def foco(self, acao, url, corpo):
        """Editor de foco do corte: estado, quadro (JPEG), refazer com a marcação, voltar ao automático, baixar."""
        from painel import foco
        os.environ["PATH"] = self.ambiente()["PATH"]      # ffmpeg/ffprobe da instalação (quadro e tamanho)
        q = parse_qs(url.query)
        texto = q.get("caminho", [""])[0] if acao in ("estado", "quadro") else campo(corpo, "caminho")
        video = self.dentro_dos_cortes(texto)
        try:
            if acao == "estado":
                return foco.estado(video)
            if acao == "quadro":
                try:
                    t = float(q.get("t", ["0"])[0])
                except ValueError:
                    t = 0.0
                return foco.quadro(video, t)
            pedido = None
            if acao == "modo":
                modo = corpo.get("modo")
                if modo not in foco.MODOS:
                    raise ErroApi(400, "Escolha o modo do corte: Câmera + jogo, Tela cheia ou Fundo desfocado.")
                return foco.iniciar(video, "modo", None, self.ambiente(), self.raiz,
                                    self.python if self.python.is_file() else None, modo=modo)
            if acao == "refazer":
                pedido = corpo.get("foco")
                if not isinstance(pedido, dict):
                    raise ErroApi(400, "Marque a área da câmera e a da gameplay antes de refazer.")
                pedido = {k: [int(round(float(v))) for v in pedido[k]] for k in ("cam", "jogo")
                          if isinstance(pedido.get(k), list) and len(pedido[k]) == 4}
            return foco.iniciar(video, acao, pedido, self.ambiente(), self.raiz,
                                self.python if self.python.is_file() else None)
        except foco.ErroFoco as erro:
            raise ErroApi(400, str(erro)) from None

    def miniatura(self, video):
        from cortelive import pastas

        for pasta in (pastas.interno_do_video(video), video.parent):
            jpg = pasta / (video.stem + ".jpg")
            if jpg.is_file() and jpg.resolve().is_relative_to(self.pasta_cortes()):
                return jpg.read_bytes()
        processo = subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", "1", "-i", str(video), "-frames:v", "1",
             "-vf", "scale=240:-2", "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1"],
            env=self.ambiente(), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, timeout=12,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if processo.returncode or not processo.stdout:
            raise ErroApi(404, "Miniatura indisponível.")
        return processo.stdout

    def postagem(self, metodo, corpo):
        if metodo == "GET":
            return config_postagem(self.raiz)
        alteracoes = {k: corpo[k] for k in PADRAO_POSTAGEM if k in corpo}
        if "originais_dias" in corpo:
            from cortelive import original
            try:
                dias = int(corpo["originais_dias"])
            except (TypeError, ValueError):
                dias = -1
            if isinstance(corpo["originais_dias"], bool) or dias not in original.DIAS_OPCOES:
                raise ErroApi(400, "Prazo inválido para apagar os originais.")
            alteracoes["originais_dias"] = dias
        if "duracao_minima_modo" in corpo:
            if str(corpo["duracao_minima_modo"]) not in DURACAO_MODOS:
                raise ErroApi(400, "Opção de duração inválida.")
            alteracoes["duracao_minima_modo"] = str(corpo["duracao_minima_modo"])
            alteracoes.pop("cortes_longos", None)
        if not alteracoes:
            raise ErroApi(400, "Informe pelo menos uma opção.")
        for chave, valor in alteracoes.items():
            if chave not in ("duracao_minima_modo", "originais_dias") and not isinstance(valor, bool):
                raise ErroApi(400, f"Opção inválida: {chave}.")
        with self.trava_config:
            arq = self.raiz / "painel_config.json"
            dados = ler_config_postagem(self.raiz)
            dados.update(alteracoes)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.raiz,
                                             prefix=".painel-config-", delete=False) as tmp:
                temporario = Path(tmp.name)
                try:
                    json.dump(dados, tmp, ensure_ascii=False, indent=2)
                    tmp.close()
                    temporario.replace(arq)
                finally:
                    temporario.unlink(missing_ok=True)
        if "originais_dias" in alteracoes:
            self.limpeza_originais_em = 0.0          # o prazo novo vale na próxima listagem dos cortes
        return config_postagem(self.raiz)

    def drive(self, acao, metodo, corpo):
        from cortelive import destino

        if acao == "detectar":
            opcoes = destino.detectar()
            mensagem = ("Escolha uma pasta sincronizada ou informe o caminho manualmente." if opcoes else
                        "Nenhuma pasta sincronizada foi encontrada. Abra o Google Drive no computador "
                        "ou informe o caminho manualmente.")
            return {"opcoes": opcoes, "mensagem": mensagem}

        cfg = {**self.config_motor(), "painel_raiz": self.raiz}
        try:
            if acao == "config":
                if metodo == "GET":
                    return destino.config_atual(cfg)
                alteracoes = {k: corpo[k] for k in destino.PADRAO if k in corpo}
                if not alteracoes:
                    raise ErroApi(400, "Informe pelo menos uma opção do destino.")
                for chave, valor in alteracoes.items():
                    if not destino.opcao_valida(chave, valor):
                        raise ErroApi(400, f"Opção do destino inválida: {chave}.")
                with self.trava_config:
                    preferencias = destino.salvar_preferencias(cfg, alteracoes)
                return {**preferencias, "mensagem": "Preferências do destino salvas."}

            video = self.corte_para_destino(corpo)
            pasta = destino.config_atual(cfg)["destino_pasta"]
            destino.verificar_pasta(pasta, cfg["pasta_saida"])
            copia = destino.copiar(video, cfg)
            if copia["estado"] == "erro":
                raise ErroApi(502, copia["erro"])
            return {"destino": copia, "mensagem": destino.MENSAGEM_COPIA}
        except destino.ErroDestino as erro:
            raise ErroApi(400, str(erro)) from None
        except TimeoutError as erro:
            raise ErroApi(409, str(erro)) from None
        except OSError:
            raise ErroApi(502, "Não consegui acessar a pasta do destino. Confira e tente de novo.") from None

    def corte_para_destino(self, corpo):
        from cortelive import pastas

        video = self.dentro_dos_cortes(campo(corpo, "caminho"))
        relativos = video.relative_to(self.pasta_cortes())
        if (video.name.lower().endswith("_sem_legenda.mp4")
                or any(p.casefold() == pastas.SEM_LEGENDA.casefold() for p in relativos.parts[:-1])
                or not pastas.interno_do_video(video).resolve().is_relative_to(video.parent)):
            raise ErroApi(404, "Corte não encontrado.")
        return video

    def apagar_historico(self, corpo=None):
        """Apaga só o histórico: log.txt e sessao.json de lives já terminadas e os arquivos de logs/.
        Nunca toca em vídeos, legendas nem em sessão sem sessao.json recente (pode estar rodando)."""
        agora = time.time()
        apagados, bytes_livres, pulados = 0, 0, 0

        def remover(arq):
            nonlocal apagados, bytes_livres
            try:
                tamanho = arq.stat().st_size
                arq.unlink()
            except OSError:
                return False
            apagados += 1
            bytes_livres += tamanho
            return True

        pasta = self.pasta_cortes()
        if pasta.is_dir():
            for sessao in pasta.iterdir():
                if not sessao.is_dir() or sessao.name.startswith((".", "_")):
                    continue
                locais = [sessao / "_interno", sessao]          # formato novo e formato antigo
                logs = [p for d in locais for p in (d / "log.txt",) if p.is_file()]
                registros = [p for d in locais for p in (d / "sessao.json",) if p.is_file()]
                if not registros:
                    # sem sessao.json: ou foi parada à força ou ainda roda; só vale se o log está parado há tempo
                    if any(agora - p.stat().st_mtime < PARADA_APOS_S for p in logs):
                        pulados += 1
                        continue
                elif any(agora - p.stat().st_mtime < 60 for p in registros + logs):
                    pulados += 1                                  # acabou de fechar; deixa assentar
                    continue
                for arq in logs + registros:
                    remover(arq)
        logs_raiz = self.raiz / "logs"
        if logs_raiz.is_dir():
            for arq in logs_raiz.iterdir():
                if arq.is_file() and arq.suffix.lower() in (".log", ".txt", ".jsonl")                         and agora - arq.stat().st_mtime >= PARADA_APOS_S:
                    remover(arq)
        if not apagados:
            mensagem = "Não havia histórico para apagar. Seus cortes continuam onde estavam."
        else:
            mensagem = (f"Histórico apagado ({apagados} {'arquivo' if apagados == 1 else 'arquivos'}). "
                        "Seus cortes continuam onde estavam.")
        return {"apagados": apagados, "bytes_liberados": bytes_livres, "pulados": pulados, "mensagem": mensagem}

    def enviar_erro(self, corpo):
        """Junta logs/versão/máquina num .zip na Área de Trabalho (sem segredos, sem internet) e abre a pasta."""
        if str(self.raiz) not in sys.path:
            sys.path.insert(0, str(self.raiz))
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        try:
            import relatorio_erro
            try:
                pasta = self.pasta_cortes()
            except ErroApi:
                pasta = self.raiz / "cortes"
            r = relatorio_erro.gerar(self.raiz, pasta, corpo if isinstance(corpo, dict) else {})
        except OSError:
            raise ErroApi(502, "Não consegui salvar o arquivo na Área de Trabalho. Confira se há espaço livre.") from None
        return {**r, "mensagem": "Arquivo salvo na Área de Trabalho. Mande para o Gabriel pelo WhatsApp."}

    def responder(self, metodo, alvo, corpo):
        try:
            dados = self._rota(metodo, alvo, corpo)
            return 200, {"ok": True, **dados} if isinstance(dados, dict) else dados
        except ErroApi as erro:
            return erro.status, {"ok": False, "erro": str(erro)}
        except subprocess.TimeoutExpired:
            return 504, {"ok": False, "erro": "A operação excedeu o tempo de espera."}
        except (OSError, ValueError):
            return 502, {"ok": False, "erro": "Não foi possível concluir a operação local."}

    def _rota(self, metodo, alvo, corpo):
        url = urlsplit(alvo)
        familias = url.path.strip("/").split("/")
        if len(familias) < 2 or familias[0] != "api":
            raise ErroApi(404, "Rota não encontrada.")
        grupo, *acoes = familias[1:]
        if grupo == "dev":
            return despachar_rota_dev(self, metodo, acoes, url, corpo)
        acao = acoes[0] if len(acoes) == 1 else ""
        consultas = {("cortes", ""), ("miniatura", ""), ("drive", "detectar"), ("gpt", "status"),
                     ("foco", "estado"), ("foco", "quadro")}
        acoes_post = {("historico", "apagar"), ("enviar-erro", ""), ("abrir-video", ""), ("abrir-pasta", ""),
                      ("gpt", "conectar"), ("drive", "enviar"), ("cortes", "excluir"),
                      ("foco", "refazer"), ("foco", "automatico"), ("foco", "baixar"), ("foco", "modo")}
        par = grupo, acao
        if len(acoes) > 1 or par not in consultas | acoes_post | {("config-postagem", ""), ("drive", "config")}:
            raise ErroApi(404, "Rota não encontrada.")
        if (metodo not in ("GET", "POST") or par in consultas and metodo != "GET"
                or par in acoes_post and metodo != "POST"):
            raise ErroApi(405, "Método não permitido.")
        if grupo == "config-postagem":
            return self.postagem(metodo, corpo)
        if grupo == "gpt":
            from cortelive.gpt import conectar, FalhaGPT
            try:
                return conectar(acao, corpo, self.ambiente())
            except FalhaGPT as erro:
                raise ErroApi(400, str(erro)) from None
        if grupo == "drive":
            return self.drive(acao, metodo, corpo)
        if grupo == "foco":
            return self.foco(acao, url, corpo)
        if grupo == "cortes":
            if acao == "excluir":
                return self.excluir_corte(corpo)
            return {"cortes": self.listar_cortes()}
        if grupo == "miniatura":
            texto = parse_qs(url.query).get("caminho", [""])[0]
            return self.miniatura(self.dentro_dos_cortes(texto))
        if grupo == "historico":
            return self.apagar_historico(corpo)
        if grupo == "enviar-erro":
            return self.enviar_erro(corpo)
        texto = campo(corpo, "caminho")
        if grupo == "abrir-pasta":
            caminho = self.dentro_dos_cortes(texto, video=False)
            if caminho.is_file() and caminho.suffix.lower() == ".mp4":
                caminho = caminho.parent
            if not texto:
                caminho.mkdir(parents=True, exist_ok=True)
            if not caminho.is_dir():
                raise ErroApi(404, "Pasta não encontrada.")
        else:
            caminho = self.dentro_dos_cortes(texto)
        os.startfile(str(caminho))
        return {}


def servidor_local(api, token, porta):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def handle_one_request(self):
            self.connection.settimeout(15)
            super().handle_one_request()

        def responder(self, status, dados):
            if isinstance(dados, bytes):
                tipo, corpo = "image/jpeg", dados
            else:
                tipo = "application/json; charset=utf-8"
                corpo = json.dumps(dados, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", tipo)
            self.send_header("Content-Length", str(len(corpo)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(corpo)

        def executar(self):
            host = f"127.0.0.1:{self.server.server_port}"
            recebido = self.headers.get("X-CorteLive-Token", "")
            origem = self.headers.get("Origin", f"http://{host}")
            if (self.headers.get("Host") != host or origem != f"http://{host}" or
                    not hmac.compare_digest(recebido.encode(), token.encode())):
                self.responder(403, {"ok": False, "erro": "Sessão inválida."})
                return
            corpo = {}
            if self.command == "POST":
                try:
                    tamanho = int(self.headers.get("Content-Length", "0"))
                    if not 0 <= tamanho <= LIMITE_CORPO:
                        raise ValueError
                    if tamanho:
                        if self.headers.get_content_type() != "application/json":
                            raise ValueError
                        corpo = json.loads(self.rfile.read(tamanho))
                        if not isinstance(corpo, dict):
                            raise ValueError
                except ValueError:
                    self.responder(400, {"ok": False, "erro": "Corpo JSON inválido."})
                    return
            self.responder(*api.responder(self.command, self.path, corpo))

        do_GET = do_POST = executar

    return ThreadingHTTPServer(("127.0.0.1", porta), Handler)


def iniciar_api(argv):
    p = argparse.ArgumentParser(prog="motor_painel")
    modo = p.add_mutually_exclusive_group(required=True)
    modo.add_argument("--servir", action="store_true")
    modo.add_argument("--api-local", action="store_true")
    p.add_argument("--raiz", type=Path, default=RAIZ_PAINEL)
    p.add_argument("--porta", type=int, default=0)
    a = p.parse_args(argv)
    api = ApiPainel(a.raiz)
    if a.api_local:
        try:
            bruto = sys.stdin.buffer.read(LIMITE_CORPO + 1)
            if len(bruto) > LIMITE_CORPO:
                raise ValueError
            pedido = json.loads(bruto.decode("utf-8-sig"))
            metodo, rota, corpo = pedido["metodo"], pedido["rota"], pedido.get("corpo", {})
            if not isinstance(metodo, str) or not isinstance(rota, str) or not isinstance(corpo, dict):
                raise ValueError
            status, dados = api.responder(metodo, rota, corpo)
        except (KeyError, TypeError, ValueError):
            status, dados = 400, {"ok": False, "erro": "Corpo JSON inválido."}
        envelope = {"status": status}
        if isinstance(dados, bytes):
            envelope.update(tipo="image/jpeg", base64=base64.b64encode(dados).decode("ascii"))
        else:
            envelope["dados"] = dados
        print(json.dumps(envelope, ensure_ascii=False), flush=True)
        return 0
    token = os.environ.pop("CORTELIVE_PAINEL_TOKEN", "")
    if not token:
        p.error("O host precisa fornecer CORTELIVE_PAINEL_TOKEN.")
    with servidor_local(api, token, a.porta) as servidor:
        cancelar = threading.Event()
        print(json.dumps({"porta": servidor.server_port}), flush=True)
        try:
            servidor.serve_forever()
        except KeyboardInterrupt:
            return 0
        finally:
            cancelar.set()


def emitir(dados: dict):
    from cortelive import log as _log
    _log.escrever_linha("@@STATUS@@" + json.dumps(dados, ensure_ascii=False))


def _iter_linhas_stdin(entrada):
    if hasattr(entrada, "readline"):
        while True:
            linha = entrada.readline()
            if not linha:
                break
            yield linha
    else:
        yield from entrada


def escutar_stdin(parar_ev: threading.Event, entrada, comandos,
                  acordar_ev: threading.Event | None = None,
                  fechar_ev: threading.Event | None = None):
    parada_normal = False
    try:
        for linha in _iter_linhas_stdin(entrada):
            cmd = linha.strip().lower()
            if cmd in ("parar", "stop", "sair", "quit"):
                parada_normal = True
                return
            if cmd == "fechar":
                if fechar_ev is not None:
                    fechar_ev.set()
                return
            acao, _, ident = cmd.partition(" ")
            if acao == "adicionar-live":
                campos = ident.split()
                if len(campos) not in (1, 3):
                    continue
                try:
                    valores = tuple(bytes.fromhex(campo).decode("utf-8").strip() for campo in campos)
                except (ValueError, UnicodeDecodeError):
                    continue
                if all(v and len(v) <= 4096 and not any(c in v for c in "\r\n") for v in valores):
                    comandos.put(("adicionar", *valores))
                    if acordar_ev is not None:
                        acordar_ev.set()
                continue
            if acao == "parar-live" and len(ident) == 32 and all(c in "0123456789abcdef" for c in ident):
                comandos.put(ident)
                if acordar_ev is not None:
                    acordar_ev.set()
    finally:
        # Fechar a ponta de escrita (painel encerrado) também encerra imediatamente todas as lives.
        if not parada_normal and fechar_ev is not None:
            fechar_ev.set()
        parar_ev.set()
        if acordar_ev is not None:
            acordar_ev.set()


def iniciar_escuta(parar_ev: threading.Event, comandos,
                   acordar_ev: threading.Event | None = None,
                   fechar_ev: threading.Event | None = None):
    entrada = sys.stdin
    if entrada is None:
        return
    if os.name == "nt" and not entrada.isatty():
        # A leitura simultânea do pipe herdado bloqueia o Python do render no Windows.
        # Só a thread do painel lê a cópia. Os filhos recebem NUL como stdin.
        entrada = os.fdopen(os.dup(sys.stdin.fileno()), encoding="utf-8", errors="replace")
        with open(os.devnull, "rb") as nulo:
            os.dup2(nulo.fileno(), sys.stdin.fileno())
    threading.Thread(
        target=escutar_stdin,
        args=(parar_ev, entrada, comandos, acordar_ev, fechar_ev),
        daemon=True,
    ).start()


def status_servicos(modo_resumo: str):
    from cortelive import servicos
    tr_st, rd_st = {}, {}
    try:
        tr_st = servicos.transcricao().status()
    except Exception:
        pass
    try:
        rd_st = servicos.render().status()
    except Exception:
        pass
    return {"modo": modo_resumo, "transcricao": tr_st, "render": rd_st}


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "--dev-api":
        sys.path.insert(0, str(RAIZ_PAINEL))
        return iniciar_dev_cli(argv[1:])
    if argv and argv[0] in ("--servir", "--api-local"):
        sys.path.insert(0, str(RAIZ_PAINEL))
        return iniciar_api(argv)

    p = argparse.ArgumentParser(prog="motor_painel")
    p.add_argument("fontes", nargs="+", help="URLs de live ou caminhos de vídeo do PC")
    p.add_argument("--modelo", default="cam-em-cima")
    p.add_argument("--legenda", default="opus")
    p.add_argument("--saida", default=None)
    p.add_argument("--duracao-min", type=float, default=None)
    p.add_argument("--duracao-max", type=float, default=None)
    p.add_argument("--minutos", type=float, default=None)
    p.add_argument("--sem-chat", action="store_true")
    p.add_argument("--gpu", choices=["nvidia", "amd", "intel", "cpu"], default=None)
    p.add_argument("--encoder", default=None)
    p.add_argument("--fala", default=None)
    p.add_argument("--modelo-whisper", default=None)
    p.add_argument("--detalhado", action="store_true")
    a = p.parse_args(argv)

    from cortelive import cli, hw, render, servicos
    from cortelive.processos import conter_processos
    from cortelive import multi

    try:
        cli.checar_ambiente()
        cfg = cli.carregar_config(a)
        postagem = config_postagem(Path.cwd())
        cfg.update(postagem)
        for tipo, nome in (("modelo", cfg["modelo"]), ("legenda", cfg["legenda"])):
            if nome not in render.listar(tipo):
                raise cli.ErroAmigavel(f"{tipo} '{nome}' não existe. Opções: {', '.join(render.listar(tipo))}")
    except BaseException as e:
        emitir({"estado": "erro", "erro": str(e), "lives": []})
        print(f"ERRO: {e}", file=sys.stderr, flush=True)
        return 1

    urls, arquivos = [], []
    for f in a.fontes:
        f_limpo = f.strip().strip('"')
        if not f_limpo:
            continue
        caminho = Path(f_limpo)
        if caminho.exists():
            arquivos.append(str(caminho.resolve()))
        elif (Path.cwd() / f_limpo).exists():
            arquivos.append(str((Path.cwd() / f_limpo).resolve()))
        elif "://" in f_limpo or "." in f_limpo:
            urls.append(f_limpo)
        else:
            msg = f"Link ou arquivo não encontrado: {f_limpo}"
            emitir({"estado": "erro", "erro": msg, "lives": []})
            print(f"ERRO: {msg}", file=sys.stderr, flush=True)
            return 1

    if not urls and not arquivos:
        emitir({"estado": "erro", "erro": "Nenhum link de live ou vídeo informado.", "lives": []})
        return 1

    if a.encoder:
        os.environ["CORTELIVE_ENCODER"] = a.encoder

    conter_processos()
    parar_ev = threading.Event()
    acordar_ev = threading.Event()
    fechar_ev = threading.Event()
    comandos = queue.Queue()
    iniciar_escuta(parar_ev, comandos, acordar_ev, fechar_ev)

    # Identificadores estáveis gerados já na largada para que cada live apareça no painel com botão Parar imediato.
    entradas_iniciais = []
    vistos_fontes = set()
    canais_usados = set()
    for arq in arquivos:
        if arq in vistos_fontes:
            continue
        vistos_fontes.add(arq)
        canal = multi.nome_canal(arq, True)
        base, n = canal, 2
        while canal in canais_usados:
            canal, n = f"{base}_{n}", n + 1
        canais_usados.add(canal)
        entradas_iniciais.append((uuid.uuid4().hex, arq, True, canal))
    for u in urls:
        if u in vistos_fontes:
            continue
        vistos_fontes.add(u)
        canal = multi.nome_canal(u, False)
        base, n = canal, 2
        while canal in canais_usados:
            canal, n = f"{base}_{n}", n + 1
        canais_usados.add(canal)
        entradas_iniciais.append((uuid.uuid4().hex, u, False, canal))

    ids_por_fonte = {fonte: ident for ident, fonte, _, _ in entradas_iniciais}
    lives_largada = [
        {
            "id": ident, "canal": canal, "fonte": fonte, "estado": "iniciando",
            "pode_parar": True, "cortes": 0, "cortes_descartados_parada": 0,
            "live_s": 0.0, "atraso_transcricao_s": None, "aviso": None,
        }
        for ident, fonte, _, canal in entradas_iniciais
    ]
    emitir({
        "estado": "iniciando",
        "mensagem": "Detectando placa de vídeo e preparando motor de voz…",
        "lives": lives_largada,
    })
    modo = hw.detectar(a.encoder, a.fala, a.modelo_whisper, None, a.gpu)
    modo_resumo = modo.resumo()
    servicos.configurar(cfg, modo)

    paradas_antes = set()
    adicoes_antes = []
    while not comandos.empty():
        c = comandos.get_nowait()
        if isinstance(c, tuple):
            adicoes_antes.append(c)
        else:
            paradas_antes.add(c)

    if parar_ev.is_set() or len(paradas_antes) >= len(entradas_iniciais):
        for item in lives_largada:
            item["estado"] = "parada"
            item["pode_parar"] = False
        emitir({"estado": "encerrada", "lives": lives_largada, "modo": modo_resumo})
        return 0

    arquivos_ativos = [f for ident, f, eh_arq, _ in entradas_iniciais if eh_arq and ident not in paradas_antes]
    urls_ativas = [f for ident, f, eh_arq, _ in entradas_iniciais if not eh_arq and ident not in paradas_antes]
    unica_cel = [len(entradas_iniciais) == 1]
    unica = unica_cel[0]
    ns = types.SimpleNamespace(
        minutos=a.minutos, detalhado=a.detalhado,
        duracao_min=a.duracao_min, duracao_max=a.duracao_max,
        modelo=a.modelo, legenda=a.legenda, saida=a.saida, sem_chat=a.sem_chat,
    )

    def criar_sessao_painel(*args):
        sessao = multi.criar_sessao(*args)
        # le de novo a cada live (inclusive as adicionadas com a sessao ja rodando): ligar "cortes de 1 min" vale
        # para a proxima live iniciada, sem reabrir o app
        sessao.cfg_extra.update(config_postagem(Path.cwd()))
        sessao.cfg_extra["recuo_sem_cam"] = []
        sessao.terminal = unica_cel[0]
        return sessao

    sup = multi.Supervisor(urls_ativas, arquivos_ativos, None, ns, painel=False,
                           repetir=not unica, criar=criar_sessao_painel)
    # Preserva no painel os cartões de lives paradas ainda durante o aquecimento inicial
    for ident, fonte, eh_arq, canal in entradas_iniciais:
        if ident in paradas_antes and fonte not in sup.lives:
            lv_parada = multi.Live(fonte, eh_arq, canal, ns, criar_sessao_painel)
            lv_parada.encerrar = True
            lv_parada.terminou = True
            sup.lives[fonte] = lv_parada
    return acompanhar_multiplas(
        sup, parar_ev, modo_resumo, comandos, unica=unica,
        acordar_ev=acordar_ev, fechar_ev=fechar_ev, ids_por_fonte=ids_por_fonte,
        adicoes_iniciais=adicoes_antes, unica_cel=unica_cel,
    )


def _aviso_descarte(qtd: int) -> str | None:
    if qtd <= 0:
        return None
    if qtd == 1:
        return "1 corte em andamento foi descartado ao parar."
    return f"{qtd} cortes em andamento foram descartados ao parar."


def _tempo_atras(seg: float) -> str:
    if seg < 45:
        return "agora mesmo"
    if seg < 3600:
        return f"há {max(1, round(seg / 60))} min"
    horas = int(seg // 3600)
    mins = int((seg % 3600) // 60)
    return f"há {horas} h" if mins == 0 else f"há {horas}h{mins:02d}"


def _atraso_voz_sessao(ativas: list[dict]) -> tuple[float | None, str | None]:
    ao_vivo = [st for st in ativas if st.get("estado") in ("ao vivo", "gravando")]
    com_atraso = [
        (float(st["atraso_transcricao_s"]), str(st.get("canal") or ""))
        for st in ao_vivo
        if st.get("atraso_transcricao_s") is not None
    ]
    if not com_atraso:
        return None, None
    maior_s, maior_canal = max(com_atraso, key=lambda item: item[0])
    menor_s = min(item[0] for item in com_atraso)
    mostrar_canal = (
        len(ao_vivo) >= 2
        and bool(maior_canal)
        and round(maior_s) > 0
        and (len(com_atraso) < len(ao_vivo) or round(maior_s) != round(menor_s))
    )
    return round(maior_s, 1), (maior_canal if mostrar_canal else None)


def _resumo_barra_sessao(dados: list[dict], estado: str, ultimo_corte_ha_s: float | None) -> str:
    ativas = [st for st in dados if st.get("pode_parar")]
    ao_vivo = [st for st in ativas if st.get("estado") in ("ao vivo", "gravando")]
    total_cortes = sum(int(st.get("cortes") or 0) for st in dados)

    def _detalhe_multi() -> str:
        if ultimo_corte_ha_s is not None:
            return f"último corte {_tempo_atras(ultimo_corte_ha_s)}"
        em_corte = next(
            (st for st in ao_vivo if any(k in str(st.get("atividade") or "").lower()
                                         for k in ("cortando", "fazendo o corte", "começando a cortar"))),
            None,
        )
        if em_corte and total_cortes > 0:
            plural = "corte pronto" if total_cortes == 1 else "cortes prontos"
            return f"{total_cortes} {plural} · cortando em {em_corte.get('canal') or 'live'}"
        if em_corte:
            return f"cortando em {em_corte.get('canal') or 'live'}"
        if total_cortes > 0:
            return f"{total_cortes} {'corte pronto' if total_cortes == 1 else 'cortes prontos'}"
        return "ouvindo em tempo real"

    if len(ao_vivo) >= 2:
        esperando = max(0, len(ativas) - len(ao_vivo))
        prefixo = f"Acompanhando {len(ao_vivo)} lives" + (f" ({esperando} aguardando)" if esperando > 0 else "")
        return f"{prefixo} · {_detalhe_multi()}"
    if len(ao_vivo) == 1:
        unica_lv = ao_vivo[0]
        canal = unica_lv.get("canal") or "live"
        esperando = max(0, len(ativas) - 1)
        prefixo = f"Acompanhando {canal}" + (f" ({esperando} aguardando)" if esperando > 0 else "")
        if ultimo_corte_ha_s is not None:
            detalhe = f"último corte {_tempo_atras(ultimo_corte_ha_s)}"
        elif unica_lv.get("atividade"):
            detalhe = str(unica_lv["atividade"])
        else:
            detalhe = _detalhe_multi()
        return f"{prefixo} · {detalhe}"
    if estado in ("encerrando", "encerrada"):
        return "Parando as lives e finalizando cortes em andamento…"
    if ativas and all(st.get("estado") == "esperando live" for st in ativas):
        if len(ativas) >= 2:
            return f"Aguardando {len(ativas)} lives entrarem ao vivo…"
        return str(ativas[0].get("mensagem") or f"Aguardando {ativas[0].get('canal') or 'a live'} entrar ao vivo…")
    if ativas and any(st.get("estado") == "reconectando" for st in ativas):
        if len(ativas) >= 2:
            return "Reconectando às lives…"
        return str(ativas[0].get("mensagem") or "A live caiu — tentando voltar sozinho…")
    if len(ativas) >= 2:
        return f"Preparando {len(ativas)} lives e modelo de voz…"
    return "Iniciando captura e modelo de voz…"


def acompanhar_multiplas(sup, parar_ev, modo_resumo, comandos, unica=False,
                         acordar_ev: threading.Event | None = None,
                         fechar_ev: threading.Event | None = None,
                         ids_por_fonte: dict | None = None,
                         adicoes_iniciais=None, unica_cel=None):
    lives = {
        (ids_por_fonte or {}).get(fonte) or uuid.uuid4().hex: lv
        for fonte, lv in sup.lives.items()
    }
    ultimo_corte_em = 0.0
    cortes_por_id: dict[str, int] = {}

    def adicionar_live(fonte, modelo=None, legenda=None):
        """Entra mais uma live na sessão em andamento (mesmo caminho da largada)."""
        existente = sup.lives.get(fonte)
        if existente is not None and not existente.encerrar:
            return
        eh_arq = Path(fonte).exists()
        unica_ao_largar = unica_cel is not None and unica_cel[0]
        if unica_cel is not None:
            unica_cel[0] = False
        sup.repetir = True
        if unica_ao_largar:
            for lv0 in lives.values():       # a 1ª live deixa de ser "única": passa a repetir se cair
                if lv0.s is not None:
                    lv0.s.terminal = False
        chave = str(Path(fonte).resolve()) if eh_arq else fonte
        args = None
        if modelo is not None:
            args = types.SimpleNamespace(**{**vars(sup.a), "modelo": modelo, "legenda": legenda})
        sup.adicionar(chave, arquivo=eh_arq, args=args)
        lv = sup.lives.get(chave)
        if lv is not None and all(lv is not x for x in lives.values()):
            lives[uuid.uuid4().hex] = lv

    for _c in (adicoes_iniciais or []):
        adicionar_live(*_c[1:])

    def estados():
        dados = []
        for ident, lv in lives.items():
            st = dict(lv.st())
            encerrando = lv.encerrar
            if encerrando:
                st["estado"] = "parada" if lv.terminou else "encerrando"
            elif not lv.terminou and lv.proximo_inicio is not None:
                st["estado"] = "esperando live"
            desc = lv.cortes_descartados() if hasattr(lv, "cortes_descartados") else (st.get("cortes_descartados_parada") or 0)
            st.update(
                id=ident,
                canal=lv.canal,
                fonte=lv.fonte,
                cortes=lv.cortes(),
                cortes_descartados_parada=desc,
                aviso=_aviso_descarte(desc),
                pode_parar=not encerrando and not lv.terminou,
            )
            dados.append(st)
        return dados

    try:
        while not parar_ev.is_set():
            if acordar_ev is not None:
                acordar_ev.clear()
            while not comandos.empty():
                cmd = comandos.get_nowait()
                if isinstance(cmd, tuple):
                    adicionar_live(*cmd[1:])
                    continue
                lv = lives.get(cmd)
                if lv and not lv.terminou and not lv.encerrar:
                    lv.parar(descartar_cortes=True)
            sup.vigiar()
            dados = estados()
            agora = time.time()
            for lv in lives.values():
                pronto_s = getattr(getattr(lv, "s", None), "_pronto_em", None)
                if isinstance(pronto_s, tuple) and pronto_s and pronto_s[0] > ultimo_corte_em:
                    ultimo_corte_em = float(pronto_s[0])
            for st in dados:
                ident = st["id"]
                c_atual = int(st.get("cortes") or 0)
                c_ant = cortes_por_id.get(ident)
                if c_ant is not None and c_atual > c_ant and ultimo_corte_em < agora - 1.0:
                    ultimo_corte_em = agora
                cortes_por_id[ident] = c_atual

            ativas = [st for st in dados if st["pode_parar"]]
            pendentes = [lv for lv in lives.values() if not lv.terminou]
            estado = ("ao vivo" if any(st.get("estado") in ("ao vivo", "gravando") for st in ativas)
                      else ativas[0].get("estado") if ativas
                      else "encerrando" if pendentes else "encerrada")
            ultimo_corte_ha_s = round(max(0.0, agora - ultimo_corte_em), 1) if ultimo_corte_em > 0 else None
            atraso_s, atraso_canal = _atraso_voz_sessao(ativas)
            avisos_disco = [st["aviso_disco"] for st in dados if st.get("aviso_disco")]
            mensagem = avisos_disco[0] if avisos_disco else _resumo_barra_sessao(dados, estado, ultimo_corte_ha_s)
            try:   # PC com lives demais: aviso claro com N estimado pelo desempenho medido
                from cortelive import servicos as _sv
                aviso_cap = _sv.transcricao().aviso_capacidade(len(ativas)) if ativas else ""
            except Exception:
                aviso_cap = ""
            if aviso_cap and not avisos_disco:
                mensagem = f"{aviso_cap} · {mensagem}"
            emitir({
                "estado": estado,
                "mensagem": mensagem,
                "atraso_s": atraso_s,
                "atraso_canal": atraso_canal,
                "ultimo_corte_ha_s": ultimo_corte_ha_s,
                "lives": dados,
                **status_servicos(modo_resumo),
            })
            if not pendentes:
                break
            if acordar_ev is not None:
                acordar_ev.wait(0.4)
            else:
                parar_ev.wait(0.4)
    except KeyboardInterrupt:
        parar_ev.set()
    finally:
        descartar_tudo = fechar_ev is not None and fechar_ev.is_set()
        if parar_ev.is_set():
            for lv in lives.values():
                if not lv.terminou:
                    lv.parar(descartar_cortes=descartar_tudo)
            if not descartar_tudo:
                emitir({"estado": "encerrando", "lives": estados(),
                        "mensagem": "Parando as lives e finalizando cortes em andamento…"})
        sup.fechar(descartar_cortes=descartar_tudo, silencioso=True)

    dados = estados()
    unica = unica and (unica_cel is None or unica_cel[0])
    erro = dados[0].get("motivo_fim") if unica and dados[0].get("estado") == "erro" else None
    disco_parou = next((st["mensagem"] for st in dados if st.get("aviso_disco") and "Disco quase lotado" in str(st.get("mensagem"))), None)
    emitir({"estado": "erro" if erro else "encerrada", "erro": erro, "lives": dados, "modo": modo_resumo,
            **({"mensagem": disco_parou} if disco_parou else {})})
    return int(bool(erro))


# =============================================================================
# Rotas exclusivas do CorteLive Dev (ativas apenas quando CORTELIVE_MODO_DEV=1)
# =============================================================================
def _gravar_texto_atomico(alvo: Path, conteudo: str):
    alvo.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", newline="\n",
        dir=alvo.parent, prefix=".dev-cfg-", delete=False,
    ) as tmp:
        temporario = Path(tmp.name)
        try:
            tmp.write(conteudo)
            tmp.close()
            temporario.replace(alvo)
        finally:
            temporario.unlink(missing_ok=True)


def _caminhos_configs_dev(raiz: Path):
    return {
        "cortelive": (raiz / "cortelive" / "config.toml").resolve(),
        "raiz": (raiz / "config.toml").resolve(),
    }


def dev_resumo(raiz: Path):
    from cortelive.dados_local import pasta_contas, pasta_dados_local

    raiz = raiz.resolve()
    pasta_tv = raiz / "teste_video"
    videos_teste = []
    if pasta_tv.is_dir():
        for ext in ("*.mp4", "*.mkv", "*.mov", "*.webm"):
            for arq in sorted(pasta_tv.glob(ext)):
                if arq.is_file():
                    try:
                        mb = round(arq.stat().st_size / (1024 * 1024), 1)
                    except OSError:
                        mb = 0.0
                    videos_teste.append({
                        "nome": arq.name,
                        "caminho": str(arq.resolve()),
                        "tamanho_mb": mb,
                    })

    versao = "0.1.0"
    arq_ver = raiz / "VERSION"
    if arq_ver.is_file():
        try:
            versao = arq_ver.read_text(encoding="utf-8-sig").strip().lstrip("vV") or versao
        except OSError:
            pass

    git_branch = ""
    git_ultimo_tag = ""
    if (raiz / ".git").exists():
        try:
            r_br = subprocess.run(
                ["git", "-C", str(raiz), "rev-parse", "--abbrev-ref", "HEAD"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=4,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if r_br.returncode == 0:
                git_branch = r_br.stdout.strip()
            r_tg = subprocess.run(
                ["git", "-C", str(raiz), "describe", "--tags", "--abbrev=0"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=4,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if r_tg.returncode == 0:
                git_ultimo_tag = r_tg.stdout.strip()
        except Exception:
            pass

    caminhos_cfg = _caminhos_configs_dev(raiz)
    return {
        "versao": versao,
        "git_branch": git_branch,
        "git_ultimo_tag": git_ultimo_tag,
        "videos_teste": videos_teste,
        "lancar_versao_existe": (raiz / "lancar_versao.cmd").is_file(),
        "pastas": {
            "cortes": str((raiz / "cortes").resolve()),
            "logs": str((raiz / "logs").resolve()),
            "config": str((raiz / "cortelive").resolve()),
            "dados_local": str(pasta_dados_local()),
            "contas": str(pasta_contas()),
        },
        "arquivos_config": {
            "cortelive": str(caminhos_cfg["cortelive"]),
            "raiz": str(caminhos_cfg["raiz"]),
        },
    }


def dev_ler_configs(raiz: Path):
    caminhos = _caminhos_configs_dev(raiz.resolve())
    txt_cortelive = ""
    if caminhos["cortelive"].is_file():
        txt_cortelive = caminhos["cortelive"].read_text(encoding="utf-8-sig")

    txt_raiz = ""
    raiz_existe = caminhos["raiz"].is_file()
    if raiz_existe:
        txt_raiz = caminhos["raiz"].read_text(encoding="utf-8-sig")

    return {
        "configs": {
            "cortelive": {
                "caminho": str(caminhos["cortelive"]),
                "existe": caminhos["cortelive"].is_file(),
                "conteudo": txt_cortelive,
            },
            "raiz": {
                "caminho": str(caminhos["raiz"]),
                "existe": raiz_existe,
                "conteudo": txt_raiz,
            },
        }
    }


def dev_salvar_config(raiz: Path, corpo: dict):
    alvo = campo(corpo, "alvo").strip().lower()
    conteudo = campo(corpo, "conteudo")
    if alvo not in ("cortelive", "raiz"):
        raise ErroApi(400, "Alvo de configuração inválido. Use 'cortelive' ou 'raiz'.")

    try:
        tomllib.loads(conteudo) if conteudo.strip() else {}
    except tomllib.TOMLDecodeError as exc:
        raise ErroApi(400, f"Erro de sintaxe TOML: {exc}") from None

    caminhos = _caminhos_configs_dev(raiz.resolve())
    caminho_final = caminhos[alvo]
    texto_final = conteudo if (not conteudo or conteudo.endswith("\n")) else (conteudo + "\n")
    _gravar_texto_atomico(caminho_final, texto_final)
    return {
        "alvo": alvo,
        "caminho": str(caminho_final),
        "mensagem": f"Configuração salva com sucesso em {caminho_final.name}.",
    }


def despachar_rota_dev(api_painel: ApiPainel, metodo: str, acoes: list[str], url, corpo: dict):
    if os.environ.get("CORTELIVE_MODO_DEV") != "1":
        raise ErroApi(404, "Rota não encontrada.")
    acao = acoes[0] if len(acoes) == 1 else ""
    if metodo == "GET" and acao == "resumo":
        return dev_resumo(api_painel.raiz)
    if metodo == "GET" and acao == "configs":
        return dev_ler_configs(api_painel.raiz)
    if metodo == "POST" and acao == "configs":
        return dev_salvar_config(api_painel.raiz, corpo)
    raise ErroApi(404, "Rota Dev não encontrada.")


def iniciar_dev_cli(argv: list[str]):
    p = argparse.ArgumentParser(prog="motor_painel --dev-api")
    p.add_argument("acao", choices=("resumo", "configs", "salvar-config"))
    p.add_argument("--raiz", type=Path, default=RAIZ_PAINEL)
    a = p.parse_args(argv)
    try:
        if a.acao == "resumo":
            dados = dev_resumo(a.raiz)
        elif a.acao == "configs":
            dados = dev_ler_configs(a.raiz)
        elif a.acao == "salvar-config":
            bruto = sys.stdin.buffer.read(LIMITE_CORPO * 4)
            corpo = json.loads(bruto.decode("utf-8-sig")) if bruto else {}
            dados = dev_salvar_config(a.raiz, corpo)
        else:
            raise ErroApi(400, "Ação desconhecida.")
        print(json.dumps({"ok": True, **dados}, ensure_ascii=False), flush=True)
        return 0
    except ErroApi as erro:
        print(json.dumps({"ok": False, "status": erro.status, "erro": str(erro)}, ensure_ascii=False), flush=True)
        return 1
    except Exception as exc:
        print(json.dumps({"ok": False, "status": 500, "erro": str(exc)}, ensure_ascii=False), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
