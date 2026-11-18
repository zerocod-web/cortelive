/* ==========================================================================
   CorteLive Dev — Lógica da Aba Dev (painel\dev\dev.js)
   Carregado SOMENTE no CorteLive Dev.exe (define de compilação MODO_DEV).
   Zero alteração em index.html, app.js ou estilos.css.
   ========================================================================== */
(function () {
  "use strict";

  if (window.CORTELIVE_MODO !== "dev") {
    return;
  }

  const el = (id) => document.getElementById(id);

  let abaDevVisivel = false;
  let jaCarregouInicial = false;
  let timerLogsDev = null;

  // Cache local das 3 configurações editáveis
  let cacheConfigs = {
    cortelive: { caminho: "cortelive\\config.toml", conteudo: "", salvo: "" },
    postar: { caminho: "%LOCALAPPDATA%\\CorteLive\\postar.toml", conteudo: "", salvo: "", usando_exemplo: false },
    raiz: { caminho: "config.toml", conteudo: "", salvo: "" }
  };
  let cfgAlvoAtual = "cortelive";

  async function apiDev(rota, metodo = "GET", corpo = null) {
    const opcoes = {
      method: metodo,
      headers: { "Accept": "application/json" },
      cache: "no-store"
    };
    if (corpo !== null) {
      opcoes.headers["Content-Type"] = "application/json; charset=utf-8";
      opcoes.body = JSON.stringify(corpo);
    }
    try {
      const resp = await fetch(rota, opcoes);
      const txt = await resp.text();
      let dados = {};
      try {
        dados = txt ? JSON.parse(txt) : {};
      } catch (_) {
        dados = { ok: resp.ok, texto: txt };
      }
      if (!resp.ok) {
        dados.ok = false;
        if (!dados.erro) dados.erro = `Erro HTTP ${resp.status}`;
      }
      return dados;
    } catch (e) {
      return { ok: false, erro: `Falha de conexão local: ${e.message || e}` };
    }
  }

  async function copiarParaClipboard(texto, btn, rotuloPadrao) {
    let ok = false;
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(texto);
        ok = true;
      }
    } catch (_) {}
    if (!ok) {
      const t = document.createElement("textarea");
      t.value = texto;
      t.style.position = "fixed";
      t.style.opacity = "0";
      document.body.appendChild(t);
      t.select();
      try { ok = document.execCommand("copy"); } catch (_) {}
      document.body.removeChild(t);
    }
    if (btn) {
      const span = btn.querySelector("span") || btn;
      span.textContent = ok ? "Copiado!" : "Erro";
      setTimeout(() => { span.textContent = rotuloPadrao; }, 1800);
    }
  }

  function aplicarIdentidadeVisualDev() {
    document.documentElement.setAttribute("data-ambiente", "dev");
    document.body.classList.add("modo-dev");
    document.title = "CorteLive [DEV]";

    let linkIcon = document.querySelector('link[rel="icon"]');
    if (!linkIcon) {
      linkIcon = document.createElement("link");
      linkIcon.rel = "icon";
      document.head.appendChild(linkIcon);
    }
    linkIcon.type = "image/x-icon";
    linkIcon.href = "/dev/icone-dev.ico";

    const imgMarca = document.querySelector("img.marca-icone");
    if (imgMarca) {
      imgMarca.src = "/dev/icone-dev.png";
      imgMarca.alt = "CorteLive Dev";
    }

    const linhaTitulo = document.querySelector(".marca-linha-titulo");
    if (linhaTitulo && !document.getElementById("selo-cabecalho-dev")) {
      const selo = document.createElement("span");
      selo.id = "selo-cabecalho-dev";
      selo.className = "selo-cabecalho-dev";
      selo.title = "Executável de desenvolvedor (MODO_DEV) · Canal Beta habilitado";
      selo.textContent = "DEV";
      const h1 = linhaTitulo.querySelector(".marca-nome");
      if (h1 && h1.nextSibling) {
        linhaTitulo.insertBefore(selo, h1.nextSibling);
      } else {
        linhaTitulo.appendChild(selo);
      }
    }
  }

  function criarBotaoAbaDev() {
    const nav = el("abas-principais") || document.querySelector(".abas-navegacao");
    if (!nav || el("aba-btn-dev")) return;

    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "aba-btn aba-btn-dev";
    btn.id = "aba-btn-dev";
    btn.setAttribute("data-aba", "dev");
    btn.innerHTML = `
      <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/>
      </svg>
      <span>Dev</span>
      <span class="dev-ponto-aba" aria-hidden="true"></span>
    `;
    nav.appendChild(btn);

    btn.addEventListener("click", () => ativarAbaDev());

    // Quando o usuário clicar nas abas originais (Cortar / Postar), oculta a aba Dev
    ["aba-btn-cortar", "aba-btn-contas"].forEach((idBtn) => {
      const outro = el(idBtn);
      if (outro) {
        outro.addEventListener("click", () => desativarAbaDev());
      }
    });
  }

  function ativarAbaDev() {
    const painelDev = el("aba-painel-dev");
    const btnDev = el("aba-btn-dev");
    if (!painelDev || !btnDev) return;

    abaDevVisivel = true;
    const pCortar = el("aba-painel-cortar");
    const pContas = el("aba-painel-contas");
    if (pCortar) pCortar.hidden = true;
    if (pContas) pContas.hidden = true;
    painelDev.hidden = false;

    document.querySelectorAll(".abas-navegacao .aba-btn").forEach((b) => {
      const ehDev = b.id === "aba-btn-dev";
      b.classList.toggle("ativa", ehDev);
      if (ehDev) b.setAttribute("aria-current", "page");
      else b.removeAttribute("aria-current");
    });

    if (!jaCarregouInicial) {
      jaCarregouInicial = true;
      carregarTudoDev();
    } else {
      atualizarLogsAoVivo();
    }
    iniciarPollingLogs();
  }

  function desativarAbaDev() {
    abaDevVisivel = false;
    const painelDev = el("aba-painel-dev");
    const btnDev = el("aba-btn-dev");
    if (painelDev) painelDev.hidden = true;
    if (btnDev) {
      btnDev.classList.remove("ativa");
      btnDev.removeAttribute("aria-current");
    }
  }

  function iniciarPollingLogs() {
    if (timerLogsDev) clearInterval(timerLogsDev);
    timerLogsDev = setInterval(() => {
      if (abaDevVisivel) {
        atualizarLogsAoVivo();
      }
    }, 1100);
  }

  // --- Carregamento de Resumo, Vídeos de Teste, Configs e Diagnóstico ---
  async function carregarResumoDev() {
    const res = await apiDev("/api/dev/resumo");
    if (!res || res.ok === false) return;

    const gitInfo = el("dev-git-info");
    if (gitInfo) {
      const partes = [];
      if (res.git_branch) partes.push(res.git_branch);
      if (res.git_ultimo_tag) partes.push(res.git_ultimo_tag);
      if (res.versao) partes.push(`v${res.versao}`);
      gitInfo.textContent = partes.join(" · ") || "dev";
    }

    const selTeste = el("dev-sel-video-teste");
    if (selTeste) {
      selTeste.innerHTML = "";
      const listaTv = Array.isArray(res.videos_teste) ? res.videos_teste : [];
      if (listaTv.length === 0) {
        const opt = document.createElement("option");
        opt.value = "";
        opt.textContent = "Nenhum vídeo encontrado em teste_video\\";
        selTeste.appendChild(opt);
      } else {
        // Prioriza gta_rp_trecho.mp4 se existir (mais leve para teste rápido)
        const ordenados = [...listaTv].sort((a, b) => a.tamanho_mb - b.tamanho_mb);
        ordenados.forEach((v) => {
          const opt = document.createElement("option");
          opt.value = v.caminho;
          opt.textContent = `${v.nome} (${v.tamanho_mb} MB)`;
          selTeste.appendChild(opt);
        });
      }
    }

    const selDry = el("dev-sel-video-dry");
    if (selDry) {
      const valorAntes = selDry.value;
      selDry.innerHTML = "";
      const listaDry = Array.isArray(res.videos_dry_run) ? res.videos_dry_run : [];
      if (listaDry.length === 0) {
        const opt = document.createElement("option");
        opt.value = "";
        opt.textContent = "Nenhum arquivo .mp4 disponível para dry-run";
        selDry.appendChild(opt);
      } else {
        listaDry.forEach((v) => {
          const opt = document.createElement("option");
          opt.value = v.caminho;
          opt.textContent = v.rotulo;
          selDry.appendChild(opt);
        });
        if (valorAntes && Array.from(selDry.options).some((o) => o.value === valorAntes)) {
          selDry.value = valorAntes;
        }
      }
    }
  }

  async function carregarConfigsDev() {
    const res = await apiDev("/api/dev/configs");
    if (!res || res.ok === false || !res.configs) return;

    ["cortelive", "postar", "raiz"].forEach((chave) => {
      const info = res.configs[chave];
      if (!info) return;
      cacheConfigs[chave] = {
        caminho: info.caminho || cacheConfigs[chave].caminho,
        conteudo: info.conteudo || "",
        salvo: info.conteudo || "",
        existe: Boolean(info.existe),
        usando_exemplo: Boolean(info.usando_exemplo)
      };
    });
    renderizarAbaConfig();
  }

  function atualizarIndicadorSujeiraConfig() {
    const item = cacheConfigs[cfgAlvoAtual];
    const badge = el("dev-cfg-estado-edicao");
    if (!item || !badge) return;
    const modificado = item.conteudo !== item.salvo;
    if (modificado) {
      badge.className = "dev-pilula-erro";
      badge.textContent = "Alterações não salvas";
    } else if (cfgAlvoAtual === "postar" && item.usando_exemplo) {
      badge.className = "dev-pilula-neutra";
      badge.textContent = "Modelo inicial (salvar cria postar.toml)";
    } else if (cfgAlvoAtual === "raiz" && !item.existe && !item.salvo) {
      badge.className = "dev-pilula-neutra";
      badge.textContent = "Vazio (usa padrões de cortelive\\config.toml)";
    } else {
      badge.className = "dev-pilula-ok";
      badge.textContent = "Sincronizado";
    }
  }

  function renderizarAbaConfig() {
    document.querySelectorAll(".dev-cfg-aba").forEach((btn) => {
      const ativo = btn.getAttribute("data-cfg-alvo") === cfgAlvoAtual;
      btn.classList.toggle("ativa", ativo);
      btn.setAttribute("aria-selected", ativo ? "true" : "false");
    });

    const item = cacheConfigs[cfgAlvoAtual];
    if (!item) return;
    el("dev-cfg-caminho").textContent = item.caminho;
    el("dev-textarea-cfg").value = item.conteudo;
    atualizarIndicadorSujeiraConfig();
  }

  async function salvarConfigAtual() {
    const btn = el("dev-btn-salvar-cfg");
    const msg = el("dev-cfg-feedback");
    const texto = el("dev-textarea-cfg").value;
    cacheConfigs[cfgAlvoAtual].conteudo = texto;

    btn.disabled = true;
    btn.querySelector("span").textContent = "Validando e salvando…";
    msg.className = "dev-cfg-msg";
    msg.textContent = "Validando sintaxe TOML…";

    try {
      const res = await apiDev("/api/dev/configs", "POST", {
        alvo: cfgAlvoAtual,
        conteudo: texto
      });
      if (res.ok === false) {
        msg.className = "dev-cfg-msg erro";
        msg.textContent = res.erro || "Falha ao salvar configuração.";
        return;
      }
      cacheConfigs[cfgAlvoAtual].salvo = texto;
      cacheConfigs[cfgAlvoAtual].existe = true;
      cacheConfigs[cfgAlvoAtual].usando_exemplo = false;
      if (res.caminho) cacheConfigs[cfgAlvoAtual].caminho = res.caminho;
      atualizarIndicadorSujeiraConfig();
      msg.className = "dev-cfg-msg sucesso";
      msg.textContent = res.mensagem || "Configuração salva com sucesso.";
    } finally {
      btn.disabled = false;
      btn.querySelector("span").textContent = "Salvar configuração";
    }
  }

  // --- Diagnóstico de Contas e Servidor de Tokens ---
  async function checarContasETokens() {
    const btn = el("dev-btn-checar-contas");
    const online = Boolean(el("dev-chk-status-online") && el("dev-chk-status-online").checked);
    const pilula = el("dev-pilula-worker");

    btn.disabled = true;
    btn.querySelector("span").textContent = "Verificando…";
    pilula.className = "dev-pilula-neutra";
    pilula.textContent = "Consultando…";

    try {
      const res = await apiDev(`/api/dev/status-contas?online=${online ? "1" : "0"}`);
      if (!res || res.ok === false) {
        pilula.className = "dev-pilula-erro";
        pilula.textContent = "Erro";
        el("dev-st-worker").textContent = (res && res.erro) || "Falha ao consultar";
        return;
      }

      const srv = res.servidor_tokens || {};
      if (!srv.configurado) {
        pilula.className = "dev-pilula-neutra";
        pilula.textContent = "Placeholder";
        el("dev-st-worker").textContent = "Não configurado (placeholder)";
      } else if (srv.online) {
        pilula.className = "dev-pilula-ok";
        pilula.textContent = `Worker HTTP ${srv.http_status || 200}`;
        el("dev-st-worker").textContent = srv.detalhe || srv.url;
      } else {
        pilula.className = "dev-pilula-erro";
        pilula.textContent = "Worker offline";
        el("dev-st-worker").textContent = srv.detalhe || srv.url;
      }
      el("dev-st-worker").title = `${srv.url || ""} — ${srv.detalhe || ""}`;

      const dir = res.direto || {};
      ["youtube", "instagram", "tiktok"].forEach((rede) => {
        const node = el(`dev-st-${rede}`);
        if (!node) return;
        const r = dir[rede] || {};
        if (r.conectado && r.valido) {
          node.textContent = r.conta ? `OK · ${r.conta}` : "Conectada (válida)";
        } else if (r.conectado) {
          node.textContent = "Token expirado/inválido";
        } else {
          node.textContent = r.detalhe || "Não conectada";
        }
      });

      const pz = res.postiz || {};
      const stPz = el("dev-st-postiz");
      if (stPz) {
        const qtd = Array.isArray(pz.canais) ? pz.canais.length : 0;
        stPz.textContent = pz.conectado
          ? `Conectado (${qtd} cana${qtd === 1 ? "l" : "is"})`
          : "Não conectado";
      }
    } finally {
      btn.disabled = false;
      btn.querySelector("span").textContent = "Checar contas e tokens";
    }
  }

  // --- Polling de Logs ao Vivo (Corte + Postagem) ---
  async function atualizarLogsAoVivo() {
    const res = await apiDev("/api/dev/logs");
    if (!res || res.ok === false) return;

    // 1) Bus Corte
    const corte = res.corte || {};
    const cRodando = Boolean(corte.rodando);
    const cEstado = (corte.estado || "parado").toLowerCase();
    const badgeCorte = el("dev-badge-estado-corte");
    if (badgeCorte) {
      badgeCorte.className = "tally-pilula " + (
        !cRodando ? "tally-parado" : cEstado === "ao vivo" ? "tally-ao-vivo" : "tally-preparando"
      );
      badgeCorte.textContent = !cRodando ? "PARADO" : cEstado.toUpperCase();
    }

    const btnTestarCorte = el("dev-btn-testar-corte");
    const btnPararCorte = el("dev-btn-parar-corte");
    if (btnTestarCorte && btnPararCorte) {
      btnTestarCorte.hidden = cRodando;
      btnPararCorte.hidden = !cRodando;
      btnPararCorte.disabled = cEstado === "encerrando";
    }

    const subCorte = el("dev-sub-corte");
    if (subCorte) {
      const partes = [];
      if (corte.canal) partes.push(`Fonte: ${corte.canal}`);
      if (corte.detalhe_servicos) partes.push(corte.detalhe_servicos);
      if (corte.arquivo_log) partes.push(`Log: ${corte.arquivo_log}`);
      subCorte.textContent = partes.join(" · ") || (corte.ultima_mensagem || "Pronto para sessão de corte.");
    }

    const preCorte = el("dev-pre-log-corte");
    if (preCorte) {
      const txtCorte = (corte.log && corte.log.trim()) ? corte.log : "Nenhuma linha de corte nesta sessão ainda.";
      if (preCorte.textContent !== txtCorte) {
        preCorte.textContent = txtCorte;
        if (el("dev-autoscroll-corte") && el("dev-autoscroll-corte").checked) {
          preCorte.scrollTop = preCorte.scrollHeight;
        }
      }
    }

    // 2) Bus Postagem
    const post = res.postagem || {};
    const pRodando = Boolean(post.rodando);
    const badgePost = el("dev-badge-estado-post");
    if (badgePost) {
      if (pRodando) {
        badgePost.className = "tally-pilula tally-preparando";
        badgePost.textContent = "RODANDO…";
      } else if (post.exit_code === 0) {
        badgePost.className = "tally-pilula dev-pilula-ok";
        badgePost.textContent = "CONCLUÍDO · EXIT 0";
      } else if (post.exit_code !== null && post.exit_code !== undefined) {
        badgePost.className = "tally-pilula tally-ao-vivo";
        badgePost.textContent = `ERRO · EXIT ${post.exit_code}`;
      } else {
        badgePost.className = "tally-pilula tally-parado";
        badgePost.textContent = "OCIOSO";
      }
    }

    const btnPostDry = el("dev-btn-postar-dry");
    if (btnPostDry) {
      btnPostDry.disabled = pRodando;
      btnPostDry.querySelector("span").textContent = pRodando ? "Executando dry-run…" : "Rodar postagem dry-run";
    }

    const subPost = el("dev-sub-post");
    if (subPost && post.comando) {
      subPost.textContent = `$ ${post.comando}`;
    }

    const prePost = el("dev-pre-log-postagem");
    if (prePost && post.log && post.log.trim()) {
      if (prePost.textContent !== post.log) {
        prePost.textContent = post.log;
        if (el("dev-autoscroll-post") && el("dev-autoscroll-post").checked) {
          prePost.scrollTop = prePost.scrollHeight;
        }
      }
    }
  }

  // --- Eventos dos Botões da Aba Dev ---
  function registrarEventosDev() {
    // Abrir pastas (cortes, logs, config, dados_local)
    document.querySelectorAll(".dev-btn-pasta").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const pasta = btn.getAttribute("data-pasta");
        await apiDev("/api/dev/abrir-pasta", "POST", { pasta });
      });
    });

    // Canal de atualização (beta / estavel)
    const obterCanalEscolhido = () => {
      const r = document.querySelector('input[name="dev-canal-update"]:checked');
      return (r && r.value) || "beta";
    };

    document.querySelectorAll('input[name="dev-canal-update"]').forEach((r) => {
      r.addEventListener("change", () => {
        const c = obterCanalEscolhido();
        el("dev-btn-aplicar-update").hidden = true;
        el("dev-status-update-linha").textContent =
          c === "beta"
            ? "Canal Beta selecionado (inclui pre-releases no GitHub)."
            : "Canal Estável selecionado (apenas releases finais, igual ao CorteLive.exe dos amigos).";
      });
    });

    el("dev-btn-checar-update").addEventListener("click", async () => {
      const canal = obterCanalEscolhido();
      const btn = el("dev-btn-checar-update");
      const statusEl = el("dev-status-update-linha");
      const btnAplicar = el("dev-btn-aplicar-update");

      btn.disabled = true;
      btn.querySelector("span").textContent = "Consultando…";
      statusEl.textContent = `Consultando GitHub Releases no canal ${canal}…`;

      try {
        const res = await apiDev("/api/dev/atualizacao/checar", "POST", { canal });
        if (res && res.etapa === "disponivel" && res.versao_nova) {
          statusEl.textContent = `[${canal.toUpperCase()}] ${res.msg || `Nova versão v${res.versao_nova} disponível (atual: v${res.versao_atual}).`}`;
          btnAplicar.hidden = false;
        } else if (res && res.etapa === "em_dia") {
          statusEl.textContent = `[${canal.toUpperCase()}] ${res.msg || `Em dia na v${res.versao_atual}.`}`;
          btnAplicar.hidden = true;
        } else {
          statusEl.textContent = `[${canal.toUpperCase()}] ${(res && (res.erro || res.msg)) || "Não foi possível consultar."}`;
          btnAplicar.hidden = true;
        }
      } finally {
        btn.disabled = false;
        btn.querySelector("span").textContent = "Checar canal";
      }
    });

    el("dev-btn-aplicar-update").addEventListener("click", async () => {
      const canal = obterCanalEscolhido();
      const btn = el("dev-btn-aplicar-update");
      const statusEl = el("dev-status-update-linha");
      btn.disabled = true;
      btn.querySelector("span").textContent = "Aplicando…";
      statusEl.textContent = `Baixando e aplicando atualização pelo canal ${canal}…`;
      try {
        const res = await apiDev("/api/dev/atualizacao/aplicar", "POST", { canal });
        statusEl.textContent = (res && (res.msg || res.erro)) || "Operação finalizada.";
        if (res && res.etapa === "concluido") {
          setTimeout(() => window.location.reload(), 900);
        }
      } finally {
        btn.disabled = false;
        btn.querySelector("span").textContent = "Atualizar pelo canal";
      }
    });

    el("dev-btn-voltar-update").addEventListener("click", async () => {
      const btn = el("dev-btn-voltar-update");
      const statusEl = el("dev-status-update-linha");
      btn.disabled = true;
      statusEl.textContent = "Restaurando versão anterior de versoes_antigas\\…";
      try {
        const res = await apiDev("/api/dev/atualizacao/voltar", "POST", {});
        statusEl.textContent = (res && (res.msg || res.erro)) || "Rollback finalizado.";
      } finally {
        btn.disabled = false;
      }
    });

    // Lançar versão (lancar_versao.cmd)
    el("dev-btn-lancar-versao").addEventListener("click", async () => {
      const simular = Boolean(el("dev-chk-simular-release").checked);
      const statusEl = el("dev-status-update-linha");
      const res = await apiDev("/api/dev/lancar-versao", "POST", { simular });
      if (res && res.ok) {
        statusEl.textContent = simular
          ? "Assistente lancar_versao.cmd -Simular aberto em nova janela de terminal."
          : "Assistente lancar_versao.cmd aberto no terminal (confirme S/N lá para publicar).";
      } else {
        statusEl.textContent = (res && res.erro) || "Não foi possível abrir lancar_versao.cmd.";
      }
    });

    // Teste 1: Corte com arquivo de teste_video\
    el("dev-btn-testar-corte").addEventListener("click", async () => {
      const arquivo = el("dev-sel-video-teste").value;
      const modelo = el("dev-sel-modelo-teste").value;
      const minutos = el("dev-sel-minutos-teste").value;
      const fb = el("dev-feedback-corte");
      if (!arquivo) {
        fb.textContent = "Nenhum vídeo em teste_video\\.";
        return;
      }
      el("dev-btn-testar-corte").disabled = true;
      fb.textContent = "Iniciando motor…";
      try {
        const res = await apiDev("/api/dev/testar-corte", "POST", {
          arquivo,
          modelo,
          legenda: "opus",
          minutos
        });
        fb.textContent = res.ok ? "Sessão iniciada — veja o log abaixo." : (res.erro || "Erro ao iniciar.");
        await atualizarLogsAoVivo();
      } finally {
        el("dev-btn-testar-corte").disabled = false;
      }
    });

    el("dev-btn-parar-corte").addEventListener("click", async () => {
      el("dev-btn-parar-corte").disabled = true;
      el("dev-feedback-corte").textContent = "Solicitando parada limpa…";
      await apiDev("/api/parar", "POST", {});
      await atualizarLogsAoVivo();
    });

    // Teste 2: Postagem em Dry-Run
    el("dev-btn-postar-dry").addEventListener("click", async () => {
      const video = el("dev-sel-video-dry").value;
      const via = el("dev-sel-via-dry").value;
      const titulo = el("dev-inp-titulo-dry").value.trim();
      const redes = Array.from(document.querySelectorAll('input[name="dev-rede-dry"]:checked')).map((c) => c.value);
      const fb = el("dev-feedback-dry");

      if (!video) {
        fb.textContent = "Escolha um vídeo primeiro.";
        return;
      }
      if (redes.length === 0) {
        fb.textContent = "Marque ao menos uma rede.";
        return;
      }

      el("dev-btn-postar-dry").disabled = true;
      fb.textContent = "Disparando --dry-run…";
      const res = await apiDev("/api/dev/postar-dry-run", "POST", {
        video,
        via,
        titulo,
        descricao: "Teste dry-run pelo painel CorteLive Dev #shorts #cortes",
        redes: redes.join(",")
      });
      fb.textContent = res.ok ? "Executando — acompanhe no log de postagem." : (res.erro || "Erro ao iniciar.");
      await atualizarLogsAoVivo();
    });

    // Teste 3: Status das contas e servidor_tokens
    el("dev-btn-checar-contas").addEventListener("click", checarContasETokens);

    // Controles dos logs
    el("dev-btn-copiar-corte").addEventListener("click", (ev) => {
      copiarParaClipboard(el("dev-pre-log-corte").textContent, ev.currentTarget, "Copiar");
    });
    el("dev-btn-copiar-post").addEventListener("click", (ev) => {
      copiarParaClipboard(el("dev-pre-log-postagem").textContent, ev.currentTarget, "Copiar");
    });
    el("dev-btn-limpar-corte").addEventListener("click", async () => {
      await apiDev("/api/dev/limpar-log", "POST", { alvo: "corte" });
      await atualizarLogsAoVivo();
    });
    el("dev-btn-limpar-post").addEventListener("click", async () => {
      await apiDev("/api/dev/limpar-log", "POST", { alvo: "postagem" });
      el("dev-pre-log-postagem").textContent = "Log limpo.";
      await atualizarLogsAoVivo();
    });

    // Patchbay de Configuração (TOML)
    document.querySelectorAll(".dev-cfg-aba").forEach((aba) => {
      aba.addEventListener("click", () => {
        cacheConfigs[cfgAlvoAtual].conteudo = el("dev-textarea-cfg").value;
        cfgAlvoAtual = aba.getAttribute("data-cfg-alvo") || "cortelive";
        el("dev-cfg-feedback").className = "dev-cfg-msg";
        el("dev-cfg-feedback").textContent = "Validação sintática com tomllib antes de gravar (atalho: Ctrl+S).";
        renderizarAbaConfig();
      });
    });

    el("dev-textarea-cfg").addEventListener("input", () => {
      cacheConfigs[cfgAlvoAtual].conteudo = el("dev-textarea-cfg").value;
      atualizarIndicadorSujeiraConfig();
    });

    el("dev-textarea-cfg").addEventListener("keydown", (ev) => {
      if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === "s") {
        ev.preventDefault();
        salvarConfigAtual();
      }
    });

    el("dev-btn-recarregar-cfg").addEventListener("click", async () => {
      await carregarConfigsDev();
      const msg = el("dev-cfg-feedback");
      msg.className = "dev-cfg-msg";
      msg.textContent = "Arquivos recarregados do disco.";
    });

    el("dev-btn-salvar-cfg").addEventListener("click", salvarConfigAtual);
  }

  async function carregarTudoDev() {
    await Promise.all([
      carregarResumoDev(),
      carregarConfigsDev(),
      checarContasETokens(),
      atualizarLogsAoVivo()
    ]);
  }

  async function inicializarModoDev() {
    aplicarIdentidadeVisualDev();

    // Carrega o HTML da aba Dev
    try {
      const resp = await fetch("/dev/dev.html", { cache: "no-store" });
      if (!resp.ok) return;
      const html = await resp.text();

      const wrapper = document.createElement("div");
      wrapper.innerHTML = html.trim();
      const nodoDev = wrapper.firstElementChild;
      const telaMesa = el("tela-mesa") || el("conteudo-principal");
      if (!nodoDev || !telaMesa) return;

      telaMesa.appendChild(nodoDev);
      criarBotaoAbaDev();
      registrarEventosDev();

      const params = new URLSearchParams(window.location.search);
      if (params.get("aba") === "dev") {
        // Aguarda app.js liberar #tela-mesa na primeira checagem de /api/estado
        const aguardarMesa = setInterval(() => {
          const mesa = el("tela-mesa");
          if (mesa && !mesa.hidden) {
            clearInterval(aguardarMesa);
            ativarAbaDev();
          }
        }, 120);
        setTimeout(() => clearInterval(aguardarMesa), 8000);
      }
    } catch (_) {}
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", inicializarModoDev);
  } else {
    inicializarModoDev();
  }
})();
