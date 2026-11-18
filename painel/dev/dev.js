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
  let timerFeedbackPastas = null;
  let versaoAtualDev = "1.0.1";

  // Cache local dos 3 arquivos de configuração editáveis
  let cacheConfigs = {
    cortelive: { caminho: "cortelive\\config.toml", conteudo: "", salvo: "", existe: true },
    postar: { caminho: "%LOCALAPPDATA%\\CorteLive\\postar.toml", conteudo: "", salvo: "", existe: false, usando_exemplo: true },
    raiz: { caminho: "config.toml", conteudo: "", salvo: "", existe: false }
  };
  let cfgAlvoAtual = "cortelive";

  const TEXTO_AJUDA_CFG_PADRAO =
    "O texto é conferido automaticamente antes de salvar para evitar erros de digitação (atalho: Ctrl+S).";

  const NOMES_REDES = {
    youtube: "YouTube Shorts",
    instagram: "Instagram Reels",
    tiktok: "TikTok"
  };

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
        if (!dados.erro) dados.erro = `Não foi possível concluir o pedido (código ${resp.status}).`;
      }
      return dados;
    } catch (_) {
      return { ok: false, erro: "Sem resposta do painel local. Confira se o CorteLive Dev continua aberto." };
    }
  }

  function normalizarTextoPortugues(texto) {
    if (!texto) return "";
    let s = String(texto).trim();
    s = s.replace(/\bNao conectada\b/gi, "Não conectada");
    s = s.replace(/\bNao conectado\b/gi, "Não conectado");
    s = s.replace(/\s*\(use conectar [^)]*\)/gi, "");
    s = s.replace(/\bversao\b/gi, "versão");
    s = s.replace(/\bconfiguracao\b/gi, "configuração");
    s = s.replace(/\bconexao\b/gi, "conexão");
    return s;
  }

  function humanizarErroToml(erroBruto) {
    const s = String(erroBruto || "").trim();
    const mLinhaCol = s.match(/at line\s+(\d+),\s*column\s+(\d+)/i);
    if (mLinhaCol) {
      return `Erro de formato na linha ${mLinhaCol[1]}, coluna ${mLinhaCol[2]} — confira aspas, colchetes ou o sinal de igual (=).`;
    }
    if (/TOML/i.test(s)) {
      return normalizarTextoPortugues(
        s.replace(/^Erro de sintaxe TOML:\s*/i, "Erro no formato da configuração: ")
      );
    }
    return normalizarTextoPortugues(s) || "Não foi possível salvar a configuração.";
  }

  function humanizarMensagemAtualizacao(res, canal) {
    const rotuloCanal = canal === "beta" ? "Canal Beta" : "Canal Estável";
    const vAtual = (res && res.versao_atual) ? `v${String(res.versao_atual).replace(/^[vV]/, "")}` : `v${versaoAtualDev}`;
    const erro = String((res && res.erro) || "");
    const msg = String((res && res.msg) || "");

    if (res && res.etapa === "disponivel" && res.versao_nova) {
      const vNova = `v${String(res.versao_nova).replace(/^[vV]/, "")}`;
      return {
        tipo: "ok",
        texto: `${rotuloCanal}: nova versão ${vNova} encontrada (instalada: ${vAtual}).`
      };
    }
    if (res && res.etapa === "em_dia") {
      return {
        tipo: "ok",
        texto: `${rotuloCanal}: tudo em dia na versão ${vAtual}.`
      };
    }
    if (/tag_name/i.test(erro)) {
      return {
        tipo: "neutro",
        texto: `${rotuloCanal}: versão instalada ${vAtual} conferida recentemente (aguarde alguns minutos para nova consulta ao GitHub ou verifique o Canal Estável).`
      };
    }
    if (/Nenhuma release publicada/i.test(erro)) {
      return {
        tipo: "neutro",
        texto: `${rotuloCanal}: nenhuma versão de teste publicada além da ${vAtual}.`
      };
    }
    if (/versoes_antigas|backup|anterior/i.test(erro + " " + msg)) {
      return {
        tipo: "neutro",
        texto: "Ainda não há versão anterior guardada neste computador para restaurar."
      };
    }
    return {
      tipo: "erro",
      texto: `${rotuloCanal}: ${normalizarTextoPortugues(erro || msg || "Não foi possível verificar atualizações agora.")}`
    };
  }

  function formatarRotuloCorteDryRun(item) {
    const bruto = String((item && item.rotulo) || "").replace(/^Corte\s*·\s*/i, "").trim();
    const caminho = String((item && item.caminho) || "");
    const partes = caminho.split(/[\\/]/).filter(Boolean);
    const nomeArq = partes[partes.length - 1] || bruto;
    const pastaPai = partes.length >= 2 ? partes[partes.length - 2] : "";
    const mCanal = pastaPai.match(/^\d{4}-\d{2}-\d{2}(?:_\d{4}_|\s+\d{2}h\d{2}\s*-\s*)(.+)$/);
    const canal = mCanal ? mCanal[1] : pastaPai;
    const nomeLimpo = nomeArq
      .replace(/^\d{4}-\d{2}-\d{2}\s+\d{2}h\d{2}\s*-\s*[^-]+-\s*/, "")
      .replace(/\.mp4$/i, "");
    if (canal && nomeLimpo) {
      return `${canal} · ${nomeLimpo}`;
    }
    return bruto || nomeArq;
  }

  function formatarLogPostagem(logBruto) {
    if (!logBruto || !logBruto.trim()) return "";
    const linhas = logBruto.split(/\r?\n/).filter((l) => l.trim().length > 0);
    return linhas.map((linha) => {
      const t = linha.trim();
      if (t.startsWith("{") && t.endsWith("}")) {
        try {
          const ev = JSON.parse(t);
          const rede = NOMES_REDES[ev.rede] || ev.rede || "Rede";
          const pct = typeof ev.pct === "number" ? `${String(ev.pct).padStart(3, " ")}%` : "  — ";
          const etapa = String(ev.etapa || "").toLowerCase();
          if (etapa === "preparando") {
            return `[${rede}] ${pct} · Preparando vídeo para simulação…`;
          }
          if (etapa === "iniciando") {
            return `[${rede}] ${pct} · Conferindo dados do envio…`;
          }
          if (etapa === "enviando") {
            return `[${rede}] ${pct} · Simulando envio para a plataforma…`;
          }
          if (etapa === "concluido") {
            const viaTxt = ev.via === "postiz" ? " via Postiz" : "";
            const tituloTxt = ev.titulo ? ` · Título: "${ev.titulo}"` : "";
            return `[${rede}] ${pct} · Simulação concluída${viaTxt} (nenhum vídeo real publicado)${tituloTxt}`;
          }
          if (ev.erro) {
            return `[${rede}] Falha: ${normalizarTextoPortugues(ev.erro)}`;
          }
        } catch (_) {}
      }
      return normalizarTextoPortugues(t);
    }).join("\n");
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
      span.textContent = ok ? "Copiado!" : "Falhou";
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
      selo.title = "Modo de testes e desenvolvimento do CorteLive · Canal Beta liberado";
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

  function definirFeedbackCurto(nodo, texto, tipo) {
    if (!nodo) return;
    nodo.textContent = texto || "";
    nodo.removeAttribute("data-erro");
    nodo.removeAttribute("data-ok");
    if (tipo === "erro") nodo.setAttribute("data-erro", "true");
    if (tipo === "ok") nodo.setAttribute("data-ok", "true");
  }

  // --- Carregamento de Resumo, Vídeos de Teste, Cortes Dry-Run e Configs ---
  async function carregarResumoDev() {
    const res = await apiDev("/api/dev/resumo");
    if (!res || res.ok === false) return;

    if (res.versao) {
      versaoAtualDev = String(res.versao).replace(/^[vV]/, "");
    }

    const gitInfo = el("dev-git-info");
    if (gitInfo) {
      const partes = [];
      if (res.git_branch) partes.push(`ramo ${res.git_branch}`);
      if (res.versao) partes.push(`v${versaoAtualDev}`);
      gitInfo.textContent = partes.join(" · ") || `v${versaoAtualDev}`;
    }

    const selTeste = el("dev-sel-video-teste");
    const btnTestarCorte = el("dev-btn-testar-corte");
    if (selTeste) {
      selTeste.innerHTML = "";
      const listaTv = (Array.isArray(res.videos_teste) ? res.videos_teste : [])
        .filter((v) => v && v.caminho && /\.mp4$/i.test(v.caminho));
      if (listaTv.length === 0) {
        const opt = document.createElement("option");
        opt.value = "";
        opt.textContent = "Nenhum vídeo .mp4 encontrado na pasta teste_video";
        selTeste.appendChild(opt);
        if (btnTestarCorte) btnTestarCorte.disabled = true;
      } else {
        const ordenados = [...listaTv].sort((a, b) => a.tamanho_mb - b.tamanho_mb);
        ordenados.forEach((v) => {
          const opt = document.createElement("option");
          opt.value = v.caminho;
          const mbFmt = String(v.tamanho_mb).replace(".", ",");
          opt.textContent = `${v.nome} (${mbFmt} MB)`;
          selTeste.appendChild(opt);
        });
        if (btnTestarCorte) btnTestarCorte.disabled = false;
      }
    }

    // O servidor aceita somente vídeos dentro de cortes\ na simulação de postagem
    const selDry = el("dev-sel-video-dry");
    const btnDry = el("dev-btn-postar-dry");
    if (selDry) {
      const valorAntes = selDry.value;
      selDry.innerHTML = "";
      let listaDry = (Array.isArray(res.videos_dry_run) ? res.videos_dry_run : [])
        .filter((v) => v && v.caminho && /[\\/]cortes[\\/]/i.test(v.caminho) && /\.mp4$/i.test(v.caminho));

      if (listaDry.length === 0) {
        const fallbackCortes = await apiDev("/api/cortes");
        if (fallbackCortes && Array.isArray(fallbackCortes.cortes)) {
          listaDry = fallbackCortes.cortes
            .filter((c) => c && c.caminho && /\.mp4$/i.test(c.caminho))
            .map((c) => ({
              caminho: c.caminho,
              rotulo: `${c.canal || "Corte"} · ${c.titulo || c.nome_arquivo}`
            }));
        }
      }

      if (listaDry.length === 0) {
        const opt = document.createElement("option");
        opt.value = "";
        opt.textContent = "Gere um corte primeiro para liberar a simulação";
        selDry.appendChild(opt);
        if (btnDry) btnDry.disabled = true;
      } else {
        listaDry.forEach((v) => {
          const opt = document.createElement("option");
          opt.value = v.caminho;
          opt.textContent = formatarRotuloCorteDryRun(v);
          selDry.appendChild(opt);
        });
        if (valorAntes && Array.from(selDry.options).some((o) => o.value === valorAntes)) {
          selDry.value = valorAntes;
        }
        if (btnDry) btnDry.disabled = false;
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
      badge.textContent = "Modelo inicial (salvar cria o arquivo)";
    } else if (cfgAlvoAtual === "raiz" && !item.existe && !item.salvo) {
      badge.className = "dev-pilula-neutra";
      badge.textContent = "Vazio (usa os padrões do corte)";
    } else {
      badge.className = "dev-pilula-ok";
      badge.textContent = "Salvo e em dia";
    }
  }

  function renderizarAbaConfig() {
    document.querySelectorAll(".dev-cfg-aba").forEach((btn) => {
      const ativo = btn.getAttribute("data-cfg-alvo") === cfgAlvoAtual;
      btn.classList.toggle("ativa", ativo);
      btn.setAttribute("aria-selected", ativo ? "true" : "false");
      btn.tabIndex = ativo ? 0 : -1;
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
    btn.querySelector("span").textContent = "Conferindo e salvando…";
    msg.className = "dev-cfg-msg";
    msg.textContent = "Conferindo se o texto da configuração está válido…";

    try {
      const res = await apiDev("/api/dev/configs", "POST", {
        alvo: cfgAlvoAtual,
        conteudo: texto
      });
      if (res.ok === false) {
        msg.className = "dev-cfg-msg erro";
        msg.textContent = humanizarErroToml(res.erro);
        return;
      }
      cacheConfigs[cfgAlvoAtual].salvo = texto;
      cacheConfigs[cfgAlvoAtual].existe = true;
      cacheConfigs[cfgAlvoAtual].usando_exemplo = false;
      if (res.caminho) cacheConfigs[cfgAlvoAtual].caminho = res.caminho;
      atualizarIndicadorSujeiraConfig();
      msg.className = "dev-cfg-msg sucesso";
      msg.textContent = normalizarTextoPortugues(res.mensagem) || "Configuração salva com sucesso.";
    } finally {
      btn.disabled = false;
      btn.querySelector("span").textContent = "Salvar configuração";
    }
  }

  // --- Diagnóstico de Contas e Servidor de Tokens ---
  function definirLinhaDiagnostico(idNodo, texto, estado, dica) {
    const nodo = el(idNodo);
    if (!nodo) return;
    nodo.textContent = texto;
    if (estado) nodo.setAttribute("data-estado", estado);
    else nodo.removeAttribute("data-estado");
    nodo.title = dica || texto;
  }

  async function checarContasETokens() {
    const btn = el("dev-btn-checar-contas");
    const online = Boolean(el("dev-chk-status-online") && el("dev-chk-status-online").checked);
    const pilula = el("dev-pilula-worker");

    btn.disabled = true;
    btn.querySelector("span").textContent = "Verificando…";
    pilula.className = "dev-pilula-neutra";
    pilula.textContent = "Verificando…";

    try {
      const res = await apiDev(`/api/dev/status-contas?online=${online ? "1" : "0"}`);
      if (!res || res.ok === false) {
        pilula.className = "dev-pilula-erro";
        pilula.textContent = "Falha na consulta";
        definirLinhaDiagnostico(
          "dev-st-worker",
          normalizarTextoPortugues((res && res.erro) || "Não foi possível consultar agora"),
          "alerta"
        );
        return;
      }

      const srv = res.servidor_tokens || {};
      if (!srv.configurado) {
        pilula.className = "dev-pilula-neutra";
        pilula.textContent = "Não configurado";
        definirLinhaDiagnostico(
          "dev-st-worker",
          "Endereço ainda não definido",
          "neutro",
          "Configure o endereço do servidor na aba 'Postagem e contas' quando quiser usar conexão direta."
        );
      } else if (srv.online) {
        const msTxt = typeof srv.latencia_ms === "number" ? ` (${srv.latencia_ms} ms)` : "";
        pilula.className = "dev-pilula-ok";
        pilula.textContent = "Servidor online";
        definirLinhaDiagnostico(
          "dev-st-worker",
          `Respondendo${msTxt}`,
          "ok",
          srv.url || ""
        );
      } else {
        pilula.className = "dev-pilula-erro";
        pilula.textContent = "Fora do ar";
        definirLinhaDiagnostico(
          "dev-st-worker",
          "Sem resposta na internet",
          "alerta",
          `${srv.url || ""} — ${normalizarTextoPortugues(srv.detalhe || "")}`
        );
      }

      const dir = res.direto || {};
      ["youtube", "instagram", "tiktok"].forEach((rede) => {
        const r = dir[rede] || {};
        if (r.conectado && r.valido) {
          definirLinhaDiagnostico(
            `dev-st-${rede}`,
            r.conta ? `Conectada · ${r.conta}` : "Conectada e válida",
            "ok"
          );
        } else if (r.conectado) {
          definirLinhaDiagnostico(
            `dev-st-${rede}`,
            "Acesso vencido (reconectar)",
            "alerta"
          );
        } else {
          definirLinhaDiagnostico(
            `dev-st-${rede}`,
            normalizarTextoPortugues(r.detalhe) || "Não conectada",
            "neutro"
          );
        }
      });

      const pz = res.postiz || {};
      const qtd = Array.isArray(pz.canais) ? pz.canais.length : 0;
      if (pz.conectado) {
        definirLinhaDiagnostico(
          "dev-st-postiz",
          `Conectado (${qtd} cana${qtd === 1 ? "l" : "is"})`,
          "ok"
        );
      } else {
        definirLinhaDiagnostico(
          "dev-st-postiz",
          "Não conectado",
          "neutro",
          "Você pode conectar sua chave do Postiz na aba 'Postar'."
        );
      }
    } finally {
      btn.disabled = false;
      btn.querySelector("span").textContent = "Verificar contas e servidor";
    }
  }

  // --- Polling de Registros ao Vivo (Corte + Postagem) ---
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
      subCorte.textContent =
        partes.join(" · ") ||
        normalizarTextoPortugues(corte.ultima_mensagem) ||
        "Aguardando início de um corte para mostrar o andamento em tempo real…";
    }

    const preCorte = el("dev-pre-log-corte");
    if (preCorte) {
      const txtCorte = (corte.log && corte.log.trim())
        ? corte.log
        : "Nenhuma atividade de corte registrada nesta sessão ainda.";
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
    const subPost = el("dev-sub-post");

    if (badgePost) {
      if (pRodando) {
        badgePost.className = "tally-pilula tally-preparando";
        badgePost.textContent = "SIMULANDO…";
        if (subPost) subPost.textContent = "Testando envio simulado passo a passo…";
      } else if (post.exit_code === 0) {
        badgePost.className = "tally-pilula dev-pilula-ok";
        badgePost.textContent = "CONCLUÍDO";
        if (subPost) subPost.textContent = "Simulação concluída com sucesso (nenhum vídeo foi publicado nas redes).";
      } else if (post.exit_code !== null && post.exit_code !== undefined) {
        badgePost.className = "tally-pilula tally-ao-vivo";
        badgePost.textContent = "FALHOU";
        if (subPost) subPost.textContent = "A simulação encontrou um problema. Veja os detalhes abaixo.";
      } else {
        badgePost.className = "tally-pilula tally-parado";
        badgePost.textContent = "AGUARDANDO";
        if (subPost) subPost.textContent = "Simulação segura: testa o envio sem publicar vídeos reais nas redes.";
      }
    }

    const btnPostDry = el("dev-btn-postar-dry");
    if (btnPostDry) {
      const semCortes = el("dev-sel-video-dry") && !el("dev-sel-video-dry").value;
      btnPostDry.disabled = pRodando || semCortes;
      btnPostDry.querySelector("span").textContent = pRodando ? "Simulando envio…" : "Simular postagem";
    }

    const prePost = el("dev-pre-log-postagem");
    if (prePost) {
      if (post.log && post.log.trim()) {
        const formatado = formatarLogPostagem(post.log);
        if (prePost.textContent !== formatado) {
          prePost.textContent = formatado;
          if (el("dev-autoscroll-post") && el("dev-autoscroll-post").checked) {
            prePost.scrollTop = prePost.scrollHeight;
          }
        }
      }
    }
  }

  // --- Modal de Confirmação Antes de Lançar Versão ---
  function abrirModalConfirmacaoLancamento() {
    const modal = el("dev-modal-lancar");
    if (!modal) return;

    const simular = Boolean(el("dev-chk-simular-release") && el("dev-chk-simular-release").checked);
    const selo = el("dev-modal-lancar-selo");
    const titulo = el("dev-modal-lancar-titulo");
    const verAtualEl = el("dev-modal-versao-atual");
    const destinoEl = el("dev-modal-destino-texto");
    const avisoEl = el("dev-modal-lancar-aviso");
    const txtConfirmar = el("dev-txt-confirmar-lancar");

    if (verAtualEl) verAtualEl.textContent = `v${versaoAtualDev}`;

    if (simular) {
      if (selo) {
        selo.className = "dev-pilula-ok";
        selo.textContent = "Modo simulação";
      }
      if (titulo) titulo.textContent = "Confirmar simulação de lançamento?";
      if (destinoEl) destinoEl.textContent = "Somente neste computador (nada vai ao GitHub)";
      if (avisoEl) {
        avisoEl.textContent =
          "Modo seguro selecionado: o assistente vai abrir apenas para testar a preparação da nova versão neste computador, sem publicar nada para os amigos no GitHub.";
      }
      if (txtConfirmar) txtConfirmar.textContent = "Sim, abrir em modo simulação";
    } else {
      if (selo) {
        selo.className = "dev-pilula-erro";
        selo.textContent = "Publicação oficial";
      }
      if (titulo) titulo.textContent = "Confirmar lançamento de versão?";
      if (destinoEl) destinoEl.textContent = "GitHub oficial (visível para os amigos)";
      if (avisoEl) {
        avisoEl.textContent =
          "Atenção: ao confirmar, o assistente de lançamento será aberto para criar e publicar uma nova versão oficial no GitHub. Assim que entrar no ar, o painel dos seus amigos vai avisar e atualizar automaticamente.";
      }
      if (txtConfirmar) txtConfirmar.textContent = "Sim, abrir assistente de lançamento";
    }

    if (typeof modal.showModal === "function") {
      if (!modal.open) modal.showModal();
    } else {
      modal.setAttribute("open", "open");
    }

    // Foco inicial seguro no botão Cancelar
    const btnCancelar = el("dev-btn-cancelar-lancar");
    if (btnCancelar) {
      setTimeout(() => btnCancelar.focus(), 20);
    }
  }

  function fecharModalConfirmacaoLancamento(foiCancelado) {
    const modal = el("dev-modal-lancar");
    if (modal) {
      if (typeof modal.close === "function" && modal.open) {
        modal.close();
      } else {
        modal.removeAttribute("open");
      }
    }
    if (foiCancelado) {
      const statusEl = el("dev-status-update-linha");
      if (statusEl) {
        statusEl.removeAttribute("data-erro");
        statusEl.removeAttribute("data-ok");
        statusEl.textContent = "Lançamento cancelado. Nenhuma versão foi publicada.";
      }
      const btnLancar = el("dev-btn-lancar-versao");
      if (btnLancar) btnLancar.focus();
    }
  }

  async function confirmarEAbrirLancamento() {
    const simular = Boolean(el("dev-chk-simular-release") && el("dev-chk-simular-release").checked);
    const statusEl = el("dev-status-update-linha");
    const btnConfirmar = el("dev-btn-confirmar-lancar");

    if (btnConfirmar) btnConfirmar.disabled = true;
    try {
      const res = await apiDev("/api/dev/lancar-versao", "POST", { simular });
      fecharModalConfirmacaoLancamento(false);
      if (res && res.ok) {
        statusEl.removeAttribute("data-erro");
        statusEl.setAttribute("data-ok", "true");
        statusEl.textContent = simular
          ? "Assistente de versão aberto em modo simulação (nada será enviado ao GitHub)."
          : "Assistente de versão aberto em uma nova janela. Preencha os dados lá para concluir.";
      } else {
        statusEl.removeAttribute("data-ok");
        statusEl.setAttribute("data-erro", "true");
        statusEl.textContent = normalizarTextoPortugues((res && res.erro) || "Não foi possível abrir o assistente de versão.");
      }
    } finally {
      if (btnConfirmar) btnConfirmar.disabled = false;
    }
  }

  // --- Eventos dos Botões da Aba Dev ---
  function registrarEventosDev() {
    // 1. Abrir pastas (cortes, logs, config, dados_local)
    document.querySelectorAll(".dev-btn-pasta").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const pasta = btn.getAttribute("data-pasta");
        const nomePasta = btn.getAttribute("data-nome-pasta") || btn.textContent.trim();
        const fbPastas = el("dev-feedback-pastas");

        btn.disabled = true;
        try {
          const res = await apiDev("/api/dev/abrir-pasta", "POST", { pasta });
          if (fbPastas) {
            fbPastas.hidden = false;
            if (res && res.ok !== false) {
              fbPastas.removeAttribute("data-erro");
              fbPastas.textContent = `${nomePasta} aberta no Explorador de Arquivos.`;
            } else {
              fbPastas.setAttribute("data-erro", "true");
              fbPastas.textContent = normalizarTextoPortugues(
                (res && res.erro) || `Não foi possível abrir ${nomePasta.toLowerCase()}.`
              );
            }
            if (timerFeedbackPastas) clearTimeout(timerFeedbackPastas);
            timerFeedbackPastas = setTimeout(() => {
              fbPastas.hidden = true;
            }, 5000);
          }
        } finally {
          btn.disabled = false;
        }
      });
    });

    // 2. Canal de atualização (beta / estavel)
    const obterCanalEscolhido = () => {
      const r = document.querySelector('input[name="dev-canal-update"]:checked');
      return (r && r.value) || "beta";
    };

    const definirStatusUpdate = (texto, tipo) => {
      const statusEl = el("dev-status-update-linha");
      if (!statusEl) return;
      statusEl.textContent = texto;
      statusEl.removeAttribute("data-erro");
      statusEl.removeAttribute("data-ok");
      if (tipo === "erro") statusEl.setAttribute("data-erro", "true");
      if (tipo === "ok") statusEl.setAttribute("data-ok", "true");
    };

    document.querySelectorAll('input[name="dev-canal-update"]').forEach((r) => {
      r.addEventListener("change", () => {
        const c = obterCanalEscolhido();
        el("dev-btn-aplicar-update").hidden = true;
        definirStatusUpdate(
          c === "beta"
            ? "Canal Beta selecionado: verifica versões de teste antes de liberar para os amigos."
            : "Canal Estável selecionado: consulta somente versões finais públicas (igual ao painel dos amigos).",
          "neutro"
        );
      });
    });

    el("dev-btn-checar-update").addEventListener("click", async () => {
      const canal = obterCanalEscolhido();
      const btn = el("dev-btn-checar-update");
      const btnAplicar = el("dev-btn-aplicar-update");
      const nomeCanal = canal === "beta" ? "Canal Beta" : "Canal Estável";

      btn.disabled = true;
      btn.querySelector("span").textContent = "Verificando…";
      definirStatusUpdate(`Consultando atualizações no ${nomeCanal}…`, "neutro");

      try {
        const res = await apiDev("/api/dev/atualizacao/checar", "POST", { canal });
        const info = humanizarMensagemAtualizacao(res, canal);
        definirStatusUpdate(info.texto, info.tipo);
        btnAplicar.hidden = !(res && res.etapa === "disponivel" && res.versao_nova);
      } finally {
        btn.disabled = false;
        btn.querySelector("span").textContent = "Verificar atualizações";
      }
    });

    el("dev-btn-aplicar-update").addEventListener("click", async () => {
      const canal = obterCanalEscolhido();
      const btn = el("dev-btn-aplicar-update");
      btn.disabled = true;
      btn.querySelector("span").textContent = "Instalando…";
      definirStatusUpdate("Baixando e instalando a nova versão sem mexer nos seus modelos e cortes…", "neutro");
      try {
        const res = await apiDev("/api/dev/atualizacao/aplicar", "POST", { canal });
        if (res && res.etapa === "concluido") {
          definirStatusUpdate("Atualização concluída! Recarregando o painel…", "ok");
          setTimeout(() => window.location.reload(), 900);
        } else {
          const info = humanizarMensagemAtualizacao(res, canal);
          definirStatusUpdate(info.texto, info.tipo);
        }
      } finally {
        btn.disabled = false;
        btn.querySelector("span").textContent = "Instalar atualização";
      }
    });

    el("dev-btn-voltar-update").addEventListener("click", async () => {
      const btn = el("dev-btn-voltar-update");
      btn.disabled = true;
      definirStatusUpdate("Verificando se há versão anterior guardada para restaurar…", "neutro");
      try {
        const res = await apiDev("/api/dev/atualizacao/voltar", "POST", {});
        if (res && res.etapa === "concluido") {
          definirStatusUpdate(normalizarTextoPortugues(res.msg) || "Versão anterior restaurada com sucesso.", "ok");
        } else {
          const info = humanizarMensagemAtualizacao(res, obterCanalEscolhido());
          definirStatusUpdate(info.texto, info.tipo);
        }
      } finally {
        btn.disabled = false;
      }
    });

    // 3. Lançar versão — SEMPRE pede confirmação clara no modal antes de publicar
    el("dev-btn-lancar-versao").addEventListener("click", () => {
      abrirModalConfirmacaoLancamento();
    });

    const modalLancar = el("dev-modal-lancar");
    if (modalLancar) {
      el("dev-btn-cancelar-lancar").addEventListener("click", () => {
        fecharModalConfirmacaoLancamento(true);
      });
      el("dev-btn-fechar-modal-lancar").addEventListener("click", () => {
        fecharModalConfirmacaoLancamento(true);
      });
      el("dev-btn-confirmar-lancar").addEventListener("click", () => {
        confirmarEAbrirLancamento();
      });
      modalLancar.addEventListener("cancel", (ev) => {
        ev.preventDefault();
        fecharModalConfirmacaoLancamento(true);
      });
      modalLancar.addEventListener("click", (ev) => {
        if (ev.target === modalLancar) {
          fecharModalConfirmacaoLancamento(true);
        }
      });
    }

    // 4. Ensaio 1: Corte de teste com vídeo de teste_video\
    el("dev-btn-testar-corte").addEventListener("click", async () => {
      const arquivo = el("dev-sel-video-teste").value;
      const modelo = el("dev-sel-modelo-teste").value;
      const minutos = el("dev-sel-minutos-teste").value || "2";
      const fb = el("dev-feedback-corte");
      if (!arquivo) {
        definirFeedbackCurto(fb, "Nenhum vídeo de amostra encontrado.", "erro");
        return;
      }
      el("dev-btn-testar-corte").disabled = true;
      definirFeedbackCurto(fb, "Iniciando corte de teste…", "neutro");
      try {
        const res = await apiDev("/api/dev/testar-corte", "POST", {
          arquivo,
          modelo,
          legenda: "opus",
          minutos
        });
        if (res && res.ok) {
          definirFeedbackCurto(fb, "Corte iniciado — acompanhe no registro abaixo.", "ok");
        } else {
          definirFeedbackCurto(fb, normalizarTextoPortugues((res && res.erro) || "Não foi possível iniciar o corte."), "erro");
        }
        await atualizarLogsAoVivo();
      } finally {
        el("dev-btn-testar-corte").disabled = false;
      }
    });

    el("dev-btn-parar-corte").addEventListener("click", async () => {
      el("dev-btn-parar-corte").disabled = true;
      definirFeedbackCurto(el("dev-feedback-corte"), "Parando o corte com segurança…", "neutro");
      await apiDev("/api/parar", "POST", {});
      await atualizarLogsAoVivo();
      await carregarResumoDev();
    });

    // 5. Ensaio 2: Simulação de Postagem (sem publicar de verdade)
    el("dev-btn-postar-dry").addEventListener("click", async () => {
      const video = el("dev-sel-video-dry").value;
      const via = el("dev-sel-via-dry").value;
      const titulo = el("dev-inp-titulo-dry").value.trim();
      const redes = Array.from(document.querySelectorAll('input[name="dev-rede-dry"]:checked')).map((c) => c.value);
      const fb = el("dev-feedback-dry");

      if (!video) {
        definirFeedbackCurto(fb, "Escolha um corte pronto primeiro.", "erro");
        return;
      }
      if (redes.length === 0) {
        definirFeedbackCurto(fb, "Marque pelo menos uma rede social.", "erro");
        return;
      }

      el("dev-btn-postar-dry").disabled = true;
      definirFeedbackCurto(fb, "Iniciando simulação…", "neutro");
      const res = await apiDev("/api/dev/postar-dry-run", "POST", {
        video,
        via,
        titulo: titulo || "Teste de postagem — CorteLive",
        descricao: "Simulação de envio pelo painel CorteLive #shorts #cortes",
        redes: redes.join(",")
      });
      if (res && res.ok) {
        definirFeedbackCurto(fb, "Simulação iniciada — veja o registro abaixo.", "ok");
      } else {
        definirFeedbackCurto(fb, normalizarTextoPortugues((res && res.erro) || "Não foi possível simular a postagem."), "erro");
      }
      await atualizarLogsAoVivo();
    });

    // 6. Ensaio 3: Status das contas e servidor de tokens
    el("dev-btn-checar-contas").addEventListener("click", checarContasETokens);

    // 7. Controles dos registros ao vivo
    el("dev-btn-copiar-corte").addEventListener("click", (ev) => {
      copiarParaClipboard(el("dev-pre-log-corte").textContent, ev.currentTarget, "Copiar");
    });
    el("dev-btn-copiar-post").addEventListener("click", (ev) => {
      copiarParaClipboard(el("dev-pre-log-postagem").textContent, ev.currentTarget, "Copiar");
    });
    el("dev-btn-limpar-corte").addEventListener("click", async () => {
      await apiDev("/api/dev/limpar-log", "POST", { alvo: "corte" });
      el("dev-pre-log-corte").textContent = "Registro limpo.";
      definirFeedbackCurto(el("dev-feedback-corte"), "", "neutro");
      await atualizarLogsAoVivo();
    });
    el("dev-btn-limpar-post").addEventListener("click", async () => {
      await apiDev("/api/dev/limpar-log", "POST", { alvo: "postagem" });
      el("dev-pre-log-postagem").textContent = "Registro de postagem limpo.";
      definirFeedbackCurto(el("dev-feedback-dry"), "", "neutro");
      await atualizarLogsAoVivo();
    });

    // 8. Editor de Configuração (com navegação por setas nas abas)
    const abasCfg = Array.from(document.querySelectorAll(".dev-cfg-aba"));
    const selecionarAbaCfg = (aba, focar = false) => {
      if (!aba) return;
      cacheConfigs[cfgAlvoAtual].conteudo = el("dev-textarea-cfg").value;
      cfgAlvoAtual = aba.getAttribute("data-cfg-alvo") || "cortelive";
      el("dev-cfg-feedback").className = "dev-cfg-msg";
      el("dev-cfg-feedback").textContent = TEXTO_AJUDA_CFG_PADRAO;
      renderizarAbaConfig();
      if (focar) aba.focus();
    };

    abasCfg.forEach((aba, idx) => {
      aba.addEventListener("click", () => selecionarAbaCfg(aba, false));
      aba.addEventListener("keydown", (ev) => {
        if (ev.key === "ArrowRight" || ev.key === "ArrowLeft") {
          ev.preventDefault();
          const prox = ev.key === "ArrowRight"
            ? abasCfg[(idx + 1) % abasCfg.length]
            : abasCfg[(idx - 1 + abasCfg.length) % abasCfg.length];
          selecionarAbaCfg(prox, true);
        }
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
      msg.textContent = "Arquivo recarregado do computador.";
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

      // Move o modal de confirmação para o body para camada superior limpa
      const modalLancar = el("dev-modal-lancar");
      if (modalLancar && document.body) {
        document.body.appendChild(modalLancar);
      }

      criarBotaoAbaDev();
      registrarEventosDev();

      const params = new URLSearchParams(window.location.search);
      if (params.get("aba") === "dev") {
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
