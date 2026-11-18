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
  let filtroPostagem = "todos";
  let versaoEstadoCortes = 0;
  const acoesDestinoAtivas = new Set();
  let carregouConfigDrive = false;
  let postarStatus = { disponivel: false, autopost: false };
  let timerPoll = null;
  let abaAtiva = "cortar";
  let acompanhamentoAtualizacao = false;
  let aplicandoAtualizacao = false;
  let checagemAtualizacao = null;
  let canaisAtivosPostiz = [];
  let canaisSelecionados = null;
  let editandoLimites = false;
  let salvandoPreferencias = false;
  let ultimoTotalCortesSessao = 0;
  let ultimoCorteDetectadoMs = 0;
  let ultimaAssinaturaLives = "";
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
    abaAtiva = novaAba === "contas" || novaAba === "config" ? novaAba : "cortar";
    for (const nome of ["cortar", "contas", "config"]) {
      const btn = el(`aba-btn-${nome}`);
      btn.classList.toggle("ativa", abaAtiva === nome);
      if (abaAtiva === nome) btn.setAttribute("aria-current", "page");
      else btn.removeAttribute("aria-current");
      el(`aba-painel-${nome}`).hidden = abaAtiva !== nome;
    }
    if (abaAtiva === "contas") {
      recarregarTudoPostagem();
      if (!carregouConfigDrive) carregarConfigDrive();
    }
  }

  async function apagarHistorico() {
    if (!window.confirm("Apagar o histórico? Os cortes não são apagados.")) return;
    const btn = el("btn-apagar-historico");
    const aviso = el("feedback-historico");
    const rotulo = btn.querySelector("span");
    btn.disabled = true;
    rotulo.textContent = "Apagando…";
    feedbackAcao(aviso, "");
    const res = await api("/api/historico/apagar", "POST", {});
    btn.disabled = false;
    rotulo.textContent = "Apagar histórico";
    feedbackAcao(aviso, res.ok === false
      ? (res.erro || "Não consegui apagar o histórico. Tente de novo.")
      : (res.mensagem || "Histórico apagado. Seus cortes continuam onde estavam."), res.ok === false);
  }

  // --- Enviar erro para o Gabriel: gera um .zip na Área de Trabalho (nada vai pela internet) ---
  let gerandoRelatorioErro = false;
  async function enviarErroParaGabriel() {
    if (gerandoRelatorioErro) return;
    gerandoRelatorioErro = true;
    const botoes = Array.from(document.querySelectorAll(".btn-enviar-erro"));
    const rotulos = botoes.map((b) => b.querySelector(".rotulo-enviar-erro"));
    botoes.forEach((b, i) => { b.disabled = true; if (rotulos[i]) rotulos[i].textContent = "Juntando os registros…"; });
    const feedback = el("feedback-enviar-erro");
    feedbackAcao(feedback, "");
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
    feedbackAcao(feedback, texto, falhou);
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
    el("texto-btn-cortar").textContent = rodando ? "ADICIONAR LIVE" : "CORTAR";
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
      resumo.textContent = sessao.ultima_mensagem || "Cole o link da live abaixo e clique em Cortar.";
      metricas.hidden = true;
    } else if (emTransmissao) {
      pilula.classList.add("tally-ao-vivo");
      txtTally.textContent = "AO VIVO";
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

    const postagem = document.createElement("div");
    postagem.className = "corte-postagem";
    if (c.postado === true) card.classList.add("corte-postado");
    const status = document.createElement("p");
    status.className = "corte-postagem-status";
    const redesMarcadas = Object.entries(redesPostagem).filter(([rede]) => c.postagem?.redes?.[rede]?.postado);
    if (c.postado === true || redesMarcadas.length) {
      const redes = redesMarcadas.map(([, nome]) => nome).join(", ");
      status.textContent = redes ? `Postado em ${redes}` : "Postado";
      postagem.append(status);
    }

    const btnMarcar = document.createElement("button");
    btnMarcar.type = "button";
    btnMarcar.className = "btn btn-secundario";
    btnMarcar.textContent = c.postagem?.origem ? "Desfazer marca geral" : "Marcar como postado";
    btnMarcar.dataset.acaoDestino = "postagem";
    btnMarcar.addEventListener("click", () => marcarPostado(c, btnMarcar, feedback, !c.postagem?.origem));
    postagem.append(btnMarcar);
    if (c.postagem?.redes) {
      const detalhes = document.createElement("details");
      const resumo = document.createElement("summary");
      resumo.textContent = "Marcar por rede";
      const acoesRedes = document.createElement("div");
      acoesRedes.className = "corte-postagem-redes";
      for (const [rede, nome] of Object.entries(redesPostagem)) {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "btn btn-secundario btn-compacto";
        btn.dataset.acaoDestino = rede;
        btn.textContent = nome;
        btn.setAttribute("aria-pressed", String(Boolean(c.postagem.redes[rede]?.postado)));
        btn.addEventListener("click", () => marcarPostado(c, btn, feedback, !c.postagem.redes[rede]?.postado, rede));
        acoesRedes.append(btn);
      }
      detalhes.append(resumo, acoesRedes);
      postagem.append(detalhes);
    }
    if (c.destino?.estado === "copiado" || c.destino?.estado === "erro") {
      const estadoCopia = document.createElement("p");
      const falhou = c.destino.estado === "erro";
      estadoCopia.className = falhou ? "ajuda-campo" : "corte-postagem-status";
      feedbackAcao(estadoCopia, falhou ? c.destino.erro || "Não foi possível copiar para o Drive." : "Copiado para a pasta do Drive", falhou);
      postagem.append(estadoCopia);
    }
    postagem.append(feedback);

    acoes.append(btnVideo, btnPasta, btnPostar, btnDrive);

    card.append(topo, acoes, postagem);
    if (acoesDestinoAtivas.has(c.caminho)) {
      card.querySelectorAll("[data-acao-destino]").forEach((controle) => { controle.disabled = true; });
    }
    return card;
  }

  const avisoDriveIndisponivel = "O envio para o Google Drive ainda não está disponível nesta versão. Você pode abrir a pasta do corte e copiá-lo manualmente.";
  const avisoRegistroIndisponivel = "O registro de cortes postados ainda não está disponível nesta versão.";

  function erroDestino(res, indisponivel) {
    // Uma rota disponível também usa 404 para informar que o corte sumiu.
    const corteAusente = res.statusHttp === 404 && /^(Corte|Vídeo) não encontrado/i.test(res.erro || "");
    if (res.statusHttp === 503) return "Esta função está indisponível agora. Feche e abra o CorteLive e tente de novo.";
    return !corteAusente && rotaEmBreve(res) ? indisponivel : res.erro || "Não foi possível concluir. Tente de novo.";
  }

  function marcarPostado(corte, botao, feedback, postado, rede) {
    return alterarDestinoCorte(corte, botao, feedback, "/api/cortes/postado", { postado, rede }, avisoRegistroIndisponivel);
  }

  async function alterarDestinoCorte(corte, botao, feedback, rota, corpo, indisponivel) {
    if (acoesDestinoAtivas.has(corte.caminho)) return;
    acoesDestinoAtivas.add(corte.caminho);
    const card = botao.closest(".corte-card");
    const botoes = card.querySelectorAll("[data-acao-destino]");
    const enviando = rota === "/api/drive/enviar";
    const rotulo = botao.textContent;
    const devolverFoco = document.activeElement === botao;
    botoes.forEach((controle) => { controle.disabled = true; });
    botao.textContent = enviando ? "Copiando…" : "Salvando…";
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
      feedbackAcao(cardVisivel?.querySelector("[data-feedback-destino]") || el("feedback-postagem-corte"), erroDestino(res, indisponivel), true);
      if (restaurarFoco) botao.focus();
      return;
    }

    const alteracoes = enviando ? { destino: res.destino } : { postado: res.postado, postagem: res.postagem, destino: res.destino };
    versaoEstadoCortes += 1;
    if (estadoAtual?.cortes) {
      estadoAtual.cortes = estadoAtual.cortes.map((item) => item.caminho === corte.caminho ? { ...item, ...alteracoes } : item);
      atualizarListaCortes(estadoAtual.cortes);
    }
    const atualizado = encontrarCard();
    const mensagem = res.postagem?.aviso ?
      "A marca foi salva, mas a cópia não pôde ser movida. Confira a pasta e use Enviar ao Drive de novo." : res.mensagem;
    feedbackAcao(atualizado?.querySelector("[data-feedback-destino]") || el("feedback-postagem-corte"), mensagem || "Alteração salva.", Boolean(res.postagem?.aviso));
    if (restaurarFoco) {
      const controle = atualizado?.querySelector(`[data-acao-destino="${botao.dataset.acaoDestino}"]`) ||
        el("filtro-postagem").querySelector('[aria-pressed="true"]');
      controle?.focus();
    }
  }

  function selecionarFiltroPostagem(evento) {
    const botao = evento.target.closest("button[data-filtro]");
    if (!botao) return;
    feedbackAcao(el("feedback-postagem-corte"), "");
    filtroPostagem = botao.dataset.filtro;
    for (const opcao of el("filtro-postagem").querySelectorAll("button")) {
      opcao.setAttribute("aria-pressed", String(opcao === botao));
    }
    atualizarListaCortes(estadoAtual?.cortes);
  }

  function atualizarListaCortes(cortes) {
    const lista = Array.isArray(cortes) ? cortes : [];
    atualizarUltimaCopiaDrive(lista);
    const total = lista.length;
    el("contador-cortes-total").textContent = total === 1 ? "1 corte" : `${total} cortes`;

    const vazio = el("estado-sem-cortes");
    const grade = el("lista-cortes");
    const avisoFiltro = el("estado-filtro-cortes");
    const filtrar = filtroPostagem !== "todos";
    const registroDisponivel = lista.some((corte) => typeof corte.postado === "boolean");
    const listaFiltrada = filtrar ? lista.filter((corte) => corte.postado === (filtroPostagem === "postados")) : lista;
    const novaAssinatura = JSON.stringify([filtroPostagem, total, registroDisponivel, listaFiltrada]);
    if (novaAssinatura === assinaturaCortes) return;
    assinaturaCortes = novaAssinatura;

    vazio.hidden = total > 0;
    grade.hidden = listaFiltrada.length === 0;
    const mensagemFiltro = !filtrar || total === 0 || listaFiltrada.length ? "" :
      !registroDisponivel ? avisoRegistroIndisponivel : filtroPostagem === "postados" ?
        "Nenhum corte foi marcado como postado." : "Todos os cortes já foram marcados como postados.";
    feedbackAcao(avisoFiltro, mensagemFiltro);
    if (!listaFiltrada.length) {
      grade.replaceChildren();
      return;
    }

    const cards = new Map(Array.from(grade.children, (card) => [card.dataset.caminho, card]));
    const mostrados = listaFiltrada.map((c) => {
      const anterior = cards.get(c.caminho);
      const assinatura = JSON.stringify(c);
      if (anterior && (anterior.dataset.assinatura === assinatura || acoesDestinoAtivas.has(c.caminho))) return anterior;
      const card = criarCardCorte(c);
      const detalhes = card.querySelector(".corte-postagem details");
      if (detalhes) detalhes.open = Boolean(anterior?.querySelector(".corte-postagem details")?.open);
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

  // --- Pasta sincronizada ---
  function aplicarConfigDrive(res) {
    el("chk-drive").checked = res.destino_ativo === true;
    el("pasta-drive").value = res.destino_pasta || "";
    el("chk-drive-txt").checked = res.destino_txt === true;
    el("chk-drive-mover").checked = res.destino_mover_postados !== false;
  }

  async function carregarConfigDrive() {
    carregouConfigDrive = true;
    const res = await api("/api/drive/config");
    el("campos-drive").disabled = false;
    const feedback = el("feedback-drive");
    if (res.ok !== true) {
      feedbackAcao(feedback, erroDestino(res, "As preferências do Drive ainda não estão disponíveis nesta versão."), true);
      return;
    }
    aplicarConfigDrive(res);
    feedbackAcao(feedback, "");
  }

  async function detectarDrive() {
    const botao = el("btn-detectar-drive");
    const sugestoes = el("sugestoes-drive");
    const feedback = el("feedback-drive");
    const campo = el("pasta-drive");
    const devolverFoco = document.activeElement === botao;
    botao.disabled = true;
    sugestoes.hidden = true;
    sugestoes.replaceChildren();
    feedbackAcao(feedback, "Procurando pastas sincronizadas…");
    const res = await api("/api/drive/detectar");
    botao.disabled = false;
    if (devolverFoco && document.activeElement === document.body) botao.focus();
    if (res.ok !== true) {
      feedbackAcao(feedback, erroDestino(res, "A busca pela pasta do Drive ainda não está disponível nesta versão."), true);
      return;
    }
    for (const opcao of res.opcoes || []) {
      const escolher = document.createElement("button");
      escolher.type = "button";
      escolher.className = "btn btn-secundario";
      escolher.textContent = `${opcao.nome} · ${opcao.sugestao}`;
      escolher.addEventListener("click", () => {
        campo.value = opcao.sugestao;
        campo.removeAttribute("aria-invalid");
        feedbackAcao(feedback, "Pasta escolhida. Salve as preferências para começar a enviar cortes.");
        campo.focus();
        sugestoes.hidden = true;
      });
      sugestoes.append(escolher);
    }
    sugestoes.hidden = !sugestoes.childElementCount;
    feedbackAcao(feedback, res.mensagem || "Você pode informar o caminho manualmente.");
  }

  async function salvarDrive(evento) {
    evento.preventDefault();
    const campos = el("campos-drive");
    if (campos.disabled) return;
    const campo = el("pasta-drive");
    const botao = el("btn-salvar-drive");
    const feedback = el("feedback-drive");
    const devolverFoco = document.activeElement === botao;
    campos.disabled = true;
    feedbackAcao(feedback, "Salvando preferências…");
    campo.removeAttribute("aria-invalid");
    const res = await api("/api/drive/config", "POST", {
      destino_pasta: campo.value.trim(),
      destino_ativo: el("chk-drive").checked,
      destino_txt: el("chk-drive-txt").checked,
      destino_mover_postados: el("chk-drive-mover").checked
    });
    campos.disabled = false;
    if (devolverFoco && document.activeElement === document.body) botao.focus();
    if (res.ok !== true) {
      if (res.statusHttp === 400) campo.setAttribute("aria-invalid", "true");
      feedbackAcao(feedback, erroDestino(res, "Ainda não dá para salvar as preferências do Drive nesta versão."), true);
      return;
    }
    carregouConfigDrive = true;
    aplicarConfigDrive(res);
    el("sugestoes-drive").hidden = true;
    feedbackAcao(feedback, "Preferências do Drive salvas.");
  }

  function atualizarUltimaCopiaDrive(cortes) {
    const aviso = el("ultima-copia-drive");
    const ultima = cortes.reduce((maisRecente, { destino }) => {
      if (destino?.estado !== "copiado" || typeof destino.quando !== "string") return maisRecente;
      return destino.quando > maisRecente ? destino.quando : maisRecente;
    }, "");
    if (!ultima || !Number.isFinite(Date.parse(ultima))) {
      aviso.hidden = true;
      return;
    }
    if (aviso.dataset.quando !== ultima) {
      aviso.replaceChildren("Última cópia em ", horarioFila(ultima), ". Confira a sincronização no Google Drive para computador.");
      aviso.dataset.quando = ultima;
    }
    aviso.hidden = false;
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

  function horarioFila(data) {
    const instante = new Date(data);
    const dia = instante.toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit" });
    const hora = instante.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
    const horario = document.createElement("time");
    horario.dateTime = instante.toISOString();
    horario.textContent = `${dia} às ${hora}`;
    return horario;
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
    const redes = Object.entries(it.redes || {});
    const destinos = redes.flatMap(([rede, dados]) =>
      Object.entries(dados.canais || {}).map(([id, canal]) => ({ rede, id, ...canal }))
    );
    const agendamentos = destinos.filter((canal) =>
      canal.estado === "agendado" && Number.isFinite(Date.parse(canal.agendado_para))
    );
    const futuros = agendamentos.filter((canal) => Date.parse(canal.agendado_para) > Date.now());
    const temAgenda = estado === "agendado" || estado === "cancelando" || agendamentos.length > 0;
    const editaveis = new Set(futuros.map((canal) => canal.rede));
    const podeEditarAgendado = futuros.length > 0 && estado !== "cancelando";
    const dataAgendada = Number.isFinite(Date.parse(it.agendado_para)) ? it.agendado_para : null;

    const topo = document.createElement("div");
    topo.className = "fila-topo";
    const tituloTopo = document.createElement("strong");
    tituloTopo.textContent = yt.titulo || it.canal || "Corte";
    tituloTopo.tabIndex = -1;
    const badge = document.createElement("span");
    badge.className = "selo-etapa";
    const rotulos = {
      aguardando_aprovacao: "Aguardando aprovação",
      na_fila: "Na fila",
      postando: "Enviando",
      agendado: "Agendado no Postiz",
      postado: "Publicado",
      parcial: "Envio parcial",
      erro: "Erro",
      cancelando: "Cancelamento em andamento",
      cancelado: "Agendamento cancelado",
      confirmacao_pendente: "Aguardando confirmação do Postiz",
      expirado: "Prazo expirado"
    };
    badge.textContent = rotulos[estado] || estado.replace(/_/g, " ");
    if (estado === "agendado" && dataAgendada) {
      const texto = document.createElement("span");
      texto.append("Agendado para ", horarioFila(dataAgendada), " no Postiz");
      badge.replaceChildren(texto);
    }
    topo.appendChild(tituloTopo);
    topo.appendChild(badge);
    card.appendChild(topo);

    if (agendamentos.length && (estado !== "agendado" || agendamentos.length > 1 || !dataAgendada)) {
      const horarios = document.createElement("ul");
      horarios.className = "fila-horarios";
      horarios.setAttribute("aria-label", "Horários locais por destino");
      agendamentos.forEach((canal) => {
        const linha = document.createElement("li");
        const conta = canaisAtivosPostiz.find(({ id }) => id === canal.id);
        const rede = redesPostagem[canal.rede] || canal.rede;
        const destino = conta && conta.nome && conta.nome !== rede ? `${rede} · ${conta.nome}` : rede;
        linha.append(`${destino} · `, horarioFila(canal.agendado_para));
        horarios.appendChild(linha);
      });
      card.appendChild(horarios);
    }

    const erros = new Set([it.erro, ...redes.map(([, dados]) => dados.erro), ...destinos.map((canal) => canal.erro)].filter(Boolean));
    if (estado === "erro" && erros.size === 0) erros.add("Não foi possível enviar o corte.");
    erros.forEach((mensagem) => {
      const erro = document.createElement("p");
      erro.className = "erro-campo";
      erro.textContent = mensagem;
      card.appendChild(erro);
    });

    const campos = document.createElement("div");
    campos.className = "fila-campos";
    campos.hidden = temAgenda;

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
      inp.readOnly = temAgenda || !editavel;
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
    if (!temAgenda || editaveis.has("youtube")) {
      mostrarRede("youtube");
      criarCampo("youtube", "Título", "titulo", yt.titulo, false).maxLength = 100;
      criarCampo("youtube", "Legenda", "descricao", yt.descricao, true);
    }
    if (!temAgenda || editaveis.has("instagram")) {
      mostrarRede("instagram");
      criarCampo("instagram", "Título e legenda (primeira linha vira o título)", "instagram", ig.legenda, true);
    }
    if (!temAgenda || editaveis.has("tiktok")) {
      mostrarRede("tiktok");
      criarCampo("tiktok", "Título e legenda", "tiktok", tk.legenda, true);
    }
    const entradas = [...campos.querySelectorAll("input, textarea")];

    const acoes = document.createElement("div");
    acoes.className = "fila-acoes";
    const linksPublicados = new Set();
    [...destinos, ...redes.map(([rede, dados]) => ({ rede, ...dados }))].forEach(({ rede, estado, link }) => {
      if (!["postado", "publicado", "duplicado"].includes(estado) || !/^https?:\/\//i.test(link) || linksPublicados.has(link)) return;
      linksPublicados.add(link);
      const abrir = document.createElement("a");
      abrir.className = "btn btn-secundario btn-compacto";
      abrir.href = link;
      abrir.target = "_blank";
      abrir.rel = "noopener noreferrer";
      abrir.textContent = `Ver publicação · ${redesPostagem[rede] || rede}`;
      acoes.appendChild(abrir);
    });
    const feedback = document.createElement("p");
    feedback.className = "erro-campo";
    feedback.setAttribute("role", "status");
    const salvar = (rota) => {
      const editados = entradas
        .filter((campo) => campo.value !== campo.defaultValue)
        .map((campo) => [campo.name, campo.value]);
      if (editados.length === 0) return { ok: true };
      return api(rota, "POST", { id: it.id, ...Object.fromEntries(editados) });
    };
    const acaoFila = (rotulo, ocupado, executar, { primario = false, confirmar, atualizar = carregarFilaAutopost } = {}) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = `btn ${primario ? "btn-primario" : "btn-secundario"} btn-compacto`;
      btn.textContent = rotulo;
      btn.addEventListener("click", async () => {
        if (confirmar && !window.confirm(confirmar)) return;
        const controles = [...acoes.querySelectorAll("button"), ...entradas];
        controles.forEach((controle) => { controle.disabled = true; });
        btn.textContent = ocupado;
        feedback.textContent = "";
        try {
          const res = await executar();
          if (res.ok === false) {
            feedback.textContent = res.erro || "Não foi possível concluir esta ação.";
            return;
          }
          await atualizar(res);
        } finally {
          controles.forEach((controle) => { controle.disabled = false; });
          btn.textContent = rotulo;
          if (btn.isConnected && !btn.hidden && document.activeElement === document.body) btn.focus();
        }
      });
      acoes.appendChild(btn);
      return btn;
    };

    const salvarRevisao = () => salvar("/api/autopost/editar");
    if (editavel && !temAgenda) acaoFila("Salvar edição", "Salvando…", salvarRevisao);
    if (estado === "aguardando_aprovacao") {
      acaoFila("Aprovar e agendar", "Aprovando…", async () => {
        const res = await salvarRevisao();
        if (res.ok === false) return res;
        return api("/api/autopost/aprovar", "POST", { id: it.id });
      }, { primario: true });
      acaoFila("Rejeitar", "Rejeitando…", () => api("/api/autopost/rejeitar", "POST", { id: it.id }));
    }

    const atualizarAgendamento = ({ item }) => {
      if (!item) return carregarFilaAutopost();
      const atualizado = criarCardFilaAutopost(item);
      card.replaceWith(atualizado);
      atualizado.querySelector(".fila-topo strong").focus();
    };
    if (podeEditarAgendado) {
      const editar = document.createElement("button");
      editar.type = "button";
      editar.className = "btn btn-secundario btn-compacto";
      editar.textContent = "Editar texto";
      acoes.appendChild(editar);
      const salvarTexto = acaoFila("Salvar texto", "Salvando…",
        () => salvar("/api/autopost/editar-agendado"), {
          primario: true,
          atualizar: (res) => res.item ? atualizarAgendamento(res) : fecharEdicao()
        });
      const descartar = document.createElement("button");
      descartar.type = "button";
      descartar.className = "btn btn-secundario btn-compacto";
      descartar.textContent = "Descartar edição";
      acoes.appendChild(descartar);
      const fecharEdicao = () => {
        entradas.forEach((entrada) => { entrada.value = entrada.defaultValue; });
        campos.hidden = salvarTexto.hidden = descartar.hidden = true;
        editar.hidden = false;
        feedback.textContent = "";
        editar.focus();
      };
      salvarTexto.hidden = descartar.hidden = true;
      editar.addEventListener("click", () => {
        campos.hidden = salvarTexto.hidden = descartar.hidden = false;
        editar.hidden = true;
        entradas.forEach((entrada) => { entrada.readOnly = false; });
        entradas[0].focus();
      });
      descartar.addEventListener("click", fecharEdicao);
    }
    if (futuros.length || estado === "cancelando") {
      acaoFila("Cancelar agendamento", "Cancelando…", () => api("/api/autopost/cancelar", "POST", { id: it.id }), {
        confirmar: `Cancelar no Postiz os agendamentos futuros de “${tituloTopo.textContent}”?`,
        atualizar: atualizarAgendamento
      });
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

    const rodape = document.createElement("div");
    rodape.append(acoes, feedback);
    card.append(campos, rodape);
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
    el("aba-btn-contas").addEventListener("click", () => alternarAba("contas"));
    el("aba-btn-config").addEventListener("click", () => alternarAba("config"));
    el("btn-apagar-historico").addEventListener("click", apagarHistorico);
    document.querySelectorAll(".btn-enviar-erro").forEach((b) => b.addEventListener("click", enviarErroParaGabriel));
    el("form-postiz").addEventListener("submit", conectarPostiz);
    el("filtro-postagem").addEventListener("click", selecionarFiltroPostagem);
    el("btn-detectar-drive").addEventListener("click", detectarDrive);
    el("form-drive").addEventListener("submit", salvarDrive);

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
        `CorteLive atualizado para ${versaoComPrefixo(params.get("atualizado"))}`,
        "Seus modelos de voz, vídeos cortados e configurações foram mantidos intactos."
      );
      params.delete("atualizado");
      const url = new URL(window.location.href);
      url.search = params.toString();
      window.history.replaceState({}, document.title, url);
    }

    await carregarEstado();
    recarregarTudoPostagem();
    checarAtualizacao();
    setInterval(checarAtualizacao, INTERVALO_ATUALIZACAO_MS);
  });
})();
