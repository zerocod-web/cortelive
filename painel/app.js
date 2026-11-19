/* ==========================================================================
   CorteLive — Lógica do Painel (Vanilla JS, zero dependências externas)
   Integra: instalar.ps1, motor_painel.py e instalador\atualizar.ps1.
   ========================================================================== */
(function () {
  "use strict";

  const INTERVALO_ATIVO_MS = 1200;
  const INTERVALO_OCIOSO_MS = 2500;
  const INTERVALO_HEARTBEAT_MS = 3000;
  const INTERVALO_ATUALIZACAO_MS = 30 * 60 * 1000;

  let estadoAtual = null;
  let assinaturaCortes = "";
  let versaoEstadoCortes = 0;
  const acoesDestinoAtivas = new Set();
  let timerPoll = null;
  let abaAtiva = "cortar";
  let acompanhamentoAtualizacao = false;
  let aplicandoAtualizacao = false;
  let checagemAtualizacao = null;
  let salvandoPreferencias = false;
  let ultimoTotalCortesSessao = 0;
  let ultimoCorteDetectadoMs = 0;
  let ultimaAssinaturaLives = "";
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
    let txt;
    try {
      txt = await resp.text();
    } catch (_) {
      return { ok: false, erro: "A conexão com o CorteLive caiu. Tente novamente." };
    }
    let dados = {};
    try {
      dados = txt ? JSON.parse(txt) : {};
    } catch (_) {
      dados = { ok: resp.ok, texto: txt };
    }
    if (!resp.ok && !dados.erro) {
      dados.erro = `Erro HTTP ${resp.status}`;
    }
    if (!resp.ok) {
      dados.ok = false;
      dados.statusHttp = resp.status;
    }
    return dados;
  }

  function rotaEmBreve(res) {
    return [404, 405, 501].includes(res.statusHttp) || res.em_breve === true || res.etapa === "indisponivel";
  }

  function feedbackAcao(elemento, mensagem, erro = false) {
    elemento.textContent = mensagem;
    elemento.hidden = !mensagem;
    elemento.toggleAttribute("data-erro", erro);
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
    // Todo aviso de erro oferece o envio do relatório; o aviso de "arquivo salvo" não.
    el("btn-enviar-erro-aviso").hidden = titulo === "Arquivo salvo" || /^(Aviso|Atualização|O corte entrou)/i.test(titulo);
  }

  // --- Abas ---
  function alternarAba(novaAba) {
    abaAtiva = novaAba === "config" ? novaAba : "cortar";
    for (const nome of ["cortar", "config"]) {
      const btn = el(`aba-btn-${nome}`);
      btn.classList.toggle("ativa", abaAtiva === nome);
      if (abaAtiva === nome) btn.setAttribute("aria-current", "page");
      else btn.removeAttribute("aria-current");
      el(`aba-painel-${nome}`).hidden = abaAtiva !== nome;
    }
    if (abaAtiva === "config") {
      consultarGPT();
      carregarConfigCorte();
    }
  }

  function camposGPT() {
    const porApi = el("modo-gpt").value === "api";
    el("opcoes-api").hidden = !porApi;
    el("ajuda-chatgpt").hidden = el("modo-gpt").value !== "chatgpt";
    el("btn-gpt").textContent = el("modo-gpt").value === "local" ? "Usar seleção local" : "Conectar";
    el("chave-gpt").required = porApi;
    if (!porApi) el("chave-gpt").value = "";
  }

  const estadosGPT = {
    local: "Seleção local ativa.",
    conectado: "Conta ChatGPT conectada à seleção dos cortes.",
    salva: "Chave da API OpenAI validada e salva neste Windows.",
    ausente: "Instale o Codex para Windows para conectar sua conta ChatGPT.",
    desconectado: "Entre com sua conta ChatGPT pelo botão Conectar.",
    aguardando: "Conclua o login no navegador e clique em Conectar para confirmar."
  };

  let versaoFormGPT = 0;
  async function consultarGPT() {
    const versao = ++versaoFormGPT;
    const res = await api("/api/gpt/status");
    if (versao !== versaoFormGPT) return;
    if (res.ok) {
      el("modo-gpt").value = res.modo;
      camposGPT();
    }
    feedbackAcao(el("estado-gpt"), res.erro || estadosGPT[res.estado], !res.ok);
  }

  async function conectarGPT(evento) {
    evento.preventDefault();
    ++versaoFormGPT;
    const modo = el("modo-gpt");
    const chave = el("chave-gpt");
    const corpo = { modo: modo.value };
    if (modo.value === "api") corpo.chave = chave.value.trim();
    const btn = el("btn-gpt");
    btn.disabled = modo.disabled = chave.disabled = true;
    feedbackAcao(el("estado-gpt"), "Verificando conexão…");
    try {
      const res = await api("/api/gpt/conectar", "POST", corpo);
      feedbackAcao(el("estado-gpt"), res.erro || estadosGPT[res.estado], !res.ok);
      if (res.ok) chave.value = "";
    } finally {
      btn.disabled = modo.disabled = chave.disabled = false;
    }
  }

  async function enviarErroParaGabriel() {
    if (gerandoRelatorioErro) return;
    gerandoRelatorioErro = true;
    const botoes = Array.from(document.querySelectorAll(".btn-enviar-erro"));
    const rotulos = botoes.map((b) => b.querySelector(".rotulo-enviar-erro"));
    botoes.forEach((b, i) => { b.disabled = true; if (rotulos[i]) rotulos[i].textContent = "Juntando os registros…"; });

    const s = (estadoAtual && estadoAtual.sessao) || {};
    const msgErro = [s.erro, estadoAtual && estadoAtual.instalacao && estadoAtual.instalacao.erro].filter(Boolean).join(" | ");
    const corpo = {
      mensagem_erro: msgErro,
      estado: estadoAtual ? {
        versao: estadoAtual.versao, instalado: estadoAtual.instalado, instalacao: estadoAtual.instalacao,
        sessao: Object.assign({}, s, { log_completo: undefined, log_recente: undefined })
      } : null,
      log_painel: [s.log_completo, estadoAtual && estadoAtual.instalacao && estadoAtual.instalacao.log_recente]
        .filter(Boolean).map((t) => Array.isArray(t) ? t.join("\n") : String(t)).join("\n---\n")
    };
    const res = await api("/api/enviar-erro", "POST", corpo);
    botoes.forEach((b, i) => { b.disabled = false; if (rotulos[i]) rotulos[i].textContent = "Enviar erro para o Gabriel"; });
    gerandoRelatorioErro = false;
    const falhou = res.ok === false;
    const texto = falhou
      ? (res.erro || "Não consegui juntar o arquivo. Tente de novo.")
      : (res.mensagem || "Arquivo salvo na Área de Trabalho. Mande para o Gabriel pelo WhatsApp.");

    mostrarAvisoSistema(falhou ? "Não deu para salvar o arquivo" : "Arquivo salvo", texto);
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

  // --- Botão "Parar" de cada live ---
  function renderizarListaLives(sessao, rodando) {
    const ul = el("lista-lives");
    const lives = Array.isArray(sessao.lives) ? sessao.lives : [];
    if (!rodando || lives.length < 1) {
      ul.hidden = true;
      ul.textContent = "";
      return;
    }
    ul.hidden = false;
    ul.textContent = "";
    lives.forEach((lv) => {
      const li = document.createElement("li");
      li.className = "item-live";
      const nome = document.createElement("span");
      nome.className = "item-live-nome";
      nome.textContent = lv.canal || "Live";
      const est = document.createElement("span");
      est.className = "item-live-estado";
      const cortes = Number(lv.cortes) || 0;
      est.textContent = (lv.estado || "") + " · " + cortes + (cortes === 1 ? " corte" : " cortes");
      const bt = document.createElement("button");
      bt.type = "button";
      bt.className = "btn btn-perigo btn-parar-live";
      bt.dataset.liveId = lv.id;
      bt.disabled = !lv.pode_parar;
      bt.textContent = lv.pode_parar ? "Parar" : "Parada";
      bt.setAttribute("aria-label", "Parar a live " + (lv.canal || ""));
      const bloco = document.createElement("div");
      bloco.className = "item-live-texto";
      const ativ = document.createElement("span");
      ativ.className = "item-live-atividade";
      ativ.textContent = lv.atividade || "";
      ativ.hidden = !lv.atividade;
      bloco.append(nome, ativ);
      li.append(bloco, est, bt);
      ul.appendChild(li);
    });
  }

  function ehMensagemLargada(msg) {
    if (!msg) return false;
    const t = String(msg).trim();
    return (
      /^Detectando placa de vídeo/i.test(t) ||
      /^Preparando motor de corte/i.test(t) ||
      /^Iniciando captura e modelo de voz/i.test(t) ||
      /^\d{2}:\d{2}:\d{2}\s+[+~=!-]\s+/i.test(t)
    );
  }

  function ehAvisoDisco(msg) {
    if (!msg) return false;
    return /pouco espaço|disco quase lotado|libere espaço/i.test(String(msg));
  }

  // Aviso "Seu PC aguenta bem até N lives..." que o motor põe na frente da mensagem quando há lives demais.
  function avisoCapacidade(msg) {
    const m = /Seu PC aguenta bem até \d+ lives?;[^·]*/.exec(String(msg || ""));
    return m ? m[0].trim() : "";
  }

  function formatarTempoAtras(seg) {
    const s = Math.max(0, Number(seg) || 0);
    if (s < 45) return "agora mesmo";
    if (s < 3600) return `há ${Math.max(1, Math.round(s / 60))} min`;
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    return m === 0 ? `há ${h} h` : `há ${h}h${String(m).padStart(2, "0")}`;
  }

  function extrairAtrasoLive(lv) {
    if (!lv || typeof lv !== "object") return null;
    for (const chave of ["atraso_transcricao_s", "atraso_s"]) {
      const val = lv[chave];
      if (val !== null && val !== undefined && val !== "" && Number.isFinite(Number(val))) {
        return Math.max(0, Number(val));
      }
    }
    const m = String(lv.atividade || "").match(/atraso\s+(\d+(?:[.,]\d+)?)\s*s/i);
    return m ? Math.max(0, Number(m[1].replace(",", "."))) : null;
  }

  function formatarAtrasoVoz(sessao, aoVivoLives) {
    const comAtraso = aoVivoLives
      .map((lv) => ({ canal: String(lv.canal || "").trim(), atraso: extrairAtrasoLive(lv) }))
      .filter((x) => x.atraso !== null);

    if (comAtraso.length > 0) {
      const maior = comAtraso.reduce((a, b) => (b.atraso > a.atraso ? b : a));
      const menor = comAtraso.reduce((a, b) => (b.atraso < a.atraso ? b : a));
      const seg = Math.round(maior.atraso);
      const mostrarNome =
        aoVivoLives.length >= 2 &&
        Boolean(maior.canal) &&
        seg > 0 &&
        (comAtraso.length < aoVivoLives.length || Math.round(menor.atraso) !== seg);
      if (mostrarNome) {
        const curto = maior.canal.length > 18 ? `${maior.canal.slice(0, 17)}…` : maior.canal;
        return `${seg} s (${curto})`;
      }
      return `${seg} s`;
    }

    const atr = sessao.atraso_s;
    if (atr !== null && atr !== undefined && atr !== "" && Number.isFinite(Number(atr))) {
      const seg = Math.round(Math.max(0, Number(atr)));
      const canalTopo = sessao.atraso_canal && aoVivoLives.length >= 2 && seg > 0 ? String(sessao.atraso_canal).trim() : "";
      if (canalTopo) {
        const curto = canalTopo.length > 18 ? `${canalTopo.slice(0, 17)}…` : canalTopo;
        return `${seg} s (${curto})`;
      }
      return `${seg} s`;
    }
    return "—";
  }

  function detalheAtividadeLives(aoVivoLives, cortesSessao, ultimoCorteHaS) {
    if (ultimoCorteHaS !== null) {
      return `último corte ${formatarTempoAtras(ultimoCorteHaS)}`;
    }
    const emCorte = aoVivoLives.find((lv) =>
      /cortando|fazendo o corte|começando a cortar|legenda\s+\d+%/i.test(String(lv.atividade || ""))
    );
    if (emCorte && cortesSessao > 0) {
      const plural = cortesSessao === 1 ? "corte pronto" : "cortes prontos";
      return `${cortesSessao} ${plural} · cortando em ${emCorte.canal || "live"}`;
    }
    if (emCorte) {
      return `cortando em ${emCorte.canal || "live"}`;
    }
    if (cortesSessao > 0) {
      return `${cortesSessao} ${cortesSessao === 1 ? "corte pronto" : "cortes prontos"}`;
    }
    return "ouvindo em tempo real";
  }

  function montarResumoAoVivo(sessao, lives, ativas, aoVivoLives, cortesSessao, ultimoCorteHaS) {
    const avisoDisco = sessao.aviso_disco || (ehAvisoDisco(sessao.ultima_mensagem) ? String(sessao.ultima_mensagem).trim() : "")
      || avisoCapacidade(sessao.ultima_mensagem);
    if (lives.length === 0) {
      const canal = sessao.canal ? `Acompanhando ${sessao.canal}` : "Acompanhando transmissão em tempo real";
      const msg = sessao.ultima_mensagem && !ehMensagemLargada(sessao.ultima_mensagem) ? String(sessao.ultima_mensagem).trim() : "";
      if (msg.startsWith("Acompanhando ")) return msg;
      return msg ? `${canal} · ${msg}` : canal;
    }

    if (aoVivoLives.length >= 2) {
      const esperando = Math.max(0, ativas.length - aoVivoLives.length);
      const prefixo = `Acompanhando ${aoVivoLives.length} lives${esperando > 0 ? ` (${esperando} aguardando)` : ""}`;
      const detalhe = avisoDisco || detalheAtividadeLives(aoVivoLives, cortesSessao, ultimoCorteHaS);
      return `${prefixo} · ${detalhe}`;
    }

    const unica = aoVivoLives[0] || ativas[0] || lives[0];
    const canal = (unica && unica.canal) || sessao.canal || "transmissão em tempo real";
    const esperando = Math.max(0, ativas.length - 1);
    const prefixo = `Acompanhando ${canal}${esperando > 0 ? ` (${esperando} aguardando)` : ""}`;
    if (avisoDisco) return `${prefixo} · ${avisoDisco}`;
    if (ultimoCorteHaS !== null) return `${prefixo} · último corte ${formatarTempoAtras(ultimoCorteHaS)}`;
    if (unica && unica.atividade) return `${prefixo} · ${unica.atividade}`;
    const msgLimpa =
      sessao.ultima_mensagem && !ehMensagemLargada(sessao.ultima_mensagem) && !String(sessao.ultima_mensagem).startsWith("Acompanhando ")
        ? String(sessao.ultima_mensagem).trim()
        : "";
    return `${prefixo} · ${msgLimpa || detalheAtividadeLives(aoVivoLives, cortesSessao, null)}`;
  }

  // --- Mesa de Corte + Status ao Vivo ---
  function atualizarMesaCorte(dados) {
    el("info-hardware-modo").textContent = dados.modo_descricao || "Hardware detectado automaticamente";

    const sessao = dados.sessao || {};
    const rodando = Boolean(sessao.rodando);
    const estado = (sessao.estado || "parado").toLowerCase();
    const lives = Array.isArray(sessao.lives) ? sessao.lives : [];
    const ativas = lives.filter(
      (lv) => lv && lv.pode_parar !== false && !["parada", "encerrada", "erro"].includes(String(lv.estado || "").toLowerCase())
    );
    const aoVivoLives = ativas.filter((lv) => ["ao vivo", "gravando"].includes(String(lv.estado || "").toLowerCase()));
    const cortesSessao =
      sessao.cortes_sessao !== null && sessao.cortes_sessao !== undefined
        ? Number(sessao.cortes_sessao) || 0
        : lives.reduce((acc, lv) => acc + (Number(lv && lv.cortes) || 0), 0);
    const maiorLiveS = Math.max(
      Number(sessao.live_s) || 0,
      ...lives.map((lv) => Number(lv && lv.live_s) || 0)
    );

    const assinaturaLives = lives.map((lv) => (lv && (lv.id || lv.canal)) || "").join("|");
    if (!rodando || assinaturaLives !== ultimaAssinaturaLives || cortesSessao < ultimoTotalCortesSessao) {
      ultimaAssinaturaLives = rodando ? assinaturaLives : "";
      ultimoTotalCortesSessao = rodando ? cortesSessao : 0;
      ultimoCorteDetectadoMs = 0;
    } else if (cortesSessao > ultimoTotalCortesSessao) {
      ultimoTotalCortesSessao = cortesSessao;
      ultimoCorteDetectadoMs = Date.now();
    }

    let ultimoCorteHaS = null;
    if (
      sessao.ultimo_corte_ha_s !== null &&
      sessao.ultimo_corte_ha_s !== undefined &&
      sessao.ultimo_corte_ha_s !== "" &&
      Number.isFinite(Number(sessao.ultimo_corte_ha_s))
    ) {
      ultimoCorteHaS = Math.max(0, Number(sessao.ultimo_corte_ha_s));
    } else if (ultimoCorteDetectadoMs > 0) {
      ultimoCorteHaS = Math.max(0, (Date.now() - ultimoCorteDetectadoMs) / 1000);
    }

    const btnCortar = el("btn-cortar");
    const btnParar = el("btn-parar");
    const campoLink = el("campo-link");

    const encerrandoSessao = estado === "encerrando" || estado === "encerrada";
    const emTransmissao = !encerrandoSessao && (estado === "ao vivo" || estado === "gravando" || aoVivoLives.length > 0);
    // Com live rodando o Cortar continua disponível: cola outro link e entra mais uma live na mesma sessão.
    btnCortar.hidden = false;
    btnParar.hidden = !rodando;
    renderizarListaLives(sessao, rodando);
    const finalizando = estado === "encerrando" || estado === "encerrada";
    btnParar.disabled = finalizando;
    el("texto-btn-parar").textContent = finalizando ? "FINALIZANDO CORTES…" : "PARAR SESSÃO";
    campoLink.disabled = encerrandoSessao;
    btnCortar.disabled = encerrandoSessao;
    el("texto-btn-cortar").textContent = rodando ? "ADICIONAR LINK" : "CORTAR";
    el("btn-escolher-video").disabled = encerrandoSessao;
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
      resumo.textContent = sessao.ultima_mensagem || "Cole o link da live ou do vídeo e clique em Cortar.";
      metricas.hidden = true;
    } else if (emTransmissao) {
      pilula.classList.add("tally-ao-vivo");
      txtTally.textContent = aoVivoLives.length && aoVivoLives.every((lv) => lv.video_gravado) ? "PROCESSANDO VÍDEO" : "AO VIVO";
      resumo.textContent = montarResumoAoVivo(sessao, lives, ativas, aoVivoLives, cortesSessao, ultimoCorteHaS);
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
      if (ativas.length >= 2 && ativas.every((lv) => String(lv.estado || "").toLowerCase() === "esperando live")) {
        resumo.textContent = `Aguardando ${ativas.length} lives entrarem ao vivo…`;
      } else if (ativas.length >= 2 && estado === "iniciando") {
        resumo.textContent = `Preparando ${ativas.length} lives e modelo de voz…`;
      } else {
        resumo.textContent = sessao.ultima_mensagem || "Iniciando captura e modelo de voz…";
      }
      metricas.hidden = false;
    }

    if (rodando) {
      el("metrica-tempo-live").textContent = formatarHms(maiorLiveS);
      el("metrica-cortes-sessao").textContent = String(cortesSessao);
      el("metrica-atraso-voz").textContent = formatarAtrasoVoz(sessao, aoVivoLives);
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

  // Prévia ao passar o mouse (v1.0.17): uma tira de 8 quadros do corte, trocada a cada 350 ms. A tira só é
  // pedida na primeira passada do mouse; sem ela (corte antigo sem prévia), nada acontece.
  const QUADROS_PREVIA = 8;
  const movimentoReduzido = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  function ativarPrevia(slate, url) {
    if (movimentoReduzido) return;
    let tira = null;
    let timer = null;
    let quadro = 0;
    const parar = () => {
      clearInterval(timer);
      timer = null;
      if (tira) tira.hidden = true;
    };
    slate.addEventListener("mouseenter", () => {
      if (!tira) {
        tira = document.createElement("div");
        tira.className = "corte-previa";
        tira.hidden = true;
        const img = new Image();
        img.addEventListener("load", () => {
          tira.style.backgroundImage = `url("${url}")`;
          tira.dataset.pronta = "1";
          if (slate.matches(":hover")) iniciar();
        }, { once: true });
        img.addEventListener("error", () => { tira.dataset.falhou = "1"; }, { once: true });
        img.src = url;
        slate.append(tira);
      } else if (tira.dataset.pronta) {
        iniciar();
      }
    });
    slate.addEventListener("mouseleave", parar);
    function iniciar() {
      if (timer || !tira?.dataset.pronta) return;
      quadro = 0;
      tira.hidden = false;
      const passo = () => {
        tira.style.backgroundPosition = `${(quadro / (QUADROS_PREVIA - 1)) * 100}% 0`;
        quadro = (quadro + 1) % QUADROS_PREVIA;
      };
      passo();
      timer = setInterval(passo, 350);
    }
  }

  // --- Lista de Cortes ---
  function criarCardCorte(c) {
    const card = document.createElement("article");
    card.className = "corte-card";

    const durTxt = c.duracao > 0 ? formatarHms(c.duracao).replace(/^00:/, "") : "MP4";
    const tempoTxt = c.inicio_hms ? `Momento ${c.inicio_hms}` : (c.data_hora || "");
    const titulo = c.titulo || c.nome_arquivo;

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

    meta.appendChild(linhaCanal);
    meta.appendChild(h3);
    // v1.0.17: ver o corte sem abrir — nota em destaque na miniatura, primeira frase e prévia ao passar o mouse
    const nota = notaDoCorte(c);
    if (nota !== null) {
      const selo = document.createElement("span");
      selo.className = "corte-nota-selo";
      selo.dataset.faixa = nota >= 8 ? "alta" : nota >= 6 ? "media" : "baixa";
      selo.textContent = String(nota);
      selo.setAttribute("aria-label", `Nota do GPT ${nota} de 10`);
      slate.append(selo);
    }
    if (c.frase) {
      const frase = document.createElement("p");
      frase.className = "corte-frase";
      frase.textContent = `“${c.frase}”`;
      meta.appendChild(frase);
    }
    if (nota !== null) {
      const notaGPT = document.createElement("p");
      notaGPT.className = "ajuda-campo";
      const ajustada = Number.isInteger(c.nota_original) && c.nota_original !== nota && c.ajuste_nota;
      notaGPT.textContent = ajustada
        ? `Nota ${nota}/10 · o GPT deu ${c.nota_original}, ajustada pelo que você rejeitou antes`
        : `Avaliado pelo GPT · ${nota}/10`;
      if (ajustada && c.ajuste_nota.motivo) notaGPT.title = c.ajuste_nota.motivo;
      meta.appendChild(notaGPT);
    }
    if (c.previa && c.previa.startsWith("/api/miniatura?")) ativarPrevia(slate, c.previa);
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

    const btnDrive = document.createElement("button");
    btnDrive.type = "button";
    btnDrive.className = "btn btn-secundario btn-compacto";
    btnDrive.textContent = "Enviar ao Drive";
    btnDrive.dataset.acaoDestino = "drive";
    const feedback = document.createElement("p");
    feedback.className = "ajuda-campo";
    feedback.dataset.feedbackDestino = "";
    feedback.setAttribute("role", "status");
    feedback.hidden = true;
    btnDrive.addEventListener("click", () => alterarDestinoCorte(c, btnDrive, feedback, "/api/drive/enviar", {}, avisoDriveIndisponivel));

    const destino = document.createElement("div");
    destino.className = "corte-destino";
    if (c.destino?.estado === "copiado" || c.destino?.estado === "erro") {
      const estadoCopia = document.createElement("p");
      const falhou = c.destino.estado === "erro";
      estadoCopia.className = falhou ? "ajuda-campo" : "corte-destino-status";
      feedbackAcao(estadoCopia, falhou ? c.destino.erro || "Não foi possível copiar para o Drive." : "Copiado para a pasta do Drive", falhou);
      destino.append(estadoCopia);
    }
    destino.append(feedback);

    // "Não quero" (v1.0.17): some na hora, com Desfazer por alguns segundos; depois vai à Lixeira do Windows.
    const btnExcluir = document.createElement("button");
    btnExcluir.type = "button";
    btnExcluir.className = "btn btn-secundario btn-compacto btn-nao-quero";
    btnExcluir.innerHTML = `
      <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <path d="M3 6h18"/><path d="M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6M14 11v6"/>
      </svg>
      <span>Não quero</span>
    `;
    btnExcluir.dataset.acaoDestino = "excluir";
    btnExcluir.setAttribute("aria-label", "Não quero o corte " + titulo + " (vai para a Lixeira)");
    btnExcluir.addEventListener("click", () => naoQuero(c));
    const btnFoco = document.createElement("button");
    btnFoco.type = "button";
    btnFoco.className = "btn btn-secundario btn-compacto";
    btnFoco.textContent = "Ajustar foco";
    btnFoco.setAttribute("aria-label", "Ajustar câmera e gameplay do corte " + titulo);
    btnFoco.addEventListener("click", () => window.CorteLiveFoco?.abrir(c));
    acoes.append(btnVideo, btnPasta, btnFoco, btnDrive, btnExcluir);

    card.append(topo, acoes, destino);
    if (acoesDestinoAtivas.has(c.caminho)) {
      card.querySelectorAll("[data-acao-destino]").forEach((controle) => { controle.disabled = true; });
    }
    return card;
  }

  const avisoDriveIndisponivel = "O envio para o Google Drive ainda não está disponível nesta versão. Você pode abrir a pasta do corte e copiá-lo manualmente.";

  function erroDestino(res, indisponivel) {
    // Uma rota disponível também usa 404 para informar que o corte sumiu.
    const corteAusente = res.statusHttp === 404 && /^(Corte|Vídeo) não encontrado/i.test(res.erro || "");
    if (res.statusHttp === 503) return "Esta função está indisponível agora. Feche e abra o CorteLive e tente de novo.";
    return !corteAusente && rotaEmBreve(res) ? indisponivel : res.erro || "Não foi possível concluir. Tente de novo.";
  }

  async function alterarDestinoCorte(corte, botao, feedback, rota, corpo, indisponivel) {
    if (acoesDestinoAtivas.has(corte.caminho)) return;
    acoesDestinoAtivas.add(corte.caminho);
    const card = botao.closest(".corte-card");
    const botoes = card.querySelectorAll("[data-acao-destino]");
    const rotulo = botao.textContent;
    const devolverFoco = document.activeElement === botao;
    botoes.forEach((controle) => { controle.disabled = true; });
    const excluindo = rota === "/api/cortes/excluir";
    botao.textContent = excluindo ? "Excluindo…" : "Copiando…";
    feedbackAcao(feedback, "");

    const res = await api(rota, "POST", { caminho: corte.caminho, ...corpo });
    acoesDestinoAtivas.delete(corte.caminho);
    assinaturaCortes = "";
    botao.textContent = rotulo;
    const grade = el("lista-cortes");
    const encontrarCard = () => Array.from(grade.children).find((item) => item.dataset.caminho === corte.caminho);
    const cardVisivel = encontrarCard();
    cardVisivel?.querySelectorAll("[data-acao-destino]").forEach((controle) => { controle.disabled = false; });
    const restaurarFoco = devolverFoco && [botao, document.body].includes(document.activeElement);
    if (res.ok !== true) {
      feedbackAcao(cardVisivel?.querySelector("[data-feedback-destino]") || el("feedback-corte"), erroDestino(res, indisponivel), true);
      if (restaurarFoco) botao.focus();
      return;
    }

    versaoEstadoCortes += 1;
    if (excluindo) {
      estadoAtual.cortes = estadoAtual.cortes.filter((item) => item.caminho !== corte.caminho);
      atualizarListaCortes(estadoAtual.cortes);
      feedbackAcao(el("feedback-corte"), res.mensagem);
      if (restaurarFoco) grade.querySelector("button")?.focus();
      return;
    }
    const alteracoes = { destino: res.destino };
    if (estadoAtual?.cortes) {
      estadoAtual.cortes = estadoAtual.cortes.map((item) => item.caminho === corte.caminho ? { ...item, ...alteracoes } : item);
      atualizarListaCortes(estadoAtual.cortes);
    }
    const atualizado = encontrarCard();
    feedbackAcao(atualizado?.querySelector("[data-feedback-destino]") || el("feedback-corte"), res.mensagem || "Alteração salva.");
    if (restaurarFoco) atualizado?.querySelector(`[data-acao-destino="${botao.dataset.acaoDestino}"]`)?.focus();
  }

  // Filtro de nota do GPT e exclusão em lote (v1.0.17, pedido do Gabriel).
  // "Todas" mostra também os cortes sem nota; uma faixa mostra só os cortes com nota nessa faixa.
  const filtroNota = { todas: true, min: 0, max: 10 };
  let ultimaListaCortes = [];
  let excluindoLote = false;

  function notaDoCorte(c) {
    return Number.isInteger(c.nota_juiz) && c.nota_juiz >= 0 && c.nota_juiz <= 10 ? c.nota_juiz : null;
  }

  function passaNoFiltro(c) {
    if (filtroNota.todas) return true;
    const nota = notaDoCorte(c);
    return nota !== null && nota >= filtroNota.min && nota <= filtroNota.max;
  }

  function textoFaixa() {
    return filtroNota.min === filtroNota.max ? `nota ${filtroNota.min}` : `nota de ${filtroNota.min} a ${filtroNota.max}`;
  }

  function aplicarFiltroNotas(novo) {
    Object.assign(filtroNota, novo);
    if (filtroNota.min > filtroNota.max) [filtroNota.min, filtroNota.max] = [filtroNota.max, filtroNota.min];
    el("filtro-nota-min").value = String(filtroNota.min);
    el("filtro-nota-max").value = String(filtroNota.max);
    el("filtro-notas").querySelectorAll(".filtro-chip").forEach((chip) => {
      const [min, max] = chip.dataset.filtro === "todas" ? [] : chip.dataset.filtro.split("-").map(Number);
      const ativo = chip.dataset.filtro === "todas" ? filtroNota.todas
        : !filtroNota.todas && min === filtroNota.min && max === filtroNota.max;
      chip.setAttribute("aria-pressed", String(ativo));
    });
    try {
      localStorage.setItem("cortelive_filtro_nota", JSON.stringify(filtroNota));
    } catch (_) {}
    assinaturaCortes = "";
    atualizarListaCortes(estadoAtual?.cortes || ultimaListaCortes);
  }

  function iniciarFiltroNotas() {
    for (const id of ["filtro-nota-min", "filtro-nota-max"]) {
      const sel = el(id);
      for (let n = 0; n <= 10; n += 1) {
        const opcao = document.createElement("option");
        opcao.value = String(n);
        opcao.textContent = String(n);
        sel.appendChild(opcao);
      }
      sel.addEventListener("change", () => aplicarFiltroNotas({
        todas: false, min: Number(el("filtro-nota-min").value), max: Number(el("filtro-nota-max").value),
      }));
    }
    el("filtro-notas").querySelectorAll(".filtro-chip").forEach((chip) => {
      chip.addEventListener("click", () => {
        if (chip.dataset.filtro === "todas") return aplicarFiltroNotas({ todas: true, min: 0, max: 10 });
        const [min, max] = chip.dataset.filtro.split("-").map(Number);
        aplicarFiltroNotas({ todas: false, min, max });
      });
    });
    el("btn-excluir-filtrados").addEventListener("click", excluirFiltrados);
    let salvo = null;
    try {
      salvo = JSON.parse(localStorage.getItem("cortelive_filtro_nota") || "null");
    } catch (_) {}
    const valido = (n) => Number.isInteger(n) && n >= 0 && n <= 10;
    aplicarFiltroNotas(salvo && valido(salvo.min) && valido(salvo.max)
      ? { todas: salvo.todas !== false, min: salvo.min, max: salvo.max } : { todas: true, min: 0, max: 10 });
  }

  function atualizarBarraFiltro(totalTodos, filtrados) {
    el("filtro-notas").hidden = totalTodos === 0;
    const botao = el("btn-excluir-filtrados");
    const n = filtrados.length;
    botao.hidden = filtroNota.todas || (n === 0 && !excluindoLote);
    botao.disabled = excluindoLote;
    if (!excluindoLote) botao.textContent = n === 1 ? "Excluir o corte filtrado" : `Excluir os ${n} cortes filtrados`;
    const vazioFiltro = el("filtro-sem-cortes");
    vazioFiltro.hidden = totalTodos === 0 || n > 0;
    vazioFiltro.textContent = `Nenhum corte com ${textoFaixa()}.`;
  }

  // "Não quero": o corte some da lista na hora e só vai à Lixeira depois de PRAZO_DESFAZER_MS.
  // Se o painel fechar antes, o corte fica (nunca apaga sem querer).
  const PRAZO_DESFAZER_MS = 7000;
  const naoQueroPendentes = new Map();   // caminho -> { corte, timer }

  function avisoDesfazer() {
    let aviso = el("aviso-desfazer");
    if (aviso) return aviso;
    aviso = document.createElement("div");
    aviso.id = "aviso-desfazer";
    aviso.className = "aviso-desfazer";
    aviso.setAttribute("role", "status");
    aviso.hidden = true;
    const texto = document.createElement("span");
    texto.className = "aviso-desfazer-texto";
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn btn-secundario btn-compacto";
    btn.textContent = "Desfazer";
    btn.addEventListener("click", desfazerNaoQuero);
    aviso.append(texto, btn);
    document.body.append(aviso);
    return aviso;
  }

  function mostrarAvisoDesfazer() {
    const aviso = avisoDesfazer();
    const n = Array.from(naoQueroPendentes.values()).filter((p) => !p.enviando).length;
    aviso.hidden = n === 0;
    if (n === 0) return;
    aviso.querySelector(".aviso-desfazer-texto").textContent =
      n === 1 ? "Corte retirado. Ele vai para a Lixeira em instantes." : `${n} cortes retirados. Eles vão para a Lixeira em instantes.`;
  }

  function naoQuero(corte) {
    if (naoQueroPendentes.has(corte.caminho) || acoesDestinoAtivas.has(corte.caminho)) return;
    const timer = setTimeout(() => confirmarNaoQuero(corte.caminho), PRAZO_DESFAZER_MS);
    naoQueroPendentes.set(corte.caminho, { corte, timer });
    assinaturaCortes = "";
    atualizarListaCortes(estadoAtual?.cortes || ultimaListaCortes);
    mostrarAvisoDesfazer();
    avisoDesfazer().querySelector("button").focus();
  }

  function desfazerNaoQuero() {
    for (const [caminho, p] of naoQueroPendentes) {
      if (p.enviando) continue;        // já foi para a Lixeira: restaurar por lá
      clearTimeout(p.timer);
      naoQueroPendentes.delete(caminho);
    }
    assinaturaCortes = "";
    atualizarListaCortes(estadoAtual?.cortes || ultimaListaCortes);
    mostrarAvisoDesfazer();
    feedbackAcao(el("feedback-corte"), "Corte devolvido à lista.");
  }

  async function confirmarNaoQuero(caminho) {
    const pendente = naoQueroPendentes.get(caminho);
    if (!pendente || pendente.enviando) return;
    pendente.enviando = true;          // continua fora da lista até a Lixeira responder
    mostrarAvisoDesfazer();
    acoesDestinoAtivas.add(caminho);
    const res = await api("/api/cortes/excluir", "POST", { caminho, rejeitado: true, origem: "nao_quero" });
    acoesDestinoAtivas.delete(caminho);
    naoQueroPendentes.delete(caminho);
    mostrarAvisoDesfazer();
    versaoEstadoCortes += 1;
    if (res.ok === true) {
      if (estadoAtual?.cortes) estadoAtual.cortes = estadoAtual.cortes.filter((item) => item.caminho !== caminho);
      ultimaListaCortes = ultimaListaCortes.filter((item) => item.caminho !== caminho);
    } else {
      feedbackAcao(el("feedback-corte"), erroDestino(res, "A exclusão está indisponível nesta versão."), true);
    }
    assinaturaCortes = "";
    atualizarListaCortes(estadoAtual?.cortes || ultimaListaCortes);
  }

  async function excluirFiltrados() {
    if (excluindoLote || filtroNota.todas) return;
    const alvo = ultimaListaCortes.filter(passaNoFiltro)
      .filter((c) => !acoesDestinoAtivas.has(c.caminho) && !naoQueroPendentes.has(c.caminho));
    const n = alvo.length;
    if (!n) return;
    const quantos = n === 1 ? "1 corte" : `${n} cortes`;
    if (!window.confirm(`Excluir ${quantos} com ${textoFaixa()}?\n\nOs vídeos vão para a Lixeira do Windows. Para desfazer, restaure-os pela Lixeira.`)) return;

    excluindoLote = true;
    const botao = el("btn-excluir-filtrados");
    botao.disabled = true;
    alvo.forEach((c) => acoesDestinoAtivas.add(c.caminho));
    el("lista-cortes").querySelectorAll(".corte-card").forEach((card) => {
      if (acoesDestinoAtivas.has(card.dataset.caminho)) {
        card.querySelectorAll("[data-acao-destino]").forEach((controle) => { controle.disabled = true; });
      }
    });
    let enviados = 0;
    const falhas = [];
    for (const [i, c] of alvo.entries()) {
      botao.textContent = `Excluindo ${i + 1} de ${n}…`;
      const res = await api("/api/cortes/excluir", "POST", { caminho: c.caminho, rejeitado: true, origem: "filtro_nota" });
      acoesDestinoAtivas.delete(c.caminho);
      versaoEstadoCortes += 1;
      if (res.ok === true) {
        enviados += 1;
        if (estadoAtual?.cortes) estadoAtual.cortes = estadoAtual.cortes.filter((item) => item.caminho !== c.caminho);
        ultimaListaCortes = ultimaListaCortes.filter((item) => item.caminho !== c.caminho);
      } else {
        falhas.push(erroDestino(res, "A exclusão está indisponível nesta versão."));
      }
    }
    excluindoLote = false;
    assinaturaCortes = "";
    atualizarListaCortes(estadoAtual?.cortes || ultimaListaCortes);
    const ok = enviados === 1 ? "1 corte enviado à Lixeira do Windows" : `${enviados} cortes enviados à Lixeira do Windows`;
    feedbackAcao(el("feedback-corte"), falhas.length
      ? `${ok}. ${falhas.length === 1 ? "1 não pôde" : falhas.length + " não puderam"} ser excluídos: ${falhas[0]}`
      : `${ok}. Para desfazer, restaure pela Lixeira.`, falhas.length > 0);
  }

  // --- Espaço em disco (v1.0.17): avisa antes de lotar e oferece limpar o que é do CorteLive ---
  const fmtGB = (bytes) => (bytes / 1024 ** 3).toFixed(1).replace(".", ",");
  let ultimoEspaco = null;

  async function carregarEspaco() {
    const r = await api("/api/espaco");
    if (r.ok === false || typeof r.livre_gb !== "number") return;
    ultimoEspaco = r;
    const aviso = el("aviso-espaco");
    aviso.hidden = r.nivel === "ok";
    if (aviso.hidden) return;
    aviso.dataset.nivel = r.nivel;
    const livre = r.livre_gb.toFixed(1).replace(".", ",");
    el("aviso-espaco-titulo").textContent = r.nivel === "critico"
      ? `Disco quase lotado: ${livre} GB livres` : `Pouco espaço no disco: ${livre} GB livres`;
    const partes = [];
    if (r.lixeira.itens) partes.push(`${r.lixeira.itens === 1 ? "1 corte" : r.lixeira.itens + " cortes"} na Lixeira (${fmtGB(r.lixeira.bytes)} GB)`);
    if (r.originais.itens) partes.push(`${r.originais.itens === 1 ? "1 original" : r.originais.itens + " originais"} com mais de ${r.originais.dias} dias (${fmtGB(r.originais.bytes)} GB)`);
    const total = r.lixeira.bytes + r.originais.bytes;
    el("aviso-espaco-detalhe").textContent = total > 0
      ? `Dá para liberar ${fmtGB(total)} GB: ${partes.join(" e ")}.`
      : "Nada do CorteLive para liberar agora. Apague cortes antigos ou libere espaço em outras pastas.";
    const btn = el("btn-limpar-espaco");
    btn.hidden = total <= 0;
    btn.textContent = `Liberar ${fmtGB(total)} GB`;
  }

  async function limparEspaco() {
    const r = ultimoEspaco;
    if (!r) return;
    const itens = [];
    if (r.lixeira.itens) itens.push(`• ${r.lixeira.itens} corte(s) que você já mandou para a Lixeira`);
    if (r.originais.itens) itens.push(`• ${r.originais.itens} original(is) com mais de ${r.originais.dias} dias (o “Ajustar foco” desses cortes deixa de funcionar)`);
    if (!window.confirm(`Apagar de vez, sem volta?\n\n${itens.join("\n")}\n\nOs cortes prontos na lista não são apagados.`)) return;
    const btn = el("btn-limpar-espaco");
    btn.disabled = true;
    btn.textContent = "Liberando…";
    const res = await api("/api/espaco/limpar", "POST", { lixeira: true, originais: true });
    btn.disabled = false;
    feedbackAcao(el("feedback-corte"), res.ok === false ? (res.erro || "Não consegui liberar espaço.") : res.mensagem, res.ok === false);
    await carregarEspaco();
  }

  // --- Aprender com o "Não quero" (configurações) ---
  async function carregarAprendizado() {
    const r = await api("/api/aprendizado");
    const status = el("aprendizado-status");
    if (r.ok === false || typeof r.rejeitados !== "number") {
      status.textContent = "Não foi possível ler o aprendizado agora.";
      return;
    }
    const n = r.rejeitados;
    status.textContent = r.ativo
      ? `Aprendendo com ${n} ${n === 1 ? "corte rejeitado" : "cortes rejeitados"}.`
      : `Aprendendo com ${n} ${n === 1 ? "corte rejeitado" : "cortes rejeitados"}. Começa a ajustar as notas a partir de ${r.minimo}.`;
    el("btn-zerar-aprendizado").disabled = n === 0;
  }

  async function zerarAprendizado() {
    if (!window.confirm("Zerar o aprendizado? As notas deixam de ser ajustadas até você rejeitar 15 cortes de novo. O histórico atual fica guardado num arquivo à parte.")) return;
    const r = await api("/api/aprendizado/zerar", "POST", {});
    feedbackAcao(el("feedback-aprendizado"), r.ok === false ? (r.erro || "Não foi possível zerar.") : r.mensagem, r.ok === false);
    await carregarAprendizado();
  }

  function iniciarTriagem() {
    el("btn-limpar-espaco").addEventListener("click", limparEspaco);
    el("btn-zerar-aprendizado").addEventListener("click", zerarAprendizado);
    el("aba-btn-config")?.addEventListener("click", carregarAprendizado);
    carregarAprendizado();
    carregarEspaco();
    setInterval(carregarEspaco, 120000);
  }

  function atualizarListaCortes(cortes) {
    const recebidos = Array.isArray(cortes) ? cortes : [];
    ultimaListaCortes = recebidos;
    const todos = recebidos.filter((c) => !naoQueroPendentes.has(c.caminho));
    const lista = todos.filter(passaNoFiltro);
    const total = lista.length;
    el("contador-cortes-total").textContent = filtroNota.todas
      ? (total === 1 ? "1 corte" : `${total} cortes`)
      : `${total} de ${todos.length} ${todos.length === 1 ? "corte" : "cortes"}`;
    atualizarBarraFiltro(todos.length, lista);

    const vazio = el("estado-sem-cortes");
    const grade = el("lista-cortes");
    const novaAssinatura = JSON.stringify([total, lista, filtroNota]);
    if (novaAssinatura === assinaturaCortes) return;
    assinaturaCortes = novaAssinatura;

    vazio.hidden = todos.length > 0;
    grade.hidden = total === 0;
    if (!total) {
      grade.replaceChildren();
      return;
    }

    const cards = new Map(Array.from(grade.children, (card) => [card.dataset.caminho, card]));
    const mostrados = lista.map((c) => {
      const anterior = cards.get(c.caminho);
      const assinatura = JSON.stringify(c);
      if (anterior && (anterior.dataset.assinatura === assinatura || acoesDestinoAtivas.has(c.caminho))) return anterior;
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

  async function carregarConfigCorte() {
    try {
      const cfg = await api("/api/config-postagem");
      if (cfg.ok === false || salvandoPreferencias) return;
      el("sel-duracao-minima").value = ["livre", "30", "45", "61"].includes(String(cfg.duracao_minima_modo)) ? String(cfg.duracao_minima_modo) : "61";
    } catch (_) {}
  }

  async function salvarConfigCorte() {
    if (salvandoPreferencias) return;
    salvandoPreferencias = true;
    const seletor = el("sel-duracao-minima");
    seletor.disabled = true;
    let resp = null;
    try {
      resp = await api("/api/config-postagem", "POST", { duracao_minima_modo: seletor.value });
    } finally {
      salvandoPreferencias = false;
      seletor.disabled = false;
    }
    if (resp?.ok === false) {
      mostrarAvisoSistema("Não foi possível salvar as preferências", resp.erro);
      await carregarConfigCorte();
    }
  }

  // --- Atualizações (instalador\atualizar.ps1 -Checar / -Aplicar) ---
  function versaoComPrefixo(versao) {
    return `v${String(versao).replace(/^v/i, "")}`;
  }

  function exibirFaixaAtualizacao(visivel) {
    el("faixa-atualizacao").hidden = !visivel;
    el("btn-checar-atualizacao").setAttribute("aria-expanded", String(visivel));
  }

  function statusAtualizacao(titulo, detalhe = "", podeAtualizar = false) {
    el("titulo-atualizacao").textContent = titulo;
    el("detalhe-atualizacao").textContent = detalhe;
    el("detalhe-atualizacao").hidden = !detalhe;
    el("btn-aplicar-atualizacao").disabled = !podeAtualizar;
    el("progresso-atualizacao").hidden = true;
  }

  async function checarAtualizacao(manual = false) {
    if (aplicandoAtualizacao || acompanhamentoAtualizacao) {
      if (manual) exibirFaixaAtualizacao(true);
      return;
    }
    if (manual) {
      exibirFaixaAtualizacao(true);
      statusAtualizacao("Verificando atualizações…");
    }
    if (!checagemAtualizacao) checagemAtualizacao = api("/api/atualizacao/checar");
    const consulta = checagemAtualizacao;
    const res = await consulta;
    if (checagemAtualizacao === consulta) checagemAtualizacao = null;
    if (aplicandoAtualizacao || acompanhamentoAtualizacao) return;

    const disponivel = res.etapa === "disponivel" && res.versao_nova && res.ok !== false;
    const emDia = res.etapa === "em_dia" && res.ok !== false;
    if (disponivel || emDia) el("ponto-atualizacao").hidden = !disponivel;
    if (el("faixa-atualizacao").hidden) return;
    if (disponivel) {
      statusAtualizacao(
        `Nova versão ${versaoComPrefixo(res.versao_nova)} disponível`,
        "Seus modelos, cortes e configurações são mantidos.",
        true
      );
    } else if (emDia) {
      const versao = res.versao_atual || estadoAtual?.versao;
      statusAtualizacao(versao ? `Você está na versão mais nova (${versaoComPrefixo(versao)})` : "Você está na versão mais nova");
    } else if (rotaEmBreve(res)) {
      statusAtualizacao("Atualizações indisponíveis nesta versão", "Você pode continuar usando o painel normalmente.");
    } else {
      statusAtualizacao("Não foi possível verificar atualizações", res.erro || res.msg || "Tente novamente em alguns minutos.");
    }
  }

  function finalizarAtualizacao(res) {
    acompanhamentoAtualizacao = false;
    el("btn-fechar-atualizacao").disabled = false;
    if (res.etapa === "concluido") {
      const versao = res.versao_atual || res.versao_nova || "nova";
      statusAtualizacao(
        `Pronto! ${versao === "nova" ? "Versão nova" : versaoComPrefixo(versao)} instalada`,
        "O CorteLive vai fechar e abrir de novo sozinho em alguns segundos. Seus modelos, cortes e configurações continuam aqui."
      );
      const progresso = el("progresso-atualizacao");
      progresso.value = 100;
      progresso.hidden = false;
      el("btn-fechar-atualizacao").disabled = true;
      // Se por algum motivo a janela não for reaberta sozinha, recarrega a tela na versão nova.
      setTimeout(() => {
        const url = new URL(window.location.href);
        url.searchParams.set("atualizado", versao);
        window.location.assign(url);
      }, 30000);
    } else if (res.etapa === "rollback" || res.exit_code === 2) {
      statusAtualizacao("Atualização revertida", res.erro || res.msg || "A versão anterior foi restaurada.");
    } else if (res.etapa === "em_dia") {
      el("ponto-atualizacao").hidden = true;
      const versao = res.versao_atual || estadoAtual?.versao;
      statusAtualizacao(versao ? `Você está na versão mais nova (${versaoComPrefixo(versao)})` : "Você está na versão mais nova");
    } else if (rotaEmBreve(res)) {
      statusAtualizacao("Atualizações indisponíveis nesta versão", "Você pode continuar usando o painel normalmente.");
    } else {
      statusAtualizacao("Não foi possível atualizar agora", res.erro || res.msg || "Tente novamente em alguns minutos.", true);
    }
    if (document.activeElement === document.body) el("btn-checar-atualizacao").focus();
  }

  function mostrarProgressoAtualizacao(atualizacao) {
    exibirFaixaAtualizacao(true);
    const pct = Number.isFinite(atualizacao.pct) ? Math.max(0, Math.min(100, atualizacao.pct)) : null;
    statusAtualizacao(
      "Atualização em andamento",
      (atualizacao.msg || "Preparando atualização…") + (pct === null ? "" : ` · ${pct}%`)
    );
    const progresso = el("progresso-atualizacao");
    if (pct === null) progresso.removeAttribute("value");
    else progresso.value = pct;
    progresso.hidden = false;
    el("btn-fechar-atualizacao").disabled = true;
  }

  function acompanharAtualizacao(atualizacao) {
    if (!atualizacao) return;
    if (atualizacao.rodando) {
      acompanhamentoAtualizacao = true;
      mostrarProgressoAtualizacao(atualizacao);
    } else if (!aplicandoAtualizacao && acompanhamentoAtualizacao && atualizacao.exit_code != null) {
      finalizarAtualizacao(atualizacao);
    }
  }

  async function aplicarAtualizacao() {
    if (aplicandoAtualizacao || acompanhamentoAtualizacao) return;
    aplicandoAtualizacao = true;
    mostrarProgressoAtualizacao({});
    el("btn-checar-atualizacao").focus();
    const res = await api("/api/atualizacao/aplicar", "POST", {});
    aplicandoAtualizacao = false;
    if (!res.rodando) {
      finalizarAtualizacao(res);
      return;
    }
    acompanharAtualizacao(res);
    agendarProximoPoll(INTERVALO_ATIVO_MS);
  }

  // --- Loop Principal de Estado ---
  async function carregarEstado() {
    try {
      const versaoCortes = versaoEstadoCortes;
      const dados = await api("/api/estado");
      if (dados.ok === false) {
        if (!estadoAtual && el("faixa-aviso-sistema").hidden) {
          mostrarAvisoSistema("Não foi possível abrir o painel", dados.erro);
        }
        agendarProximoPoll(INTERVALO_OCIOSO_MS);
        return;
      }
      if (versaoCortes !== versaoEstadoCortes && estadoAtual?.cortes) dados.cortes = estadoAtual.cortes;
      estadoAtual = dados;
      acompanharAtualizacao(dados.atualizacao);

      if (dados.versao) {
        el("badge-versao").textContent = versaoComPrefixo(dados.versao);
        el("badge-versao").hidden = false;
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
        Boolean(dados.sessao && dados.sessao.rodando) || acompanhamentoAtualizacao || aplicandoAtualizacao;
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
    el("aba-btn-config").addEventListener("click", () => alternarAba("config"));
    el("modo-gpt").addEventListener("change", () => { ++versaoFormGPT; camposGPT(); });
    el("chave-gpt").addEventListener("input", () => { ++versaoFormGPT; });
    el("form-gpt").addEventListener("submit", conectarGPT);
    document.querySelectorAll(".btn-enviar-erro").forEach((b) => b.addEventListener("click", enviarErroParaGabriel));

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
        erroEl.textContent = "Cole o link da live ou do vídeo, ou escolha um arquivo do PC.";
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
        } else if (res && res.ok) {
          el("campo-link").value = "";
        }
      } finally {
        el("btn-cortar").disabled = false;
      }
      await carregarEstado();
    });

    el("lista-lives").addEventListener("click", async (ev) => {
      const bt = ev.target.closest(".btn-parar-live");
      if (!bt || bt.disabled) return;
      bt.disabled = true;
      bt.textContent = "Parando…";
      const res = await api("/api/parar-live", "POST", { id: bt.dataset.liveId });
      if (res.ok === false) mostrarAvisoSistema("Não foi possível parar a live", res.erro);
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

    el("btn-checar-atualizacao").addEventListener("click", () => checarAtualizacao(true));
    el("btn-aplicar-atualizacao").addEventListener("click", aplicarAtualizacao);
    el("btn-fechar-atualizacao").addEventListener("click", () => {
      exibirFaixaAtualizacao(false);
      el("btn-checar-atualizacao").focus();
    });

    el("sel-duracao-minima").addEventListener("change", salvarConfigCorte);

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
    iniciarFiltroNotas();
    registrarEventos();
    try {
      iniciarTriagem();
    } catch (_) {}

    const params = new URLSearchParams(window.location.search);
    if (params.get("atualizado")) {
      mostrarAvisoSistema(
        `CorteLive atualizado para ${versaoComPrefixo(params.get("atualizado"))}`,
        "Seus modelos de voz, vídeos cortados e configurações foram mantidos intactos."
      );
      params.delete("atualizado");
      const url = new URL(window.location.href);
      url.search = params.toString();
      window.history.replaceState({}, document.title, url);
    }

    await carregarEstado();
    carregarConfigCorte();
    checarAtualizacao();
    setInterval(checarAtualizacao, INTERVALO_ATUALIZACAO_MS);
  });
})();
