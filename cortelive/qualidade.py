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


class Filtro:
    def __init__(self, cfg):
        self.nivel = nivel(cfg)
        n = NIVEIS[self.nivel]
        self.piso = {"gpt": n["gpt"], "local": n["local"]}
        self.por_hora = n["por_hora"]
        self.por_janela = max(1, round(self.por_hora / 4))
        self.aceitos = []                # tempo na live dos cortes que saíram

    def avaliar(self, nota, juiz, t):
        """-> (aprovado, motivo). juiz = "gpt" ou "local"; t = tempo do momento na live (s)."""
        juiz = "gpt" if juiz == "gpt" else "local"
        piso = self.piso[juiz]
        if nota < piso:
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
