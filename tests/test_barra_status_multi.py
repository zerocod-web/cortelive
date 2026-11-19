import json
from pathlib import Path
from queue import Queue
import subprocess
from threading import Event
import time
import types

from cortelive import multi
from painel import motor_painel
from painel.motor_painel import acompanhar_multiplas


class _SessaoMultiSimulada:
    def __init__(self, canal, estado="ao vivo", cortes=0, live_s=600.0, atraso_s=5.0, pronto_em=0.0, atividade=""):
        self.canal = canal
        self.cfg_extra = {}
        self.terminal = False
        self._fim_ev = Event()
        self.estado = estado
        self.cortes = cortes
        self.live_s = live_s
        self.atraso_s = atraso_s
        self._pronto_em = (pronto_em, "Corte teste") if pronto_em else (0.0, "")
        self._atividade = atividade
        self.resumo = None
        self.erro = None

    def iniciar(self):
        return self

    def parar(self, descartar_cortes=False):
        self.estado = "encerrada"
        self.resumo = {"cortes": self.cortes, "motivo_fim": "parado pelo usuário"}
        self._fim_ev.set()

    def aguardar(self, timeout=None):
        self._fim_ev.wait(timeout)
        return self.resumo

    def status(self):
        return {
            "canal": self.canal,
            "pasta": f"C:/tmp/{self.canal}",
            "estado": self.estado,
            "cortes": self.cortes,
            "cortes_descartados_parada": 0,
            "live_s": self.live_s if self.estado in ("ao vivo", "gravando") else 0.0,
            "atraso_transcricao_s": self.atraso_s,
            "atividade": self._atividade,
            "aviso_disco": None,
            "mensagem": "",
            "motivo_fim": (self.resumo or {}).get("motivo_fim"),
        }


def test_motor_painel_emite_mensagem_atraso_e_ultimo_corte_com_varias_lives(monkeypatch):
    agora = time.time()
    sessoes = {
        "gaules": _SessaoMultiSimulada(
            "gaules", estado="ao vivo", cortes=2, live_s=1260.0, atraso_s=11.8,
            pronto_em=agora - 65.0, atividade="Fazendo o corte 3: legenda 62%"
        ),
        "ovotz": _SessaoMultiSimulada(
            "ovotz", estado="ao vivo", cortes=1, live_s=840.0, atraso_s=4.1,
            pronto_em=agora - 420.0, atividade="Ouvindo a fala (atraso 4 s)"
        ),
        "alanzoka": _SessaoMultiSimulada(
            "alanzoka", estado="esperando live", cortes=0, live_s=0.0, atraso_s=0.0,
            pronto_em=0.0, atividade="Esperando a live começar"
        ),
    }

    def criar_falsa(fonte, arquivo, canal, a):
        return sessoes[canal]

    emitidos = []
    parar_ev, acordar_ev, fechar_ev = Event(), Event(), Event()
    comandos = Queue()
    monkeypatch.setattr(motor_painel, "status_servicos", lambda m: {"modo": m, "transcricao": {}, "render": {}})

    def capturar(dados):
        emitidos.append(json.loads(json.dumps(dados)))
        if len(emitidos) == 1:
            parar_ev.set()
            acordar_ev.set()

    monkeypatch.setattr(motor_painel, "emitir", capturar)
    ns = types.SimpleNamespace(
        minutos=None, detalhado=False, duracao_min=30, duracao_max=60,
        modelo="cam-em-cima", legenda="opus", saida=None, sem_chat=True,
    )
    sup = multi.Supervisor(
        ["https://twitch.tv/gaules", "https://twitch.tv/ovotz", "https://twitch.tv/alanzoka"],
        [], None, ns, painel=False, repetir=True, criar=criar_falsa,
    )
    rc = acompanhar_multiplas(sup, parar_ev, "NVIDIA teste", comandos, unica=False,
                              acordar_ev=acordar_ev, fechar_ev=fechar_ev)
    assert rc == 0
    vivo = emitidos[0]
    assert vivo["estado"] == "ao vivo"
    assert vivo["atraso_s"] == 11.8
    assert vivo["atraso_canal"] == "gaules"
    assert 55.0 <= vivo["ultimo_corte_ha_s"] <= 80.0
    assert "Acompanhando 2 lives" in vivo["mensagem"]
    assert "1 aguardando" in vivo["mensagem"]
    assert "último corte há 1 min" in vivo["mensagem"]


def test_motor_painel_duas_lives_detecta_novo_corte_em_tempo_real(monkeypatch):
    sessoes = {
        "gaules": _SessaoMultiSimulada("gaules", estado="ao vivo", cortes=0, live_s=300.0, atraso_s=4.0),
        "ovotz": _SessaoMultiSimulada("ovotz", estado="ao vivo", cortes=0, live_s=280.0, atraso_s=4.2),
    }

    def criar_falsa(fonte, arquivo, canal, a):
        return sessoes[canal]

    emitidos = []
    parar_ev, acordar_ev, fechar_ev = Event(), Event(), Event()
    comandos = Queue()
    monkeypatch.setattr(motor_painel, "status_servicos", lambda m: {"modo": m, "transcricao": {}, "render": {}})

    def capturar(dados):
        emitidos.append(json.loads(json.dumps(dados)))
        if len(emitidos) == 1:
            sessoes["gaules"].cortes = 1
            sessoes["gaules"].atraso_s = 9.3
            acordar_ev.set()
        elif len(emitidos) >= 2:
            parar_ev.set()
            acordar_ev.set()

    monkeypatch.setattr(motor_painel, "emitir", capturar)
    ns = types.SimpleNamespace(
        minutos=None, detalhado=False, duracao_min=30, duracao_max=60,
        modelo="cam-em-cima", legenda="opus", saida=None, sem_chat=True,
    )
    sup = multi.Supervisor(
        ["https://twitch.tv/gaules", "https://twitch.tv/ovotz"],
        [], None, ns, painel=False, repetir=True, criar=criar_falsa,
    )
    rc = acompanhar_multiplas(sup, parar_ev, "NVIDIA teste", comandos, unica=False,
                              acordar_ev=acordar_ev, fechar_ev=fechar_ev)
    assert rc == 0
    antes, depois = emitidos[0], emitidos[1]
    assert antes["mensagem"] == "Acompanhando 2 lives · ouvindo em tempo real"
    assert antes["atraso_canal"] is None  # ambos arredondam para 4 s
    assert depois["mensagem"] == "Acompanhando 2 lives · último corte agora mesmo"
    assert depois["atraso_s"] == 9.3
    assert depois["atraso_canal"] == "gaules"
    assert depois["ultimo_corte_ha_s"] is not None and depois["ultimo_corte_ha_s"] < 5.0


def test_app_js_barra_topo_reflete_estado_real_com_1_2_e_3_lives():
    raiz = Path(__file__).resolve().parents[1]
    script_node = r"""
const fs = require("node:fs");
const vm = require("node:vm");

const codigo = fs.readFileSync(process.argv[1], "utf8");

function criarEl(id) {
  const attrs = new Map();
  const classes = new Set();
  const base = {
    id,
    textContent: "",
    innerHTML: "",
    value: "",
    checked: false,
    disabled: false,
    hidden: false,
    open: false,
    title: "",
    dataset: {},
    style: {},
    children: [],
    classList: {
      add: (...cs) => { cs.forEach((c) => classes.add(c)); },
      remove: (...cs) => { cs.forEach((c) => classes.delete(c)); },
      toggle: (c, f) => {
        const on = f === undefined ? !classes.has(c) : Boolean(f);
        if (on) classes.add(c); else classes.delete(c);
      },
      contains: (c) => classes.has(c),
      _setFromClassName: (s) => { classes.clear(); String(s || "").split(/\s+/).filter(Boolean).forEach((c) => classes.add(c)); },
      _toString: () => Array.from(classes).join(" ")
    },
    setAttribute(k, v) { attrs.set(k, String(v)); },
    getAttribute(k) { return attrs.get(k) ?? null; },
    removeAttribute(k) { attrs.delete(k); },
    toggleAttribute(k, f) {
      const on = f === undefined ? !attrs.has(k) : Boolean(f);
      if (on) attrs.set(k, ""); else attrs.delete(k);
      if (k === "hidden") this.hidden = on;
    },
    addEventListener() {},
    querySelector() { return criarEl("sub"); },
    querySelectorAll() { return []; },
    append(...ns) { this.children.push(...ns); },
    appendChild(n) { this.children.push(n); return n; },
    prepend(n) { this.children.unshift(n); },
    replaceChildren(...ns) { this.children = [...ns]; },
    focus() {}
  };
  let cls = "";
  Object.defineProperty(base, "className", {
    get() { return base.classList._toString() || cls; },
    set(v) { cls = String(v || ""); base.classList._setFromClassName(cls); }
  });
  return base;
}

const elementos = new Map();
function getEl(id) {
  if (!elementos.has(id)) elementos.set(id, criarEl(id));
  return elementos.get(id);
}

let estadoCorrente = null;
let domReady = null;

const ctx = {
  console,
  setTimeout: () => 1,
  clearTimeout: () => {},
  setInterval: () => 1,
  clearInterval: () => {},
  URL,
  URLSearchParams,
  localStorage: { getItem: () => "escuro", setItem: () => {} },
  navigator: {},
  window: {
    addEventListener() {},
    confirm: () => true,
    history: { replaceState() {} },
    location: { href: "http://127.0.0.1:9999/", search: "" }
  },
  document: {
    activeElement: null,
    body: criarEl("body"),
    documentElement: criarEl("html"),
    getElementById: getEl,
    createElement: (tag) => criarEl(tag),
    querySelector: () => criarEl("q"),
    querySelectorAll: () => [],
    addEventListener(ev, fn) { if (ev === "DOMContentLoaded") domReady = fn; }
  },
  fetch: async (rota) => {
    let payload = { ok: true };
    if (rota === "/api/estado") payload = estadoCorrente;
    else if (rota === "/api/atualizacao/checar") payload = { etapa: "em_dia", versao_atual: "1.0.7" };
    else if (rota === "/api/config-postagem") payload = { ok: true, duracao_minima_modo: "61", cortes_longos: true };
    const txt = JSON.stringify(payload);
    return { ok: true, status: 200, text: async () => txt };
  }
};

vm.runInNewContext(codigo, ctx);

async function rodarCenario(sessao) {
  estadoCorrente = {
    ok: true,
    versao: "1.0.7",
    instalado: true,
    modo_descricao: "Hardware detectado automaticamente",
    sessao,
    cortes: []
  };
  await domReady();
  return {
    pilula: getEl("texto-sinal-tally").textContent,
    classe: getEl("indicador-tally").className,
    resumo: getEl("resumo-status-ao-vivo").textContent,
    atraso: getEl("metrica-atraso-voz").textContent,
    cortes: getEl("metrica-cortes-sessao").textContent,
    tempo: getEl("metrica-tempo-live").textContent
  };
}

(async () => {
  // 1) Defeito exato do print v1.0.7: 2 lives 'gravando' há 21 min, 3 cortes, atraso_s null no topo
  const c1 = await rodarCenario({
    rodando: true,
    estado: "gravando",
    canal: "gaules, ovotz",
    live_s: 1260,
    cortes_sessao: 3,
    atraso_s: null,
    ultimo_corte_ha_s: 62,
    ultima_mensagem: "",
    lives: [
      { id: "a", canal: "gaules", estado: "gravando", cortes: 2, pode_parar: true, live_s: 1260, atraso_transcricao_s: 11.8, atividade: "Fazendo o corte 3: legenda 62%" },
      { id: "b", canal: "ovotz", estado: "gravando", cortes: 1, pode_parar: true, live_s: 840, atraso_transcricao_s: 4.1, atividade: "Procurando o próximo momento (live há 14 min)" }
    ]
  });

  // 2) 3 lives (2 ao vivo + 1 esperando live), mensagem velha de largada presa em ultima_mensagem
  const c2 = await rodarCenario({
    rodando: true,
    estado: "ao vivo",
    canal: "gaules, ovotz, alanzoka",
    live_s: 1940,
    cortes_sessao: 4,
    atraso_s: null,
    ultimo_corte_ha_s: 125,
    ultima_mensagem: "Detectando placa de vídeo e preparando motor de voz…",
    lives: [
      { id: "1", canal: "gaules", estado: "ao vivo", cortes: 2, pode_parar: true, live_s: 1940, atraso_transcricao_s: 6.2, atividade: "Ouvindo a fala (atraso 6 s)" },
      { id: "2", canal: "ovotz", estado: "ao vivo", cortes: 2, pode_parar: true, live_s: 1510, atraso_transcricao_s: 14.8, atividade: "Achou um momento às 24:10 — cortando (54%)" },
      { id: "3", canal: "alanzoka", estado: "esperando live", cortes: 0, pode_parar: true, live_s: 0, atraso_transcricao_s: 0.0, atividade: "Esperando a live começar" }
    ]
  });

  // 3) 1 live única continua concisa sem repetir o nome no atraso
  const c3 = await rodarCenario({
    rodando: true,
    estado: "ao vivo",
    canal: "twitch.tv/nome_do_canal",
    live_s: 1482,
    cortes_sessao: 2,
    atraso_s: 3,
    ultima_mensagem: "Ouvindo a live… 2 cortes prontos"
  });

  // 4) Aviso de pouco espaço em disco continua aparecendo no topo
  const c4 = await rodarCenario({
    rodando: true,
    estado: "ao vivo",
    canal: "gaules, ovotz",
    live_s: 600,
    cortes_sessao: 1,
    atraso_s: 5,
    ultima_mensagem: "Pouco espaço no disco: 4,2 GB livres.",
    lives: [
      { id: "1", canal: "gaules", estado: "ao vivo", cortes: 1, pode_parar: true, live_s: 600, atraso_transcricao_s: 5.0 },
      { id: "2", canal: "ovotz", estado: "ao vivo", cortes: 0, pode_parar: true, live_s: 600, atraso_transcricao_s: 3.0 }
    ]
  });

  console.log(JSON.stringify({ c1, c2, c3, c4 }));
})();
"""
    proc = subprocess.run(
        ["node", "-e", script_node, str(raiz / "painel" / "app.js")],
        capture_output=True, text=True, encoding="utf-8", timeout=15, check=True,
    )
    res = json.loads(proc.stdout.strip())

    c1 = res["c1"]
    assert "Iniciando captura" not in c1["resumo"]
    assert "Acompanhando 2 lives" in c1["resumo"]
    assert "último corte há 1 min" in c1["resumo"]
    assert c1["pilula"] == "AO VIVO"
    assert "tally-ao-vivo" in c1["classe"]
    assert c1["atraso"] == "12 s (gaules)"

    c2 = res["c2"]
    assert "Detectando placa" not in c2["resumo"]
    assert "Acompanhando 2 lives (1 aguardando)" in c2["resumo"]
    assert "último corte há 2 min" in c2["resumo"]
    assert c2["atraso"] == "15 s (ovotz)"

    c3 = res["c3"]
    assert c3["resumo"] == "Acompanhando twitch.tv/nome_do_canal · Ouvindo a live… 2 cortes prontos"
    assert c3["atraso"] == "3 s"

    c4 = res["c4"]
    assert "Pouco espaço no disco" in c4["resumo"]
