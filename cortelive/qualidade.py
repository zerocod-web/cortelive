"""Filtro de quantidade e qualidade dos cortes, igual para o GPT e para o seletor local.

Corte abaixo do piso de nota não é gerado (nem renderizado). Acima do piso, um teto de cortes por hora de live,
espalhado em janelas de 15 min, deixa sair só os melhores: com a janela cheia, só um corte excelente passa.
Escalas: GPT dá nota de 0 a 10; o seletor local soma sinais (volume, risada, palavras, chat; limiar padrão 2,2).
'quantidade_cortes' vem do painel ("melhores" | "equilibrado" | "mais").
"""

NIVEIS = {
    "melhores":    {"gpt": 8, "local": 3.0, "por_hora": 4},
    "equilibrado": {"gpt": 7, "local": 2.5, "por_hora": 8},
    "mais":        {"gpt": 6, "local": 0.0, "por_hora": 20},     # nota 6 é o mínimo absoluto (Gabriel, 19/11)
}
PADRAO = "equilibrado"
# nota de "Só os melhores": passa mesmo com a janela de 15 min cheia (nunca acima do teto por hora)
EXCELENTE = {"gpt": NIVEIS["melhores"]["gpt"], "local": NIVEIS["melhores"]["local"]}
NOMES = {"melhores": "Só os melhores", "equilibrado": "Equilibrado", "mais": "Mais cortes"}
JANELA_CURTA_S = 900.0


def nivel(cfg):
    n = str(cfg.get("quantidade_cortes", PADRAO)).strip().lower()
    return n if n in NIVEIS else PADRAO


def sugestao_apagar(nv):
    """Valor que a opção Quantidade de cortes sugere para 'apagar cortes com nota X ou menos'."""
    return NIVEIS[nv if nv in NIVEIS else PADRAO]["gpt"] - 1


def apagar_ate(cfg):
    """'Apagar cortes com nota X ou menos' (0 a 10, GPT): o número das Configurações; sem ele, a sugestão do nível."""
    x = cfg.get("apagar_ate_nota")
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not 0 <= x <= 10:
        return sugestao_apagar(nivel(cfg))
    return int(x)


def faixas_validas(faixas):
    """[{de, ate, pasta}] com 0 <= de <= ate <= 10 e pasta texto (vazio = pasta de sempre)."""
    if not isinstance(faixas, list) or len(faixas) > 10:
        return False
    for f in faixas:
        if not isinstance(f, dict) or set(f) - {"de", "ate", "pasta"}:
            return False
        de, ate, pasta = f.get("de"), f.get("ate"), f.get("pasta", "")
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in (de, ate)) or not 0 <= de <= ate <= 10:
            return False
        if not isinstance(pasta, str) or "\0" in pasta or len(pasta) > 1024:
            return False
    return True


def pasta_da_faixa(nota, cfg):
    """Pasta escolhida para a faixa em que a nota (0 a 10) cai; "" = pasta de sempre (sem faixa ou sem pasta)."""
    faixas = cfg.get("faixas_nota") or []
    if nota is None or not faixas_validas(faixas):
        return ""
    for f in faixas:
        if f["de"] <= nota <= f["ate"]:
            return (f.get("pasta") or "").strip()
    return ""


def enviar_para_faixa(video, nota, cfg):
    """Põe o corte pronto na pasta da sua faixa: <pasta>/<data hora - canal>/<corte>.mp4.
    O original continua na pasta do CorteLive, para seguir na lista do painel. -> caminho ou None."""
    import shutil
    from pathlib import Path
    from .destino import _copiar_arquivo, subpasta_da_live
    pasta = pasta_da_faixa(nota, cfg)
    if not pasta:
        return None
    raiz = Path(pasta)
    if not raiz.is_absolute():
        raise OSError(f"a pasta da faixa precisa do caminho completo: {pasta}")
    video = Path(video)
    alvo_dir = raiz / subpasta_da_live(video)
    alvo = alvo_dir / video.name
    if alvo.resolve() == video.resolve():
        return alvo
    alvo_dir.mkdir(parents=True, exist_ok=True)
    _copiar_arquivo(alvo, lambda tmp: shutil.copyfile(video, tmp))
    return alvo


class Filtro:
    def __init__(self, cfg):
        self.nivel = nivel(cfg)
        n = NIVEIS[self.nivel]
        self.piso = {"gpt": apagar_ate(cfg) + 1, "local": n["local"]}
        self.por_hora = n["por_hora"]
        self.por_janela = max(1, round(self.por_hora / 4))
        self.aceitos = []                # tempo na live dos cortes que saíram

    def avaliar(self, nota, juiz, t):
        """-> (aprovado, motivo). juiz = "gpt" ou "local"; t = tempo do momento na live (s)."""
        juiz = "gpt" if juiz == "gpt" else "local"
        piso = self.piso[juiz]
        if nota < piso:
            if juiz == "gpt":
                return False, f"nota {nota:g}: apagar cortes com nota {piso - 1:g} ou menos"
            return False, f"nota {nota:.1f} abaixo do piso {piso:g} ({NOMES[self.nivel]})"
        hora = sum(1 for x in self.aceitos if t - 3600.0 < x <= t)
        if hora >= self.por_hora:
            return False, f"limite de {self.por_hora} cortes por hora de live ({NOMES[self.nivel]})"
        curta = sum(1 for x in self.aceitos if t - JANELA_CURTA_S < x <= t)
        if curta >= self.por_janela and nota < EXCELENTE[juiz]:
            return False, (f"limite de {self.por_janela} cortes a cada 15 min ({NOMES[self.nivel]}); "
                           f"só nota {EXCELENTE[juiz]:g}+ passa")
        return True, None

    def registrar(self, t):
        self.aceitos.append(float(t))
