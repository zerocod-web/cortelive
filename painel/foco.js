/* Editor de foco do corte (v1.0.16).
   Abre um quadro do corte, a pessoa arrasta uma caixa sobre a CÂMERA e outra sobre a GAMEPLAY, e o corte é refeito
   com essa marcação, fixa. As caixas têm a proporção dos painéis do vídeo final: o que está na caixa é exatamente o
   que aparece. Ideia de uso (caixa sobre a facecam e outra sobre o jogo) vista no modo Split do Eklipse; código próprio. */
(function () {
  "use strict";

  const ALVOS = { cam: "Câmera", jogo: "Gameplay" };
  let st = null;   // estado do editor aberto

  async function chamar(rota, metodo = "GET", corpo = null) {
    const op = { method: metodo, headers: { "Accept": "application/json" }, cache: "no-store" };
    if (corpo) {
      op.headers["Content-Type"] = "application/json; charset=utf-8";
      op.body = JSON.stringify(corpo);
    }
    try {
      const r = await fetch(rota, op);
      const t = await r.text();
      let d = {};
      try { d = t ? JSON.parse(t) : {}; } catch (_) { d = {}; }
      if (!r.ok && d.ok !== false) d = { ok: false, erro: "Esta função não está disponível nesta versão." };
      return d;
    } catch (_) {
      return { ok: false, erro: "A conexão com o CorteLive caiu. Tente novamente." };
    }
  }

  function criarModal() {
    let dlg = document.getElementById("modal-foco");
    if (dlg) return dlg;
    dlg = document.createElement("dialog");
    dlg.id = "modal-foco";
    dlg.className = "modal-dialogo modal-foco";
    dlg.setAttribute("aria-labelledby", "titulo-modal-foco");
    dlg.innerHTML = `
      <div class="modal-interno">
        <div class="modal-cabecalho">
          <h3 id="titulo-modal-foco">Ajustar foco do corte</h3>
          <button type="button" class="btn-icone" data-foco="fechar" aria-label="Fechar o editor de foco">
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>
          </button>
        </div>
        <div class="modal-corpo">
          <div>
            <div class="foco-ferramentas" role="group" aria-label="O que marcar">
              <span class="campo-rotulo">Marcar:</span>
              <button type="button" class="foco-alvo" data-alvo="cam" aria-pressed="true"><span class="foco-amostra" aria-hidden="true"></span>Câmera</button>
              <button type="button" class="foco-alvo" data-alvo="jogo" aria-pressed="false"><span class="foco-amostra" aria-hidden="true"></span>Gameplay</button>
            </div>
            <div class="foco-palco" data-foco="palco" tabindex="0" aria-label="Quadro do corte. Arraste para marcar; setas movem a caixa escolhida.">
              <p class="foco-vazio" data-foco="vazio">Carregando o quadro do corte…</p>
            </div>
            <div class="foco-linha-tempo">
              <label for="foco-tempo" class="campo-rotulo">Momento</label>
              <input type="range" id="foco-tempo" min="0" max="60" step="0.5" value="0">
              <span class="foco-tempo" data-foco="tempo">0:00</span>
            </div>
          </div>
          <div>
            <p class="campo-rotulo foco-previa-titulo">Como o corte vai ficar</p>
            <canvas class="foco-previa" data-foco="previa" width="432" height="768" aria-label="Prévia do vídeo vertical"></canvas>
            <p class="foco-ajuda">Escolha Câmera ou Gameplay e arraste sobre o quadro. Arraste a caixa para mover e a bolinha do canto para mudar o tamanho. A faixa vermelha da prévia mostra onde fica a legenda.</p>
          </div>
        </div>
        <div class="modal-rodape">
          <p class="foco-status" data-foco="status" role="status" aria-live="polite"></p>
          <span class="foco-espaco"></span>
          <label class="foco-lembrar" data-foco="lembrar-linha" hidden><input type="checkbox" data-foco="lembrar" checked> Usar nas próximas lives deste canal</label>
          <button type="button" class="btn btn-secundario" data-foco="auto">Voltar ao automático</button>
          <button type="button" class="btn btn-primario" data-foco="refazer">Refazer o corte com este foco</button>
        </div>
      </div>`;
    document.body.append(dlg);
    dlg.querySelector('[data-foco="fechar"]').addEventListener("click", () => dlg.close());
    dlg.addEventListener("close", () => { if (st) st.fechado = true; });
    dlg.querySelectorAll(".foco-alvo").forEach((b) => b.addEventListener("click", () => escolherAlvo(b.dataset.alvo)));
    const tempo = dlg.querySelector("#foco-tempo");
    let espera = null;
    tempo.addEventListener("input", () => {
      dlg.querySelector('[data-foco="tempo"]').textContent = mmss(tempo.value);
      clearTimeout(espera);
      espera = setTimeout(() => carregarQuadro(Number(tempo.value)), 220);
    });
    const palco = dlg.querySelector('[data-foco="palco"]');
    palco.addEventListener("pointerdown", aoApertar);
    palco.addEventListener("keydown", aoTeclar);
    dlg.querySelector('[data-foco="refazer"]').addEventListener("click", () => refazer(false));
    dlg.querySelector('[data-foco="auto"]').addEventListener("click", () => refazer(true));
    window.addEventListener("resize", () => { if (st && !st.fechado) desenhar(); });
    return dlg;
  }

  const mmss = (s) => { s = Math.floor(Number(s) || 0); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; };
  const q = (sel) => document.getElementById("modal-foco").querySelector(sel);

  function status(txt, erro = false) {
    const p = q('[data-foco="status"]');
    p.textContent = txt || "";
    p.dataset.erro = erro ? "true" : "false";
  }

  function botoes(ativos) {
    q('[data-foco="refazer"]').disabled = !ativos;
    q('[data-foco="auto"]').disabled = !ativos;
  }

  function modoDoModal(live) {
    q("#titulo-modal-foco").textContent = live ? `Conferir o foco desta live${live.canal ? " — " + live.canal : ""}` : "Ajustar foco do corte";
    q(".foco-linha-tempo").hidden = !!live;
    q('[data-foco="lembrar-linha"]').hidden = !live;
    q('[data-foco="lembrar"]').checked = true;
    q('[data-foco="refazer"]').textContent = live ? "Usar este foco na live" : "Refazer o corte com este foco";
    q('[data-foco="auto"]').textContent = live ? "Deixar no automático" : "Voltar ao automático";
  }

  async function abrir(corte) {
    const dlg = criarModal();
    st = { corte, caixas: {}, alvo: "cam", sw: 0, sh: 0, prop: null, img: null, fechado: false };
    modoDoModal(null);
    q('[data-foco="palco"]').querySelectorAll(".foco-caixa, img").forEach((n) => n.remove());
    q('[data-foco="vazio"]').hidden = false;
    q('[data-foco="vazio"]').textContent = "Carregando o quadro do corte…";
    status("");
    botoes(false);
    escolherAlvo("cam");
    if (typeof dlg.showModal === "function") dlg.showModal(); else dlg.setAttribute("open", "");
    await prepararFonte();
  }

  // ---- foco da live: conferido no começo, vale para a live toda (e, se marcado, para as próximas do canal) ----
  function abrirLive(p, proporcoes) {
    const dlg = criarModal();
    st = { live: p, caixas: {}, alvo: "cam", sw: p.tamanho[0], sh: p.tamanho[1], prop: proporcoes, img: null, fechado: false };
    modoDoModal(p);
    q('[data-foco="palco"]').querySelectorAll(".foco-caixa, img").forEach((n) => n.remove());
    q('[data-foco="vazio"]').hidden = false;
    q('[data-foco="vazio"]').textContent = "Carregando o quadro da live…";
    const pr = p.proposta || {};
    const salvo = p.origem === "salvo do canal";
    st.caixas.cam = salvo && pr.cam ? ajustar("cam", pr.cam[0], pr.cam[1], pr.cam[2]) : caixaInicial("cam", pr.cam);
    st.caixas.jogo = pr.jogo ? caixaDentro("jogo", pr.jogo) : caixaInicial("jogo", null);
    status(salvo ? "Esta live já começou com o foco salvo deste canal. Confirme ou arraste para corrigir."
      : "Confira a câmera e o jogo. Sem resposta, a live segue no automático.");
    escolherAlvo("cam");
    botoes(true);
    if (typeof dlg.showModal === "function") dlg.showModal(); else dlg.setAttribute("open", "");
    carregarQuadro(0);
  }

  function caixaDentro(alvo, c) {
    // a maior caixa na proporção do painel, centrada na área achada (o jogo pode não ter a proporção do painel)
    const [x, y, w, h] = c, p = proporcao(alvo);
    const ww = Math.min(w, h * p);
    return ajustar(alvo, x + (w - ww) / 2, y + (h - ww / p) / 2, ww);
  }

  async function responderLive(automatico) {
    const meu = st;
    botoes(false);
    const arred = (c) => c.map((v) => Math.round(v / 2) * 2);
    const r = await chamar("/api/foco-live/confirmar", "POST", {
      sessao: meu.live.sessao, lembrar: q('[data-foco="lembrar"]').checked,
      foco: automatico ? null : { cam: arred(meu.caixas.cam), jogo: arred(meu.caixas.jogo) },
    });
    if (r.ok === false) { status(r.erro, true); botoes(true); return; }
    status(r.mensagem || "Pronto.");
    setTimeout(() => { if (st === meu && !meu.fechado) document.getElementById("modal-foco").close(); }, 1800);
    if (!automatico) listarSalvos();
  }

  const vistos = new Set((() => { try { return JSON.parse(sessionStorage.getItem("focoLiveVistos") || "[]"); } catch (_) { return []; } })());
  async function vigiarLives() {
    const aberto = document.getElementById("modal-foco")?.open;
    if (!aberto) {
      const r = await chamar("/api/foco-live/pendente");
      const p = r.ok !== false && (r.pendentes || []).find((x) => !vistos.has(x.sessao + "|" + x.t));
      if (p && p.tamanho) {
        vistos.add(p.sessao + "|" + p.t);
        try { sessionStorage.setItem("focoLiveVistos", JSON.stringify([...vistos])); } catch (_) {}
        abrirLive(p, r.proporcoes);
      }
    }
    setTimeout(vigiarLives, 4000);
  }

  // Configurações › Foco salvo por canal
  async function listarSalvos() {
    const caixa = document.getElementById("focos-salvos");
    if (!caixa) return;
    const r = await chamar("/api/foco-live/canais");
    caixa.textContent = "";
    const lista = r.ok === false ? [] : r.canais || [];
    if (!lista.length) {
      const p = document.createElement("p");
      p.className = "ajuda-campo";
      p.textContent = r.ok === false ? r.erro : "Nenhum canal com foco salvo.";
      caixa.append(p);
      return;
    }
    lista.forEach((c) => {
      const linha = document.createElement("div");
      linha.className = "foco-salvo";
      const nome = document.createElement("span");
      nome.textContent = `${c.canal}${c.quando ? " — salvo em " + c.quando : ""}`;
      const b = document.createElement("button");
      b.type = "button"; b.className = "btn btn-secundario btn-compacto"; b.textContent = "Esquecer";
      b.addEventListener("click", async () => {
        b.disabled = true;
        await chamar("/api/foco-live/esquecer", "POST", { canal: c.canal });
        listarSalvos();
      });
      linha.append(nome, b);
      caixa.append(linha);
    });
  }

  async function lerEstado() {
    return chamar("/api/foco/estado?caminho=" + encodeURIComponent(st.corte.caminho));
  }

  async function prepararFonte() {
    const meu = st;
    let e = await lerEstado();
    if (meu.fechado) return;
    if (e.ok === false) { q('[data-foco="vazio"]').textContent = e.erro; return; }
    if (!e.fonte_pronta) {
      if (!e.pode_baixar) {
        q('[data-foco="vazio"]').textContent = "O trecho deste corte não foi guardado (corte feito antes desta versão, de live sem VOD). Os próximos cortes já vêm prontos para ajustar.";
        return;
      }
      if (!e.trabalho || !["baixando", "renderizando"].includes(e.trabalho.estado)) {
        const r = await chamar("/api/foco/baixar", "POST", { caminho: meu.corte.caminho });
        if (r.ok === false) { q('[data-foco="vazio"]').textContent = r.erro; return; }
      }
      q('[data-foco="vazio"]').textContent = "Baixando o trecho deste corte do VOD da live (só na primeira vez)…";
      e = await esperar(meu, (x) => x.fonte_pronta || (x.trabalho && x.trabalho.estado === "erro"));
      if (!e || meu.fechado) return;
      if (!e.fonte_pronta) { q('[data-foco="vazio"]').textContent = e.trabalho.mensagem; return; }
    }
    iniciarEditor(e);
  }

  function esperar(meu, pronto) {
    return new Promise((ok) => {
      const passo = async () => {
        if (meu.fechado) return ok(null);
        const e = await lerEstado();
        if (e.ok !== false && pronto(e)) return ok(e);
        if (e.ok !== false && e.trabalho && e.trabalho.mensagem) status(e.trabalho.mensagem);
        setTimeout(passo, 1500);
      };
      passo();
    });
  }

  function iniciarEditor(e) {
    st.sw = e.tamanho[0]; st.sh = e.tamanho[1]; st.prop = e.proporcoes;
    const dur = Number(e.duracao) || 60;
    const tempo = q("#foco-tempo");
    tempo.max = String(Math.max(1, Math.floor(dur)));
    tempo.value = String(Math.min(3, dur / 2));
    q('[data-foco="tempo"]').textContent = mmss(tempo.value);
    const f = e.foco || {};
    st.caixas.cam = f.cam ? f.cam.slice() : caixaInicial("cam", e.cam_auto);
    st.caixas.jogo = f.jogo ? f.jogo.slice() : caixaInicial("jogo", null);
    status(e.foco ? "Este corte já usa um foco marcado por você." : "Marque a câmera e a gameplay e clique em Refazer.");
    if (e.trabalho && ["baixando", "renderizando"].includes(e.trabalho.estado)) acompanhar(st);
    else botoes(true);
    carregarQuadro(Number(tempo.value));
  }

  function proporcao(alvo) { return alvo === "cam" ? st.prop.cam : st.prop.jogo; }

  function ajustar(alvo, x, y, w) {
    // caixa na proporção do painel, dentro do quadro
    const p = proporcao(alvo);
    w = Math.max(48, Math.min(w, st.sw, st.sh * p));
    const h = w / p;
    x = Math.min(Math.max(0, x), st.sw - w);
    y = Math.min(Math.max(0, y), st.sh - h);
    return [x, y, w, h];
  }

  function caixaInicial(alvo, auto) {
    if (alvo === "cam" && auto && auto.length === 4 && auto[2] < st.sw * 0.95) {
      const [x, y, w, h] = auto, cx = x + w / 2, cy = y + h / 2;
      const ww = Math.min(w, h * st.prop.cam);
      return ajustar("cam", cx - ww / 2, cy - ww / st.prop.cam / 2, ww);
    }
    if (alvo === "cam") return ajustar("cam", st.sw * 0.66, st.sh * 0.04, st.sw * 0.3);
    const w = st.sh * 0.92 * st.prop.jogo;
    return ajustar("jogo", (st.sw - w) / 2, st.sh * 0.04, w);
  }

  function carregarQuadro(t) {
    const meu = st;
    const img = new Image();
    img.alt = "";
    img.decoding = "async";
    img.onload = () => {
      if (meu.fechado || meu !== st) return;
      const palco = q('[data-foco="palco"]');
      palco.querySelector("img")?.remove();
      palco.prepend(img);
      q('[data-foco="vazio"]').hidden = true;
      st.img = img;
      desenhar();
    };
    img.onerror = () => { if (!st.img) q('[data-foco="vazio"]').textContent = "Não consegui mostrar o quadro."; };
    img.src = st.live
      ? "/api/foco-live/quadro?sessao=" + encodeURIComponent(st.live.sessao) + "&v=" + Date.now()
      : "/api/foco/quadro?caminho=" + encodeURIComponent(st.corte.caminho) + "&t=" + t + "&v=" + Date.now();
  }

  function escala() {
    const palco = q('[data-foco="palco"]');
    return palco.clientWidth / st.sw;
  }

  function desenhar() {
    if (!st.img) return;
    const palco = q('[data-foco="palco"]');
    const k = escala();
    for (const alvo of Object.keys(ALVOS)) {
      let d = palco.querySelector(`.foco-caixa[data-alvo="${alvo}"]`);
      if (!d) {
        d = document.createElement("div");
        d.className = "foco-caixa";
        d.dataset.alvo = alvo;
        d.innerHTML = `<span class="foco-etiqueta">${ALVOS[alvo]}</span><span class="foco-alca" data-alca="1"></span>`;
        palco.append(d);
      }
      const [x, y, w, h] = st.caixas[alvo];
      Object.assign(d.style, { left: x * k + "px", top: y * k + "px", width: w * k + "px", height: h * k + "px" });
      d.dataset.ativa = String(alvo === st.alvo);
      d.style.zIndex = alvo === st.alvo ? "2" : "1";
    }
    previa();
  }

  function previa() {
    const cv = q('[data-foco="previa"]');
    const g = cv.getContext("2d");
    const W = cv.width, H = cv.height, hc = Math.round(H * st.prop.altura_cam / st.prop.altura);
    g.fillStyle = "#000"; g.fillRect(0, 0, W, H);
    if (!st.img || !st.img.naturalWidth) return;
    const k = st.img.naturalWidth / st.sw;
    const [cx, cy, cw, ch] = st.caixas.cam, [jx, jy, jw, jh] = st.caixas.jogo;
    g.drawImage(st.img, cx * k, cy * k, cw * k, ch * k, 0, 0, W, hc);
    g.drawImage(st.img, jx * k, jy * k, jw * k, jh * k, 0, hc, W, H - hc);
    const fa = H * 80 / st.prop.altura;          // parte opaca da faixa da legenda (~80 px de 1920)
    g.fillStyle = "rgba(186, 0, 0, 0.82)";
    g.fillRect(0, hc - fa / 2, W, fa);
  }

  function escolherAlvo(alvo) {
    if (!st) return;
    st.alvo = alvo;
    document.querySelectorAll("#modal-foco .foco-alvo").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.alvo === alvo)));
    desenhar();
  }

  function aoApertar(ev) {
    if (!st || !st.img || ev.button !== 0) return;
    const palco = q('[data-foco="palco"]');
    const r = palco.getBoundingClientRect(), k = escala();
    const px = (ev.clientX - r.left) / k, py = (ev.clientY - r.top) / k;
    // só a caixa escolhida (Câmera ou Gameplay) responde: arrastar em cima da outra desenha a escolhida
    const naCaixa = ev.target.closest(".foco-caixa");
    const alvo = st.alvo;
    const daAtiva = naCaixa && naCaixa.dataset.alvo === alvo;
    const ini = st.caixas[alvo].slice();
    const modo = daAtiva && ev.target.dataset.alca ? "tamanho" : daAtiva ? "mover" : "desenhar";
    palco.setPointerCapture(ev.pointerId);
    const mover = (e2) => {
      const qx = (e2.clientX - r.left) / k, qy = (e2.clientY - r.top) / k;
      if (modo === "mover") st.caixas[alvo] = ajustar(alvo, ini[0] + qx - px, ini[1] + qy - py, ini[2]);
      else if (modo === "tamanho") st.caixas[alvo] = ajustar(alvo, ini[0], ini[1], Math.max(qx - ini[0], (qy - ini[1]) * proporcao(alvo)));
      else {
        const w = Math.max(Math.abs(qx - px), Math.abs(qy - py) * proporcao(alvo));
        st.caixas[alvo] = ajustar(alvo, qx < px ? px - w : px, qy < py ? py - w / proporcao(alvo) : py, w);
      }
      desenhar();
    };
    const soltar = () => {
      palco.removeEventListener("pointermove", mover);
      palco.removeEventListener("pointerup", soltar);
      palco.removeEventListener("pointercancel", soltar);
    };
    palco.addEventListener("pointermove", mover);
    palco.addEventListener("pointerup", soltar);
    palco.addEventListener("pointercancel", soltar);
    ev.preventDefault();
  }

  function aoTeclar(ev) {
    if (!st || !st.img) return;
    const passo = (ev.shiftKey ? 40 : 8) / escala();
    const [x, y, w] = st.caixas[st.alvo];
    const mov = { ArrowLeft: [-passo, 0], ArrowRight: [passo, 0], ArrowUp: [0, -passo], ArrowDown: [0, passo] }[ev.key];
    if (mov) st.caixas[st.alvo] = ajustar(st.alvo, x + mov[0], y + mov[1], w);
    else if (ev.key === "+" || ev.key === "=") st.caixas[st.alvo] = ajustar(st.alvo, x, y, w * 1.05);
    else if (ev.key === "-") st.caixas[st.alvo] = ajustar(st.alvo, x, y, w / 1.05);
    else return;
    ev.preventDefault();
    desenhar();
  }

  async function refazer(automatico) {
    const meu = st;
    if (!meu) return;
    if (meu.live) return responderLive(automatico);
    botoes(false);
    const arred = (c) => c.map((v) => Math.round(v / 2) * 2);
    const r = automatico
      ? await chamar("/api/foco/automatico", "POST", { caminho: meu.corte.caminho })
      : await chamar("/api/foco/refazer", "POST", { caminho: meu.corte.caminho, foco: { cam: arred(meu.caixas.cam), jogo: arred(meu.caixas.jogo) } });
    if (r.ok === false) { status(r.erro, true); botoes(true); return; }
    status(automatico ? "Refazendo no automático…" : "Refazendo o corte com o seu foco…");
    acompanhar(meu);
  }

  async function acompanhar(meu) {
    botoes(false);
    const e = await esperar(meu, (x) => x.trabalho && ["pronto", "erro"].includes(x.trabalho.estado));
    if (!e) return;
    status(e.trabalho.mensagem, e.trabalho.estado === "erro");
    botoes(true);
    if (e.trabalho.estado === "pronto") {
      // miniatura nova no card do corte
      document.querySelectorAll(".corte-card").forEach((card) => {
        if (card.dataset.caminho !== meu.corte.caminho) return;
        card.querySelectorAll("img").forEach((im) => { im.src = im.src.replace(/&v=\d+$/, "") + "&v=" + Date.now(); });
      });
    }
  }

  window.CorteLiveFoco = { abrir, abrirLive, listarSalvos };
  const comecar = () => {
    vigiarLives();
    listarSalvos();
    document.getElementById("aba-btn-config")?.addEventListener("click", listarSalvos);
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", comecar); else comecar();
})();
