/* ==========================================================================
   CorteLive — Lógica do Painel (Vanilla JS, zero dependências externas)
   Integra: instalar.ps1, motor_painel.py, instalador\atualizar.ps1,
            cortelive.postar, cortelive.autopost e cortelive.autopost_navegador.
   ========================================================================== */
(function () {
  "use strict";

  const INTERVALO_ATIVO_MS = 1200;
  const INTERVALO_OCIOSO_MS = 2500;
  const INTERVALO_HEARTBEAT_MS = 3000;
  const INTERVALO_ATUALIZACAO_MS = 30 * 60 * 1000;

  let estadoAtual = null;
  let assinaturaCortes = "";
  let postarStatus = { disponivel: false, autopost: false };
  let timerPoll = null;
  let abaAtiva = "cortar";
  let acompanhamentoAtualizacao = false;
  let canaisAtivosPostiz = [];
  let canaisSelecionados = null;
  let editandoLimites = false;
  let salvandoPreferencias = false;
  const redesPostagem = {
    youtube: "YouTube Shorts",
    instagram: "Instagram Reels",
    tiktok: "TikTok"
  };

  const el = (id) => document.getElementById(id);

  // --- Tema claro / escuro ---
  function aplicarTema(tema) {
    const t = tema === "claro" ? "claro" : "escuro";
    document.documentElement.setAttribute("data-tema", t);
    try {
      localStorage.setItem("cortelive_tema", t);
    } catch (_) {}
    const sol = el("icone-tema-sol");
    const lua = el("icone-tema-lua");
    if (sol && lua) {
      sol.toggleAttribute("hidden", t === "claro");
      lua.toggleAttribute("hidden", t === "escuro");
    }
  }

  function iniciarTema() {
    let salvo = "escuro";
    try {
      salvo = localStorage.getItem("cortelive_tema") || "escuro";
    } catch (_) {}
    aplicarTema(salvo);
    el("btn-alternar-tema").addEventListener("click", () => {
      const atual = document.documentElement.getAttribute("data-tema");
      aplicarTema(atual === "escuro" ? "claro" : "escuro");
    });
  }

  function formatarHms(seg) {
    const s = Math.max(0, Math.floor(Number(seg) || 0));
    const h = String(Math.floor(s / 3600)).padStart(2, "0");
    const m = String(Math.floor((s % 3600) / 60)).padStart(2, "0");
    const r = String(s % 60).padStart(2, "0");
    return `${h}:${m}:${r}`;
  }

  async function api(rota, metodo = "GET", corpo = null) {
    const opcoes = {
      method: metodo,
      headers: { "Accept": "application/json" },
      cache: "no-store"
    };
    if (corpo !== null) {
      opcoes.headers["Content-Type"] = "application/json; charset=utf-8";
      opcoes.body = JSON.stringify(corpo);
    }
    let resp;
    try {
      resp = await fetch(rota, opcoes);
    } catch (_) {
      return { ok: false, erro: "A conexão com o CorteLive caiu. Tente novamente." };
    }
    const txt = await resp.text();
    let dados = {};
    try {
      dados = txt ? JSON.parse(txt) : {};
    } catch (_) {
      dados = { ok: resp.ok, texto: txt };
    }
    if (!resp.ok && !dados.erro) {
      dados.erro = `Erro HTTP ${resp.status}`;
    }
    if (!resp.ok) dados.ok = false;
    return dados;
  }

  async function copiarTexto(texto, botaoSpan, rotuloOriginal) {
    let copiou = false;
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(texto);
        copiou = true;
      }
    } catch (_) {}
    if (!copiou) {
      const area = document.createElement("textarea");
      area.value = texto;
      area.style.position = "fixed";
      area.style.opacity = "0";
      document.body.appendChild(area);
      area.select();
      try {
        copiou = document.execCommand("copy");
      } catch (_) {}
      document.body.removeChild(area);
    }
    if (botaoSpan) {
      botaoSpan.textContent = copiou ? "Log copiado!" : "Falha ao copiar";
      setTimeout(() => {
        botaoSpan.textContent = rotuloOriginal;
      }, 2400);
    }
  }

  function mostrarAvisoSistema(titulo, texto) {
    el("titulo-aviso-sistema").textContent = titulo;
    el("texto-aviso-sistema").textContent = texto;
    el("faixa-aviso-sistema").hidden = false;
  }

  // --- Abas ---
  function alternarAba(novaAba) {
    abaAtiva = novaAba === "contas" ? "contas" : "cortar";
    const btnCortar = el("aba-btn-cortar");
    const btnContas = el("aba-btn-contas");
    btnCortar.classList.toggle("ativa", abaAtiva === "cortar");
    btnContas.classList.toggle("ativa", abaAtiva === "contas");
    if (abaAtiva === "cortar") {
      btnCortar.setAttribute("aria-current", "page");
      btnContas.removeAttribute("aria-current");
    } else {
      btnContas.setAttribute("aria-current", "page");
      btnCortar.removeAttribute("aria-current");
      recarregarTudoPostagem();
    }
    el("aba-painel-cortar").hidden = abaAtiva !== "cortar";
    el("aba-painel-contas").hidden = abaAtiva !== "contas";
  }

  // --- Tela de Instalação ---
  function atualizarTelaInstalacao(inst) {
    if (!inst) return;
    const rodando = Boolean(inst.rodando);
    const pct = Math.max(0, Math.min(100, Math.round(Number(inst.porcentagem) || 0)));
    const etapaAtual = Number(inst.etapa) || 0;
    const temErro = Boolean(inst.erro) && !rodando;

    const btnInstalar = el("btn-instalar");
    btnInstalar.disabled = rodando;
    el("texto-btn-instalar").textContent = rodando ? "INSTALANDO…" : "INSTALAR";

    const blocoProg = el("bloco-progresso-instalacao");
    blocoProg.hidden = !rodando && pct === 0 && !temErro;
    el("estado-titulo-instalacao").textContent = temErro ? "Instalação interrompida" : "Instalação em andamento";

    el("progresso-porcentagem").textContent = `${pct}%`;
    el("barra-preenchimento").style.width = `${pct}%`;
    el("barra-instalacao").setAttribute("aria-valuenow", String(pct));
    el("etapa-atual-titulo").textContent = inst.titulo_etapa || (rodando ? "Instalando componentes…" : "Pronto para instalar");
    el("etapa-atual-detalhe").textContent = inst.detalhe || "Tudo fica isolado na pasta do CorteLive.";

    const itens = document.querySelectorAll("#lista-etapas .item-etapa");
    itens.forEach((li) => {
      const passo = Number(li.getAttribute("data-passo"));
      const badge = li.querySelector(".estado-etapa-badge");
      li.classList.remove("concluida", "em-andamento", "falhou");

      if (pct >= 100 || etapaAtual > passo) {
        li.classList.add("concluida");
        badge.textContent = "Concluído";
      } else if (etapaAtual === passo && rodando) {
        li.classList.add("em-andamento");
        badge.textContent = "Em andamento";
      } else if (etapaAtual === passo && temErro) {
        li.classList.add("falhou");
        badge.textContent = "Erro";
      } else {
        badge.textContent = "Pendente";
      }
    });

    const blocoErro = el("bloco-erro-instalacao");
    blocoErro.hidden = !temErro;
    if (temErro) {
      el("erro-instalacao-titulo").textContent = inst.erro;
      el("erro-instalacao-acao").textContent =
        inst.o_que_fazer || "Confira sua conexão com a internet e clique em Tentar novamente — o download continua de onde parou.";
      el("detalhes-log-instalacao").open = true;
    }

    el("pre-log-instalacao").textContent = inst.log_recente || "Aguardando início…";
  }

  // --- Mesa de Corte + Status ao Vivo ---
  function atualizarMesaCorte(dados) {
    el("info-hardware-modo").textContent = dados.modo_descricao || "Hardware detectado automaticamente";

    const sessao = dados.sessao || {};
    const rodando = Boolean(sessao.rodando);
    const estado = (sessao.estado || "parado").toLowerCase();

    const btnCortar = el("btn-cortar");
    const btnParar = el("btn-parar");
    const campoLink = el("campo-link");

    btnCortar.hidden = rodando;
    btnParar.hidden = !rodando;
    const finalizando = estado === "encerrando" || estado === "encerrada";
    btnParar.disabled = finalizando;
    el("texto-btn-parar").textContent = finalizando ? "FINALIZANDO CORTES…" : "PARAR SESSÃO";
    campoLink.disabled = rodando;
    el("btn-escolher-video").disabled = rodando;
    document.querySelectorAll('#form-cortar input[type="radio"]').forEach((r) => {
      r.disabled = rodando;
    });

    const pilula = el("indicador-tally");
    const txtTally = el("texto-sinal-tally");
    const resumo = el("resumo-status-ao-vivo");
    const metricas = el("metricas-sessao");

    pilula.className = "tally-pilula";
    if (!rodando) {
      pilula.classList.add("tally-parado");
      txtTally.textContent = sessao.erro ? "ERRO" : "PRONTO";
      resumo.textContent = sessao.ultima_mensagem || "Cole o link da live abaixo e clique em Cortar.";
      metricas.hidden = true;
    } else if (estado === "ao vivo") {
      pilula.classList.add("tally-ao-vivo");
      txtTally.textContent = "AO VIVO";
      const canal = sessao.canal ? `Acompanhando ${sessao.canal}` : "Acompanhando transmissão em tempo real";
      resumo.textContent = sessao.ultima_mensagem ? `${canal} · ${sessao.ultima_mensagem}` : canal;
      metricas.hidden = false;
    } else {
      pilula.classList.add("tally-preparando");
      const mapaRotulo = {
        "iniciando": "PREPARANDO",
        "esperando live": "AGUARDANDO LIVE",
        "reconectando": "RECONECTANDO",
        "encerrando": "FINALIZANDO",
        "encerrada": "FINALIZANDO"
      };
      txtTally.textContent = mapaRotulo[estado] || estado.toUpperCase();
      resumo.textContent = sessao.ultima_mensagem || "Iniciando captura e modelo de voz…";
      metricas.hidden = false;
    }

    if (rodando) {
      el("metrica-tempo-live").textContent = formatarHms(sessao.live_s || 0);
      el("metrica-cortes-sessao").textContent = String(sessao.cortes_sessao || 0);
      const atr = sessao.atraso_s;
      el("metrica-atraso-voz").textContent = atr !== null && atr !== undefined ? `${Math.round(atr)} s` : "—";
    }

    const blocoErroSessao = el("bloco-erro-sessao");
    if (sessao.erro && !rodando) {
      blocoErroSessao.hidden = false;
      el("erro-sessao-texto").textContent = sessao.erro;
    } else {
      blocoErroSessao.hidden = true;
    }

    const painelVivo = el("painel-atividade-vivo");
    if (rodando || (sessao.log_recente && sessao.log_recente.trim().length > 0)) {
      painelVivo.hidden = false;
      el("detalhe-motor-vivo").textContent = sessao.detalhe_servicos || "";
      el("pre-eventos-sessao").textContent = sessao.log_recente || "Iniciando sessão…";
    } else {
      painelVivo.hidden = true;
    }
  }

  // --- Lista de Cortes ---
  function criarCardCorte(c) {
    const card = document.createElement("article");
    card.className = "corte-card";

    const durTxt = c.duracao > 0 ? formatarHms(c.duracao).replace(/^00:/, "") : "MP4";
    const tempoTxt = c.inicio_hms ? `Momento ${c.inicio_hms}` : (c.data_hora || "");
    const titulo = c.titulo || c.nome_arquivo;
    const motivo = c.motivo || `Layout: ${c.modelo || "vertical 9:16"}`;

    const topo = document.createElement("div");
    topo.className = "corte-topo";

    const slate = document.createElement("div");
    slate.className = "corte-slate-916";
    slate.innerHTML = `
      <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor" aria-hidden="true"><polygon points="8,5 19,12 8,19"/></svg>
      <span class="corte-duracao"></span>
    `;
    slate.querySelector(".corte-duracao").textContent = durTxt;
    if (c.miniatura && c.miniatura.startsWith("/api/miniatura?")) {
      const img = document.createElement("img");
      img.alt = "";
      img.loading = "lazy";
      img.addEventListener("error", () => img.remove(), { once: true });
      img.src = c.miniatura;
      slate.prepend(img);
    }

    const meta = document.createElement("div");
    meta.className = "corte-meta-bloco";

    const linhaCanal = document.createElement("div");
    linhaCanal.className = "corte-canal-linha";
    const spanCanal = document.createElement("span");
    spanCanal.className = "corte-canal";
    spanCanal.textContent = c.canal || "Corte";
    const spanTempo = document.createElement("span");
    spanTempo.className = "corte-tempo";
    spanTempo.textContent = tempoTxt;
    linhaCanal.appendChild(spanCanal);
    linhaCanal.appendChild(spanTempo);

    const h3 = document.createElement("h3");
    h3.className = "corte-titulo";
    h3.textContent = titulo;

    const pMotivo = document.createElement("p");
    pMotivo.className = "corte-motivo";
    pMotivo.textContent = motivo;

    meta.appendChild(linhaCanal);
    meta.appendChild(h3);
    meta.appendChild(pMotivo);
    topo.appendChild(slate);
    topo.appendChild(meta);

    const acoes = document.createElement("div");
    acoes.className = "corte-acoes";

    const btnVideo = document.createElement("button");
    btnVideo.type = "button";
    btnVideo.className = "btn btn-secundario btn-compacto";
    btnVideo.innerHTML = `
      <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor" aria-hidden="true"><polygon points="7,4 20,12 7,20"/></svg>
      <span>Abrir</span>
    `;
    btnVideo.addEventListener("click", async () => {
      const res = await api("/api/abrir-video", "POST", { caminho: c.caminho });
      if (res.ok === false) mostrarAvisoSistema("Não foi possível abrir o vídeo", res.erro || "Confira se o corte ainda está na pasta.");
    });

    const btnPasta = document.createElement("button");
    btnPasta.type = "button";
    btnPasta.className = "btn btn-secundario btn-compacto";
    btnPasta.innerHTML = `
      <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/>
      </svg>
      <span>Pasta</span>
    `;
    btnPasta.addEventListener("click", async () => {
      const res = await api("/api/abrir-pasta", "POST", { caminho: c.caminho });
      if (res.ok === false) mostrarAvisoSistema("Não foi possível abrir a pasta", res.erro || "Confira se o corte ainda está na pasta.");
    });

    const btnPostar = document.createElement("button");
    btnPostar.type = "button";
    btnPostar.className = "btn btn-secundario btn-compacto";
    btnPostar.innerHTML = `
      <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <path d="M4 12v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8"/><polyline points="16 6 12 2 8 6"/><line x1="12" y1="2" x2="12" y2="15"/>
      </svg>
      <span>Postar</span>
    `;
    btnPostar.addEventListener("click", () => abrirModalPostar(c));

    acoes.appendChild(btnVideo);
    acoes.appendChild(btnPasta);
    acoes.appendChild(btnPostar);

    card.appendChild(topo);
    card.appendChild(acoes);
    return card;
  }

  function atualizarListaCortes(cortes) {
    const lista = Array.isArray(cortes) ? cortes : [];
    const total = lista.length;
    el("contador-cortes-total").textContent = total === 1 ? "1 corte" : `${total} cortes`;

    const vazio = el("estado-sem-cortes");
    const grade = el("lista-cortes");

    if (total === 0) {
      vazio.hidden = false;
      grade.hidden = true;
      grade.innerHTML = "";
      assinaturaCortes = "vazio";
      return;
    }

    vazio.hidden = true;
    grade.hidden = false;

    const novaAssinatura = JSON.stringify(lista);
    if (novaAssinatura === assinaturaCortes) return;
    assinaturaCortes = novaAssinatura;

    const cards = new Map(Array.from(grade.children, (card) => [card.dataset.caminho, card]));
    const mostrados = lista.map((c) => {
      const anterior = cards.get(c.caminho);
      const assinatura = JSON.stringify(c);
      if (anterior?.dataset.assinatura === assinatura) return anterior;
      const card = criarCardCorte(c);
      card.dataset.caminho = c.caminho;
      card.dataset.assinatura = assinatura;
      return card;
    });
    const manter = new Set(mostrados);
    for (const anterior of cards.values()) {
      if (!manter.has(anterior)) anterior.remove();
    }
    mostrados.forEach((card, posicao) => {
      const atual = grade.children[posicao] || null;
      if (atual !== card) grade.insertBefore(card, atual);
    });
  }

  // --- Aba Postar e Contas (config + autopost + postar + autopost_navegador) ---
  function mostrarSelecaoCanais() {
    const opcoes = el("postar-canais-opcoes");
    const todos = el("postar-todos-canais");
    todos.checked = canaisSelecionados === null;
    todos.disabled = canaisAtivosPostiz.length === 0 && todos.checked;
    opcoes.hidden = canaisAtivosPostiz.length === 0;
    el("postar-canais-sem-opcoes").hidden = !opcoes.hidden;
    opcoes.replaceChildren();
    canaisAtivosPostiz.forEach((canal) => {
      const item = document.createElement("label");
      item.className = "check-pilula";
      const campo = document.createElement("input");
      campo.type = "checkbox";
      campo.name = "canal-postagem";
      campo.value = canal.id;
      campo.checked = todos.checked || canaisSelecionados.has(canal.id);
      const texto = document.createElement("span");
      texto.textContent = `${canal.nome || "Canal"} · ${redesPostagem[canal.rede]}`;
      item.append(campo, texto);
      opcoes.appendChild(item);
    });
  }

  function aplicarLimitesPostagem(cfg) {
    el("postar-max-dia").value = cfg.max_posts_dia ?? 48;
    el("postar-intervalo-min").value = cfg.intervalo_min_posts ?? 30;
    el("chk-postar-rodizio").checked = cfg.rodizio === true;
    const ids = cfg.canais_postiz;
    canaisSelecionados = Array.isArray(ids) && ids.length ? new Set(ids.map(String)) : null;
    mostrarSelecaoCanais();
  }

  function mostrarFeedbackLimites(texto, erro = false) {
    const feedback = el("feedback-limites-postagem");
    feedback.textContent = texto;
    feedback.dataset.erro = String(erro);
  }

  function mudarLimitesPostagem(evento) {
    editandoLimites = true;
    mostrarFeedbackLimites("");
    const campo = evento.target;
    const todos = el("postar-todos-canais");
    if (campo === todos) {
      canaisSelecionados = todos.checked ? null : new Set(canaisAtivosPostiz.map((canal) => canal.id));
      const opcoes = el("postar-canais-opcoes").querySelectorAll("input");
      opcoes.forEach((opcao) => { opcao.checked = true; });
    } else if (campo.name === "canal-postagem") {
      if (canaisSelecionados === null) {
        canaisSelecionados = new Set(canaisAtivosPostiz.map((canal) => canal.id));
      }
      if (campo.checked) {
        canaisSelecionados.add(campo.value);
      } else {
        canaisSelecionados.delete(campo.value);
      }
      todos.checked = false;
    }
  }

  async function carregarConfigPostagem() {
    try {
      const cfg = await api("/api/config-postagem");
      if (cfg.ok === false || salvandoPreferencias) return;
      el("chk-postar-sozinho").checked = Boolean(cfg.postar_sozinho);
      el("chk-revisar-antes").checked = cfg.revisar_antes !== false;
      const navLigada = Boolean(cfg.reserva_navegador);
      el("chk-reserva-navegador").checked = navLigada;
      el("controles-reserva-redes").hidden = !navLigada;
      if (!editandoLimites) aplicarLimitesPostagem(cfg);
      if (navLigada) {
        carregarStatusNavegador();
      }
    } catch (_) {}
  }

  async function salvarConfigPostagem(limites = null) {
    if (salvandoPreferencias) return null;
    salvandoPreferencias = true;
    const payload = {
      postar_sozinho: el("chk-postar-sozinho").checked,
      revisar_antes: el("chk-revisar-antes").checked,
      reserva_navegador: el("chk-reserva-navegador").checked,
      ...limites
    };
    el("controles-reserva-redes").hidden = !payload.reserva_navegador;
    const ids = ["chk-postar-sozinho", "chk-revisar-antes", "chk-reserva-navegador"];
    ids.forEach((id) => { el(id).disabled = true; });
    const botao = el("btn-salvar-limites-postagem");
    botao.disabled = true;
    try {
      return await api("/api/config-postagem", "POST", payload);
    } finally {
      salvandoPreferencias = false;
      ids.forEach((id) => { el(id).disabled = false; });
      botao.disabled = el("campos-limites-postagem").disabled;
    }
  }

  async function salvarInterruptoresPostagem() {
    const resp = await salvarConfigPostagem();
    if (resp?.ok === false) {
      mostrarAvisoSistema("Não foi possível salvar as preferências", resp.erro);
      await carregarConfigPostagem();
    } else if (el("chk-reserva-navegador").checked) {
      await carregarStatusNavegador();
    }
  }

  async function salvarLimitesPostagem(evento) {
    evento.preventDefault();
    const campos = el("campos-limites-postagem");
    if (salvandoPreferencias || campos.disabled) return;
    if (canaisSelecionados?.size === 0) {
      mostrarFeedbackLimites("Selecione pelo menos um canal ou marque Todos os canais do Postiz.", true);
      el("postar-todos-canais").focus();
      return;
    }
    const maxDia = el("postar-max-dia").valueAsNumber;
    const intervalo = el("postar-intervalo-min").valueAsNumber;
    if (!Number.isSafeInteger(maxDia) || maxDia < 1 || !Number.isSafeInteger(intervalo) || intervalo < 0) {
      mostrarFeedbackLimites("Informe um máximo diário positivo e um intervalo de zero ou mais minutos.", true);
      return;
    }
    const limites = {
      max_posts_dia: maxDia,
      intervalo_min_posts: intervalo,
      canais_postiz: canaisSelecionados === null ? [] : Array.from(canaisSelecionados),
      rodizio: el("chk-postar-rodizio").checked
    };
    campos.disabled = true;
    mostrarFeedbackLimites("Salvando limites e canais…");
    try {
      const resp = await salvarConfigPostagem(limites);
      if (resp?.ok === false) {
        mostrarFeedbackLimites(resp.erro || "Não foi possível salvar os limites e canais.", true);
        return;
      }
      const cfg = await api("/api/config-postagem");
      const canaisConferem = Array.isArray(cfg.canais_postiz) &&
        JSON.stringify([...cfg.canais_postiz].sort()) === JSON.stringify([...limites.canais_postiz].sort());
      if (cfg.ok === false || !canaisConferem ||
          cfg.max_posts_dia !== maxDia || cfg.intervalo_min_posts !== intervalo ||
          cfg.rodizio !== limites.rodizio) {
        mostrarFeedbackLimites("O CorteLive não confirmou os novos limites e canais. Tente novamente.", true);
        return;
      }
      editandoLimites = false;
      aplicarLimitesPostagem(cfg);
      mostrarFeedbackLimites("Limites e canais salvos.");
    } finally {
      campos.disabled = false;
      el("btn-salvar-limites-postagem").disabled = salvandoPreferencias;
    }
  }

  function criarCardFilaAutopost(it) {
    const card = document.createElement("article");
    card.className = "fila-card";

    const meta = it.metadados || {};
    const yt = meta.youtube || {};
    const tk = meta.tiktok || {};
    const ig = meta.instagram || {};
    const estado = it.estado || "aguardando_aprovacao";
    const editavel = ["aguardando_aprovacao", "erro", "parcial", "na_fila"].includes(estado);

    const topo = document.createElement("div");
    topo.className = "fila-topo";
    const tituloTopo = document.createElement("strong");
    tituloTopo.textContent = yt.titulo || it.canal || "Corte";
    const badge = document.createElement("span");
    badge.className = "selo-etapa";
    const rotulos = {
      aguardando_aprovacao: "Para revisar",
      na_fila: "Na fila",
      postando: "Enviando",
      postado: "Publicado",
      parcial: "Envio parcial",
      erro: "Erro no envio"
    };
    badge.textContent = rotulos[estado] || estado.replace(/_/g, " ");
    topo.appendChild(tituloTopo);
    topo.appendChild(badge);

    const campos = document.createElement("div");
    campos.className = "fila-campos";

    const criarCampo = (rede, rotulo, nome, valor, multilinha) => {
      const lb = document.createElement("label");
      lb.className = "campo-rotulo";
      lb.textContent = rotulo;
      const inp = document.createElement(multilinha ? "textarea" : "input");
      if (multilinha) inp.rows = 2;
      inp.className = "campo-texto";
      inp.name = nome;
      inp.defaultValue = valor || "";
      inp.value = valor || "";
      inp.setAttribute("aria-label", `${redesPostagem[rede]} · ${rotulo}`);
      inp.readOnly = !editavel;
      if (nome === rede) lb.classList.add("fila-texto-unico");
      lb.appendChild(inp);
      campos.appendChild(lb);
      return inp;
    };

    const mostrarRede = (rede) => {
      const nomeRede = document.createElement("strong");
      nomeRede.className = "fila-rede";
      nomeRede.textContent = redesPostagem[rede];
      campos.appendChild(nomeRede);
    };
    mostrarRede("youtube");
    criarCampo("youtube", "Título", "titulo", yt.titulo, false).maxLength = 100;
    criarCampo("youtube", "Legenda", "descricao", yt.descricao, true);
    mostrarRede("instagram");
    criarCampo("instagram", "Título e legenda (primeira linha vira o título)", "instagram", ig.legenda, true);
    mostrarRede("tiktok");
    criarCampo("tiktok", "Título e legenda", "tiktok", tk.legenda, true);

    const acoes = document.createElement("div");
    acoes.className = "fila-acoes";
    const feedback = document.createElement("p");
    feedback.className = "erro-campo";
    feedback.setAttribute("role", "status");
    feedback.hidden = true;
    const salvar = () => {
      const editados = Array.from(campos.querySelectorAll("input, textarea"))
        .filter((campo) => campo.value !== campo.defaultValue)
        .map((campo) => [campo.name, campo.value]);
      if (editados.length === 0) return { ok: true };
      return api("/api/autopost/editar", "POST", { id: it.id, ...Object.fromEntries(editados) });
    };
    const acaoFila = (rotulo, ocupado, executar, primario = false) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = `btn ${primario ? "btn-primario" : "btn-secundario"} btn-compacto`;
      btn.textContent = rotulo;
      btn.addEventListener("click", async () => {
        const controles = [...acoes.querySelectorAll("button"), ...campos.querySelectorAll("input, textarea")];
        controles.forEach((controle) => { controle.disabled = true; });
        btn.textContent = ocupado;
        feedback.hidden = true;
        try {
          const res = await executar();
          if (res.ok === false) {
            feedback.textContent = res.erro || "Não foi possível concluir esta ação.";
            feedback.hidden = false;
            return;
          }
          await carregarFilaAutopost();
        } finally {
          controles.forEach((controle) => { controle.disabled = false; });
          btn.textContent = rotulo;
        }
      });
      acoes.appendChild(btn);
    };

    if (editavel) acaoFila("Salvar edição", "Salvando…", salvar);
    if (estado === "aguardando_aprovacao") {
      acaoFila("Aprovar e postar", "Enviando…", async () => {
        const res = await salvar();
        if (res.ok === false) return res;
        return api("/api/autopost/aprovar", "POST", { id: it.id });
      }, true);
      acaoFila("Rejeitar", "Rejeitando…", () => api("/api/autopost/rejeitar", "POST", { id: it.id }));
    }

    if (it.video) {
      const btnVer = document.createElement("button");
      btnVer.type = "button";
      btnVer.className = "btn btn-secundario btn-compacto";
      btnVer.textContent = "Abrir vídeo";
      btnVer.addEventListener("click", () => {
        api("/api/abrir-video", "POST", { caminho: it.video });
      });
      acoes.appendChild(btnVer);
    }

    card.appendChild(topo);
    card.appendChild(campos);
    card.append(acoes, feedback);
    return card;
  }

  async function carregarFilaAutopost() {
    try {
      const res = await api("/api/autopost/fila");
      if (res.ok === false) return;
      postarStatus.autopost = Boolean(res.disponivel);
      const itens = Array.isArray(res.itens) ? res.itens.filter((i) => i.estado !== "rejeitado") : [];
      const pendentes = itens.filter((i) => i.estado === "aguardando_aprovacao").length;

      const badgeContagem = el("badge-fila-contagem");
      if (pendentes > 0) {
        badgeContagem.hidden = false;
        badgeContagem.textContent = String(pendentes);
      } else {
        badgeContagem.hidden = true;
      }

      const vazia = el("fila-vazia");
      const lista = el("lista-fila-autopost");
      if (itens.length === 0) {
        vazia.hidden = false;
        lista.hidden = true;
        lista.innerHTML = "";
        return;
      }

      vazia.hidden = true;
      lista.hidden = false;
      lista.innerHTML = "";
      itens.forEach((it) => lista.appendChild(criarCardFilaAutopost(it)));
    } catch (_) {}
  }

  async function carregarStatusPostar() {
    try {
      const st = await api("/api/postar/status");
      postarStatus.disponivel = Boolean(st && st.disponivel);
      const disp = postarStatus.disponivel;
      const contas = st.contas || {};
      const postiz = contas.postiz || {};
      const canais = Array.isArray(postiz.canais) ? postiz.canais : [];
      if (st.ok !== false) {
        canaisAtivosPostiz = postiz.conectado ? canais.flatMap((canal) => {
          const rede = String(canal.identifier || "").split("-")[0];
          if (canal.desativado || !canal.id || !Object.hasOwn(redesPostagem, rede)) return [];
          return [{ id: String(canal.id), nome: canal.nome, rede }];
        }) : [];
        mostrarSelecaoCanais();
      }
      const listaCanais = el("canais-postiz");
      const statusPostiz = el("status-postiz");
      statusPostiz.textContent = st.ok === false ? "Status indisponível" : !disp ? "Postagem indisponível" :
        postiz.conectado && postiz.valido === false ? "Confira a conexão" :
        postiz.conectado ? "Conectado" : "Não conectado";
      el("guia-postiz").hidden = Boolean(postiz.conectado && canais.length);
      listaCanais.replaceChildren();
      listaCanais.hidden = !postiz.conectado || canais.length === 0;
      canais.forEach((canal) => {
        const item = document.createElement("li");
        item.className = "conta-item";
        const nome = document.createElement("strong");
        nome.textContent = canal.nome || "Canal";
        const detalhe = document.createElement("span");
        detalhe.className = "conta-status-texto";
        const rede = redesPostagem[String(canal.identifier || "").split("-")[0]];
        detalhe.textContent = [rede, canal.desativado ? "Desativado" : "Conectado"].filter(Boolean).join(" · ");
        item.append(nome, detalhe);
        listaCanais.appendChild(item);
      });
      el("lista-redes-contas").hidden = Boolean(contas.postiz);
      el("aviso-postagem-direta").hidden = !contas.postiz;

      el("aviso-postar-breve").hidden = disp;
      el("selo-estado-postar").textContent = disp ? "Ativo" : "Em breve";

      ["youtube", "instagram", "tiktok"].forEach((rede) => {
        const txtEl = el(`status-rede-${rede}`);
        const btn = document.querySelector(`.btn-conectar-rede[data-rede="${rede}"]`);
        if (!disp) {
          if (txtEl) txtEl.textContent = "Em breve";
          if (btn) {
            btn.disabled = true;
            btn.querySelector("span").textContent = "Em breve";
          }
        } else {
          const infoRede = contas[rede];
          const conectada =
            typeof infoRede === "object" && infoRede !== null
              ? Boolean(infoRede.conectado)
              : Boolean(infoRede);
          const detalheRede =
            (infoRede && (infoRede.conta || infoRede.detalhe)) ||
            (conectada ? "Conta conectada" : "Não conectada");
          if (txtEl) txtEl.textContent = detalheRede;
          if (btn) {
            btn.disabled = false;
            btn.querySelector("span").textContent = conectada ? "Reconectar" : "Conectar";
          }
        }
      });

      if (estadoAtual && estadoAtual.cortes) {
        atualizarListaCortes(estadoAtual.cortes);
      }
    } catch (_) {}
  }

  async function conectarPostiz(evento) {
    evento.preventDefault();
    const campo = el("chave-postiz");
    const erro = el("erro-postiz");
    const botao = el("btn-conectar-postiz");
    const chave = campo.value.trim();
    erro.hidden = true;
    campo.removeAttribute("aria-invalid");
    if (!chave) {
      erro.textContent = "Cole a chave da seção Public API no Postiz.";
      erro.hidden = false;
      campo.setAttribute("aria-invalid", "true");
      campo.focus();
      return;
    }
    campo.disabled = true;
    botao.disabled = true;
    botao.textContent = "Conectando…";
    try {
      const res = await api("/api/postar/conectar", "POST", { rede: "postiz", chave });
      if (res.ok !== true) {
        erro.textContent = "Não foi possível conectar. Confira a chave e sua conexão com a internet.";
        erro.hidden = false;
        campo.setAttribute("aria-invalid", "true");
        return;
      }
      campo.value = "";
      await carregarStatusPostar();
    } finally {
      campo.disabled = false;
      botao.disabled = false;
      botao.textContent = "Conectar";
      if (!erro.hidden) campo.focus();
    }
  }

  async function carregarStatusNavegador() {
    if (!el("chk-reserva-navegador").checked) return;
    try {
      const res = await api("/api/autopost-navegador/status");
      const redes = (res && res.redes) || {};
      ["youtube", "instagram", "tiktok"].forEach((r) => {
        const elTxt = el(`status-nav-${r}`);
        if (!elTxt) return;
        const info = redes[r];
        if (!info) {
          elTxt.textContent = "Pronto para conectar";
        } else {
          elTxt.textContent = info.conectado ? "Sessão conectada" : (info.erro || "Não conectada");
        }
      });
    } catch (_) {}
  }

  async function recarregarTudoPostagem() {
    await Promise.all([
      carregarConfigPostagem(),
      carregarFilaAutopost(),
      carregarStatusPostar()
    ]);
  }

  async function abrirModalPostar(corte) {
    const dlg = el("modal-postar");
    const disp = Boolean(postarStatus.disponivel || postarStatus.autopost);
    el("corpo-postar-breve").hidden = disp;
    el("corpo-postar-form").hidden = !disp;
    el("btn-confirmar-postar").hidden = !postarStatus.disponivel;
    el("btn-enfileirar-postar").hidden = !postarStatus.autopost;
    el("postar-progresso-log").hidden = true;
    el("postar-progresso-log").textContent = "";

    if (disp) {
      el("postar-caminho-video").value = corte.caminho || "";
      el("postar-titulo").value = corte.titulo || "";
      el("postar-descricao").value = "";
    }

    if (typeof dlg.showModal === "function") {
      dlg.showModal();
    } else {
      dlg.setAttribute("open", "");
    }

    // Pede sugestão inteligente via cortelive.autopost gerar --video ARQ
    if (postarStatus.autopost && corte.caminho) {
      try {
        const gen = await api("/api/autopost/gerar", "POST", {
          video: corte.caminho,
          enfileirar: false
        });
        if (gen.metadados && dlg.open && el("postar-caminho-video").value === corte.caminho) {
          const yt = gen.metadados.youtube || {};
          if (yt.titulo && el("postar-titulo").value === (corte.titulo || "")) el("postar-titulo").value = yt.titulo;
          if (yt.descricao && !el("postar-descricao").value) el("postar-descricao").value = yt.descricao;
        }
      } catch (_) {}
    }
  }

  function fecharModalPostar() {
    const dlg = el("modal-postar");
    if (typeof dlg.close === "function") {
      dlg.close();
    } else {
      dlg.removeAttribute("open");
    }
  }

  // --- Atualizações (instalador\atualizar.ps1 -Checar / -Aplicar) ---
  async function checarAtualizacao() {
    if (acompanhamentoAtualizacao) return;
    try {
      const res = await api("/api/atualizacao/checar");
      if (res && res.etapa === "disponivel" && res.versao_nova) {
        el("titulo-atualizacao").textContent = `Nova versão v${res.versao_nova} disponível`;
        el("detalhe-atualizacao").textContent =
          res.msg || `Atualiza da v${res.versao_atual} para v${res.versao_nova} mantendo modelos e cortes.`;
        el("faixa-atualizacao").hidden = false;
      } else {
        el("faixa-atualizacao").hidden = true;
      }
    } catch (_) {}
  }

  function finalizarAtualizacao(res) {
    acompanhamentoAtualizacao = false;
    const botao = el("btn-aplicar-atualizacao");
    botao.disabled = false;
    botao.querySelector("span").textContent = "Atualizar";
    if (res.etapa === "concluido") {
      const url = new URL(window.location.href);
      url.searchParams.set("atualizado", res.versao_atual || "nova");
      window.location.assign(url);
    } else if (res.etapa === "rollback" || res.exit_code === 2) {
      el("faixa-atualizacao").hidden = true;
      mostrarAvisoSistema("Atualização revertida", res.erro || res.msg || "A versão anterior foi restaurada.");
    } else if (res.etapa === "em_dia") {
      el("faixa-atualizacao").hidden = true;
      mostrarAvisoSistema("O CorteLive está atualizado", res.msg || "Você já tem a versão mais recente.");
    } else {
      mostrarAvisoSistema("Não foi possível atualizar agora", res.erro || res.msg || "Tente novamente em alguns minutos.");
    }
  }

  function acompanharAtualizacao(atualizacao) {
    if (!atualizacao) return;
    if (atualizacao.rodando) {
      acompanhamentoAtualizacao = true;
      el("faixa-atualizacao").hidden = false;
      el("btn-aplicar-atualizacao").disabled = true;
      el("btn-aplicar-atualizacao").querySelector("span").textContent = "Atualizando…";
      el("titulo-atualizacao").textContent = "Atualização em andamento";
      const pct = Number.isFinite(atualizacao.pct) ? ` · ${atualizacao.pct}%` : "";
      el("detalhe-atualizacao").textContent = (atualizacao.msg || "Preparando atualização…") + pct;
    } else if (acompanhamentoAtualizacao && atualizacao.exit_code != null) {
      finalizarAtualizacao(atualizacao);
    }
  }

  async function aplicarAtualizacao() {
    const btn = el("btn-aplicar-atualizacao");
    btn.disabled = true;
    btn.querySelector("span").textContent = "Atualizando…";
    el("detalhe-atualizacao").textContent = "Preparando a atualização…";
    const res = await api("/api/atualizacao/aplicar", "POST", {});
    if (!res.rodando) {
      finalizarAtualizacao(res);
      return;
    }
    acompanhamentoAtualizacao = true;
    await carregarEstado();
  }

  // --- Loop Principal de Estado ---
  async function carregarEstado() {
    try {
      const dados = await api("/api/estado");
      if (dados.ok === false) {
        if (!estadoAtual && el("faixa-aviso-sistema").hidden) {
          mostrarAvisoSistema("Não foi possível abrir o painel", dados.erro);
        }
        agendarProximoPoll(INTERVALO_OCIOSO_MS);
        return;
      }
      estadoAtual = dados;
      acompanharAtualizacao(dados.atualizacao);

      if (dados.versao) {
        el("badge-versao").textContent = `v${dados.versao}`;
      }
      if (dados.pasta_raiz) {
        el("rodape-pasta").title = dados.pasta_raiz;
      }

      el("tela-carregando").hidden = true;

      const instalado = Boolean(dados.instalado);
      const instalandoAgora = dados.instalacao && dados.instalacao.rodando;
      el("rodape-atualizacao-info").textContent = instalado ?
        "Formato dos cortes · Vertical 9:16" : "Mantenha o painel aberto durante a instalação";

      if (!instalado || instalandoAgora) {
        el("tela-instalacao").hidden = false;
        el("tela-mesa").hidden = true;
        el("abas-principais").hidden = true;
        atualizarTelaInstalacao(dados.instalacao || {});
      } else {
        el("tela-instalacao").hidden = true;
        el("tela-mesa").hidden = false;
        el("abas-principais").hidden = false;
        atualizarMesaCorte(dados);
        atualizarListaCortes(dados.cortes || []);
      }

      const emAtividade =
        Boolean(dados.instalacao && dados.instalacao.rodando) ||
        Boolean(dados.sessao && dados.sessao.rodando) || acompanhamentoAtualizacao;
      agendarProximoPoll(emAtividade ? INTERVALO_ATIVO_MS : INTERVALO_OCIOSO_MS);
    } catch (_) {
      agendarProximoPoll(INTERVALO_OCIOSO_MS);
    }
  }

  function agendarProximoPoll(ms) {
    if (timerPoll) clearTimeout(timerPoll);
    timerPoll = setTimeout(carregarEstado, ms);
  }

  // --- Eventos de Interface ---
  function registrarEventos() {
    el("aba-btn-cortar").addEventListener("click", () => alternarAba("cortar"));
    el("aba-btn-contas").addEventListener("click", () => alternarAba("contas"));
    el("form-postiz").addEventListener("submit", conectarPostiz);

    el("btn-fechar-aviso").addEventListener("click", () => {
      el("faixa-aviso-sistema").hidden = true;
    });

    const iniciarInstalacao = async () => {
      el("bloco-erro-instalacao").hidden = true;
      el("btn-instalar").disabled = true;
      el("texto-btn-instalar").textContent = "INSTALANDO…";
      el("bloco-progresso-instalacao").hidden = false;
      el("btn-tentar-novamente").disabled = true;
      const res = await api("/api/instalar", "POST", {});
      await carregarEstado();
      el("btn-tentar-novamente").disabled = false;
      if (res.ok === false && !estadoAtual?.instalacao?.rodando) {
        atualizarTelaInstalacao({ ...estadoAtual?.instalacao, erro: res.erro });
      }
    };
    el("btn-instalar").addEventListener("click", iniciarInstalacao);
    el("btn-tentar-novamente").addEventListener("click", iniciarInstalacao);

    el("btn-copiar-log-instalacao").addEventListener("click", async () => {
      const resp = await api("/api/log-instalacao");
      await copiarTexto(resp.log || el("pre-log-instalacao").textContent, el("texto-copiar-log"), "Copiar log");
    });

    el("btn-copiar-log-sessao").addEventListener("click", async (ev) => {
      const texto =
        (estadoAtual && estadoAtual.sessao && estadoAtual.sessao.log_completo) ||
        (estadoAtual && estadoAtual.sessao && estadoAtual.sessao.erro) ||
        "Sem log da sessão.";
      await copiarTexto(texto, ev.currentTarget, "Copiar log");
    });

    document.querySelectorAll('.card-preset input[type="radio"]').forEach((radio) => {
      radio.addEventListener("change", () => {
        const nome = radio.name;
        document.querySelectorAll(`.card-preset input[name="${nome}"]`).forEach((r) => {
          r.closest(".card-preset").classList.toggle("selecionado", r.checked);
        });
      });
    });

    el("btn-escolher-video").addEventListener("click", async () => {
      const res = await api("/api/escolher-arquivo", "POST", {});
      if (res.ok === false) mostrarAvisoSistema("Não foi possível escolher o vídeo", res.erro);
      if (res && res.caminho) {
        el("campo-link").value = res.caminho;
        el("erro-campo-link").hidden = true;
        el("campo-link").focus();
      }
    });

    el("campo-link").addEventListener("input", () => {
      el("erro-campo-link").hidden = true;
    });

    el("form-cortar").addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const fonte = el("campo-link").value.trim();
      const erroEl = el("erro-campo-link");
      if (!fonte) {
        erroEl.textContent = "Cole o link da live (Twitch, YouTube ou Kick) ou escolha um vídeo para começar.";
        erroEl.hidden = false;
        el("campo-link").focus();
        return;
      }
      erroEl.hidden = true;

      const modelo = (document.querySelector('input[name="modelo"]:checked') || {}).value || "cam-em-cima";
      const legenda = (document.querySelector('input[name="legenda"]:checked') || {}).value || "opus";

      el("btn-cortar").disabled = true;
      try {
        const res = await api("/api/cortar", "POST", { fonte, modelo, legenda });
        if (res && res.erro) {
          erroEl.textContent = res.erro;
          erroEl.hidden = false;
        }
      } finally {
        el("btn-cortar").disabled = false;
      }
      await carregarEstado();
    });

    el("btn-parar").addEventListener("click", async () => {
      el("btn-parar").disabled = true;
      el("texto-btn-parar").textContent = "FINALIZANDO CORTES…";
      const res = await api("/api/parar", "POST", {});
      if (res.ok === false) mostrarAvisoSistema("Não foi possível parar o corte", res.erro);
      await carregarEstado();
    });

    el("btn-abrir-pasta-geral").addEventListener("click", async () => {
      const res = await api("/api/abrir-pasta", "POST", { caminho: "" });
      if (res.ok === false) mostrarAvisoSistema("Não foi possível abrir a pasta de cortes", res.erro);
    });

    el("btn-aplicar-atualizacao").addEventListener("click", aplicarAtualizacao);

    // Switches de automação e reserva
    el("chk-postar-sozinho").addEventListener("change", salvarInterruptoresPostagem);
    el("chk-revisar-antes").addEventListener("change", salvarInterruptoresPostagem);
    el("chk-reserva-navegador").addEventListener("change", salvarInterruptoresPostagem);
    const formLimites = el("form-limites-postagem");
    formLimites.addEventListener("input", (evento) => {
      if (evento.target.type === "number") editandoLimites = true;
    });
    formLimites.addEventListener("change", mudarLimitesPostagem);
    formLimites.addEventListener("submit", salvarLimitesPostagem);
    el("btn-recarregar-fila").addEventListener("click", carregarFilaAutopost);

    // Conectar contas oficiais (cortelive.postar)
    document.querySelectorAll(".btn-conectar-rede").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const rede = btn.getAttribute("data-rede");
        btn.disabled = true;
        try {
          const res = await api("/api/postar/conectar", "POST", { rede });
          if (res.ok === false) mostrarAvisoSistema("Não foi possível conectar a conta", res.erro);
          await carregarStatusPostar();
        } finally {
          btn.disabled = false;
        }
      });
    });

    // Conectar sessão reserva no navegador (cortelive.autopost_navegador)
    document.querySelectorAll(".btn-conectar-nav").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const rede = btn.getAttribute("data-rede");
        btn.disabled = true;
        try {
          const res = await api("/api/autopost-navegador/conectar", "POST", { rede });
          if (res.ok === false) mostrarAvisoSistema("Não foi possível conectar pelo navegador", res.erro);
          await carregarStatusNavegador();
        } finally {
          btn.disabled = false;
        }
      });
    });

    // Modal Postar
    el("btn-fechar-modal-postar").addEventListener("click", fecharModalPostar);
    el("btn-cancelar-postar").addEventListener("click", fecharModalPostar);

    el("btn-enfileirar-postar").addEventListener("click", async () => {
      const video = el("postar-caminho-video").value;
      const titulo = el("postar-titulo").value.trim();
      const descricao = el("postar-descricao").value.trim();
      if (!video) return;

      el("btn-enfileirar-postar").disabled = true;
      el("btn-confirmar-postar").disabled = true;
      const log = el("postar-progresso-log");
      log.hidden = false;
      log.textContent = "Preparando o corte para revisão…";
      try {
        const gen = await api("/api/autopost/gerar", "POST", {
          video,
          enfileirar: true
        });
        if (gen.ok === false) {
          log.textContent = gen.erro || "Não foi possível adicionar o corte à fila.";
          return;
        }
        if (gen && gen.item && gen.item.id && (titulo || descricao)) {
          const edicao = await api("/api/autopost/editar", "POST", {
            id: gen.item.id,
            titulo,
            descricao
          });
          if (edicao.ok === false) mostrarAvisoSistema("O corte entrou na fila", "Edite o título e a legenda diretamente na fila antes de aprovar.");
        }
        fecharModalPostar();
        alternarAba("contas");
      } finally {
        el("btn-enfileirar-postar").disabled = false;
        el("btn-confirmar-postar").disabled = false;
      }
    });

    el("form-modal-postar").addEventListener("submit", async (ev) => {
      ev.preventDefault();
      if (!postarStatus.disponivel) {
        fecharModalPostar();
        return;
      }
      const video = el("postar-caminho-video").value;
      const titulo = el("postar-titulo").value.trim();
      const descricao = el("postar-descricao").value.trim();
      const redes = Array.from(document.querySelectorAll('input[name="rede-destino"]:checked')).map((c) => c.value);
      const logEl = el("postar-progresso-log");
      logEl.hidden = false;
      if (redes.length === 0) {
        logEl.textContent = "Selecione pelo menos uma rede para postar.";
        return;
      }
      logEl.textContent = "Enviando o corte…";
      el("btn-confirmar-postar").disabled = true;
      el("btn-enfileirar-postar").disabled = true;
      try {
        const res = await api("/api/postar/enviar", "POST", {
          video,
          titulo,
          descricao,
          redes: redes.join(",")
        });
        logEl.textContent = res.ok === false ? (res.erro || "Não foi possível postar o vídeo.") :
          (res.mensagem || "Envio concluído. Confira os canais escolhidos.");
      } finally {
        el("btn-confirmar-postar").disabled = false;
        el("btn-enfileirar-postar").disabled = false;
      }
    });

    window.addEventListener("beforeunload", (e) => {
      if (
        (estadoAtual && estadoAtual.sessao && estadoAtual.sessao.rodando) ||
        (estadoAtual && estadoAtual.instalacao && estadoAtual.instalacao.rodando)
      ) {
        e.preventDefault();
        e.returnValue = "";
      }
    });

    setInterval(() => {
      fetch("/api/heartbeat", { method: "POST", keepalive: true }).catch(() => {});
    }, INTERVALO_HEARTBEAT_MS);

    window.addEventListener("pagehide", () => {
      if (navigator.sendBeacon) {
        navigator.sendBeacon("/api/janela-fechada", "{}");
      }
    });
  }

  document.addEventListener("DOMContentLoaded", async () => {
    iniciarTema();
    registrarEventos();

    const params = new URLSearchParams(window.location.search);
    if (params.get("atualizado")) {
      mostrarAvisoSistema(
        `CorteLive atualizado para v${params.get("atualizado")}`,
        "Seus modelos de voz, vídeos cortados e configurações foram mantidos intactos."
      );
      window.history.replaceState({}, document.title, window.location.pathname);
    }

    await carregarEstado();
    await recarregarTudoPostagem();
    await checarAtualizacao();
    setInterval(checarAtualizacao, INTERVALO_ATUALIZACAO_MS);
  });
})();
