let dadosGlobais = [];
let paginaAtual = 1;
const itensPorPagina = 20;

/* ==============================================================================
   RELOGIO EM TEMPO REAL E STATUS DO MERCADO B3
   ============================================================================== */
let feriadosNacionais = [];

const diasHorarioEspecial = {
    '2026-02-18': { inicio: 13 * 60, fim: 17 * 60 + 55 }, 
    '2027-02-10': { inicio: 13 * 60, fim: 17 * 60 + 55 }, 
    '2026-12-24': { inicio: 0, fim: 0 },                  
    '2026-12-31': { inicio: 0, fim: 0 }                   
};

async function carregarFeriadosNacionais() {
    try {
        const anoAtual = new Date().getFullYear();
        const res = await fetch(`https://brasilapi.com.br/api/feriados/v1/${anoAtual}`);
        if (res.ok) {
            const data = await res.json();
            feriadosNacionais = data.map(f => f.date);
        }
    } catch (error) {
        console.warn("⚠️ Não foi possível carregar os feriados da BrasilAPI.", error);
    }
}

function atualizarRelogio() {
    const agora = new Date();
    const opcoesData = { timeZone: 'America/Sao_Paulo', day: '2-digit', month: '2-digit', year: 'numeric' };
    const opcoesHora = { timeZone: 'America/Sao_Paulo', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false };
    
    const dataStr = agora.toLocaleDateString('pt-BR', opcoesData);
    const horaStr = agora.toLocaleTimeString('pt-BR', opcoesHora);
    
    const clockElement = document.getElementById('clock-display');
    if (clockElement) {
        clockElement.innerText = `${horaStr} - ${dataStr}`;
    }
}

function verificarStatusMercadoB3() {
    const agora = new Date();
    const dataSP = agora.toLocaleDateString('sv-SE', { timeZone: 'America/Sao_Paulo' }); 
    const diaSemana = agora.getDay(); 
    
    const horas = agora.getHours();
    const minutos = agora.getMinutes();
    const tempoEmMinutos = horas * 60 + minutos;
    
    const statusBox = document.getElementById('market-status');
    const statusText = document.getElementById('status-text');
    if (!statusBox || !statusText) return;

    if (diaSemana === 0 || diaSemana === 6) {
        statusBox.className = "market-status closed";
        statusText.innerText = "B3 Mercado Fechado";
        return;
    }

    if (feriadosNacionais.includes(dataSP)) {
        statusBox.className = "market-status closed";
        statusText.innerText = "B3 Fechado (Feriado)";
        return;
    }

    let inicioMercado = 10 * 60; 
    let fimMercado = 18 * 60;    

    if (diasHorarioEspecial[dataSP]) {
        const configEspecial = diasHorarioEspecial[dataSP];
        inicioMercado = configEspecial.inicio;
        fimMercado = configEspecial.fim;

        if (inicioMercado === 0 && fimMercado === 0) {
            statusBox.className = "market-status closed";
            statusText.innerText = "B3 Fechado (Sem Pregão)";
            return;
        }
    }

    if (tempoEmMinutos >= inicioMercado && tempoEmMinutos < fimMercado) {
        statusBox.className = "market-status open";
        if (tempoEmMinutos >= (17 * 60 + 55)) {
            statusText.innerText = "B3 Leilão de Fechamento";
        } else {
            statusText.innerText = "B3 Mercado Ativo";
        }
    } else {
        statusBox.className = "market-status closed";
        if (tempoEmMinutos >= (9 * 60 + 45) && tempoEmMinutos < inicioMercado) {
            statusText.innerText = "B3 Pré-Abertura";
        } else {
            statusText.innerText = "B3 Mercado Fechado";
        }
    }
}

carregarFeriadosNacionais().then(() => {
    verificarStatusMercadoB3();
});

setInterval(atualizarRelogio, 1000);
setInterval(verificarStatusMercadoB3, 30000);
atualizarRelogio();

/* ==============================================================================
   NAVEGAÇÃO, TOOLTIPS E SELEÇÃO DE ESTRATÉGIA POR PILLS
   ============================================================================== */
function mudarAba(aba) {
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.aba-content').forEach(c => c.classList.remove('active'));
    
    if(aba === 'screener') {
        document.querySelectorAll('.tab-btn')[0].classList.add('active');
        document.getElementById('aba-screener').classList.add('active');
    } else {
        document.querySelectorAll('.tab-btn')[1].classList.add('active');
        document.getElementById('aba-detalhes').classList.add('active');
        if(document.getElementById('lista-tickers').children.length === 0) carregarTickers();
    }
}

function selecionarEstrategia(metodo) {
    document.querySelectorAll('.strategy-pill').forEach(btn => {
        btn.classList.toggle('active', btn.getAttribute('data-metodo') === metodo);
    });
    document.getElementById('metodo').value = metodo;
    atualizarTooltipMetodo();
    carregarDados();
}

function atualizarTooltipMetodo() {
    const m = document.getElementById('metodo').value;
    const tt = document.getElementById('tooltip-metodo');
    
    if(m === 'graham') {
        tt.setAttribute('data-tooltip', "Benjamin Graham: Calcula o valor intrínseco baseado no patrimônio e lucro da empresa.");
    } else if(m === 'bazin') {
        tt.setAttribute('data-tooltip', "Décio Bazin: Focado em renda passiva, calcula o Preço Teto para garantir dividendo mínimo de 6% ao ano.");
    } else if(m === 'greenblatt') {
        tt.setAttribute('data-tooltip', "Joel Greenblatt: Magic Formula combinando alta rentabilidade com baixo múltiplo.");
    } else if(m === 'lynch') {
        tt.setAttribute('data-tooltip', "Peter Lynch: Identifica ações de crescimento dividindo o P/L pelo crescimento da receita (PEG).");
    }
}

function selecionarPeriodo(periodo) {
    document.querySelectorAll('.periodo-pill').forEach(btn => {
        btn.classList.toggle('active', btn.getAttribute('data-periodo') === periodo);
    });
    document.getElementById('select-periodo').value = periodo;
    carregarAnalise();
}

/* ==============================================================================
   CONSULTA E RENDERIZAÇÃO DA TABELA
   ============================================================================== */
async function carregarDados() {
    const tbody = document.querySelector('#tabela-resultados tbody');
    tbody.innerHTML = `<tr><td colspan="13"><div class="spinner-box"><div class="spinner"></div><strong>Consultando base de dados do mercado...</strong></div></td></tr>`;
    
    const p = param => document.getElementById(param) ? document.getElementById(param).value : 0;
    
    // INCLUÍDO 'divida_max' NA REQUISIÇÃO
    const url = `https://jmpinvestimentos.onrender.com/api/rankings?metodo=${p('metodo')}&liq_min=${p('liq_min')}&pl_max=${p('pl_max')}&pvp_max=${p('pvp_max')}&dy_min=${p('dy_min')}&roe_min=${p('roe_min')}&roic_min=${p('roic_min')}&margem_min=${p('margem_min')}&cagr_min=${p('cagr_min')}&divida_max=${p('divida_max')}`;
    
    try {
        const res = await fetch(url);
        dadosGlobais = await res.json();
        paginaAtual = 1;        
        document.getElementById('kpi-count').innerText = dadosGlobais.length;
        const pls = dadosGlobais.filter(d => d.pl > 0).map(d => d.pl).sort((a,b) => a-b);
        document.getElementById('kpi-pl').innerText = pls.length ? `${pls[Math.floor(pls.length/2)].toFixed(1)}x` : '-';
        const med = campo => dadosGlobais.reduce((acc, item) => acc + item[campo], 0) / (dadosGlobais.length || 1);
        document.getElementById('kpi-roic').innerText = `${(med('roic')*100).toFixed(1)}%`;
        document.getElementById('kpi-dy').innerText = `${(med('dy')*100).toFixed(1)}%`;

        const m = p('metodo');
        const col1 = document.getElementById('th-col1');
        const col2 = document.getElementById('th-col2');
        
        col1.style.display = "";
        col2.style.display = "";
        
        if(m === 'graham') {
            col1.innerHTML = 'Valor Justo Graham';
            col2.innerHTML = 'Margem de Segurança';
        } else if(m === 'bazin') {
            col1.innerHTML = 'Preço Teto Bazin';
            col2.innerHTML = 'Potencial de Renda';
        } else if(m === 'greenblatt') {
            col1.innerHTML = 'Pontuação Geral';
            col2.style.display = "none"; 
        } else if(m === 'lynch') {
            col1.innerHTML = 'PEG Ratio';
            col2.innerHTML = 'Crescimento (CAGR)';
        }

        renderizarTabela();
    } catch (error) {
        tbody.innerHTML = `<tr><td colspan="13"><div class="placeholder-box"><h3 style="color:#ef5350;">Aguardando Servidor</h3><p>O servidor está iniciando. Atualize a página em alguns segundos.</p></div></td></tr>`;
    }
}

function renderizarTabela() {
    const tbody = document.querySelector('#tabela-resultados tbody');
    tbody.innerHTML = '';
    
    if (!dadosGlobais || dadosGlobais.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="13">
                    <div class="placeholder-box">
                        <i data-lucide="filter-x" style="width: 48px; height: 48px; color: #E11D48;"></i>
                        <h3 style="color: #F8FAFC; margin: 5px 0 0 0;">Nenhum ativo encontrado</h3>
                    </div>
                </td>
            </tr>
        `;
        if (window.lucide) lucide.createIcons();
        document.getElementById('pag-info').innerText = "Exibindo 0-0 de 0 ativos";
        document.getElementById('pag-atual').innerText = "Página 1";
        return;
    }

    const inicio = (paginaAtual - 1) * itensPorPagina;
    const fim = inicio + itensPorPagina;
    const itens = dadosGlobais.slice(inicio, fim);
    const m = document.getElementById('metodo').value;

    itens.forEach(item => {
        let tdCol1 = '';
        let tdCol2 = '';

        if(m === 'graham') {
            tdCol1 = `<td><strong>R$ ${item.valor_justo.toFixed(2)}</strong></td>`;
            tdCol2 = `<td class="val-positive">${(item.potencial * 100).toFixed(1)}%</td>`;
        } else if(m === 'bazin') {
            tdCol1 = `<td><strong>R$ ${item.preco_teto.toFixed(2)}</strong></td>`;
            tdCol2 = `<td class="val-positive">${(item.potencial * 100).toFixed(1)}%</td>`;
        } else if(m === 'greenblatt') {
            tdCol1 = `<td><strong>${item.potencial} pts</strong></td>`;
        } else if(m === 'lynch') {
            tdCol1 = `<td><strong>${item.peg_ratio.toFixed(2)}</strong></td>`;
            tdCol2 = `<td class="val-positive">${(item.crescimento * 100).toFixed(1)}% a.a.</td>`;
        }

        const fallbackAvatar = `https://ui-avatars.com/api/?name=${item.ticker.slice(0,2)}&background=1E293B&color=10B981&bold=true&size=64`;
        
        tbody.innerHTML += `
            <tr>
                <td><span class="rank-badge">${item.rank}º</span></td>
                <td><img src="${item.logo}" class="logo-img" alt="${item.ticker}" onerror="this.onerror=null; this.src='${fallbackAvatar}';"></td>
                <td>${item.nome}</td>
                <td><strong>${item.ticker}</strong></td>
                <td>R$ ${item.preco.toFixed(2)}</td>
                ${tdCol1}
                ${tdCol2}
                <td>${item.pl.toFixed(1)}</td>
                <td>${item.pvp.toFixed(1)}</td>
                <td>${(item.roic * 100).toFixed(1)}%</td>
                <td>${(item.roe * 100).toFixed(1)}%</td>
                <td>${(item.dy * 100).toFixed(1)}%</td>
                <td>${(item.crescimento * 100).toFixed(1)}%</td>
            </tr>
        `;
    });

    document.getElementById('pag-info').innerText = `Exibindo ${dadosGlobais.length ? inicio + 1 : 0}-${Math.min(fim, dadosGlobais.length)} de ${dadosGlobais.length} ativos`;
    document.getElementById('pag-atual').innerText = `Página ${paginaAtual}`;
    if (window.lucide) lucide.createIcons();
}

function mudarPagina(direcao) {
    const totalPaginas = Math.ceil(dadosGlobais.length / itensPorPagina);
    paginaAtual += direcao;
    if(paginaAtual < 1) paginaAtual = 1;
    if(paginaAtual > totalPaginas) paginaAtual = totalPaginas;
    renderizarTabela();
}

async function carregarTickers() {
    const res = await fetch('https://jmpinvestimentos.onrender.com/api/tickers');
    const tickers = await res.json();
    const datalist = document.getElementById('lista-tickers');
    datalist.innerHTML = '';
    tickers.forEach(t => datalist.innerHTML += `<option value="${t}">`);
}

function gerarModuloProventos(dyDecimal, precoAtual, dpa12m) {
    if (dyDecimal <= 0.001 || dpa12m <= 0) {
        return `
            <div class="proventos-section">
                <h4><i data-lucide="coins"></i> Histórico de Proventos (12 Meses)</h4>
                <p style="color: #94A3B8; font-size: 0.85rem; margin: 0;">Este ativo não registrou distribuição relevante de proventos nos últimos 12 meses.</p>
            </div>
        `;
    }

    const dyPorcento = (dyDecimal * 100).toFixed(2);
    
    return `
        <div class="proventos-section">
            <h4><i data-lucide="coins"></i> Proventos Consolidados (Últimos 12 Meses)</h4>
            <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; margin-top: 15px;">
                <div style="background: var(--bg-card); padding: 12px 16px; border-radius: var(--radius-sm); border: 1px solid var(--border-color);">
                    <span style="font-size: 0.75rem; color: #94A3B8; text-transform: uppercase;">Total por Ação (DPA)</span>
                    <h3 style="margin: 5px 0 0 0; color: #34D399; font-family: var(--font-mono);">R$ ${dpa12m.toFixed(2)}</h3>
                </div>
                <div style="background: var(--bg-card); padding: 12px 16px; border-radius: var(--radius-sm); border: 1px solid var(--border-color);">
                    <span style="font-size: 0.75rem; color: #94A3B8; text-transform: uppercase;">Dividend Yield (DY)</span>
                    <h3 style="margin: 5px 0 0 0; color: #34D399; font-family: var(--font-mono);">${dyPorcento}%</h3>
                </div>
            </div>
        </div>
    `;
}

/* ==============================================================================
   ANÁLISE DETALHADA E GRÁFICOS TÉCNICOS
   ============================================================================== */
async function carregarAnalise() {
    const t = document.getElementById('input-ticker').value.toUpperCase().trim();
    const p = document.getElementById('select-periodo').value;
    if(!t) return;

    const fichaContainer = document.getElementById('ficha-container');
    const chartSection = document.getElementById('chart-section');
    const chartContainer = document.getElementById('chart-container');

    chartSection.style.display = "block";
    chartContainer.innerHTML = `<div class="spinner-box" style="min-height: 400px;"><div class="spinner"></div><strong>Carregando dados técnicos...</strong></div>`;
    
    if(fichaContainer.innerHTML.includes('placeholder-box') || fichaContainer.innerHTML === '') {
        fichaContainer.innerHTML = `<div class="spinner-box"><div class="spinner"></div><strong>Obtendo indicadores fundamentalistas...</strong></div>`;
    }

    try {
        const res = await fetch(`https://jmpinvestimentos.onrender.com/api/analise?ticker=${t}&periodo=${p}`);
        const data = await res.json();

        if(data.error) {
            fichaContainer.innerHTML = `<div class="placeholder-box"><h3 style="color:#ef5350;">Atenção: ${data.error}</h3><p>Verifique se o código do ativo foi digitado corretamente.</p></div>`;
            chartSection.style.display = "none";
            return;
        }

        const f = data.fundamentos;
        const cd = data.chart_data;
        const fallbackAvatar = `https://ui-avatars.com/api/?name=${data.ticker.slice(0,2)}&background=1E293B&color=10B981&bold=true&size=64`;

        const fmtM = val => `R$ ${(val/1000000).toFixed(1)}M`;
        const fmtP = val => `${(val*100).toFixed(1)}%`;
        const fmtX = val => `${val.toFixed(2)}x`;

        const proventosHtml = gerarModuloProventos(f.dy, f.preco, f.dpa_12m);

        fichaContainer.innerHTML = `
            <div class="raio-x-box">
                <div class="raio-x-header">
                    <div class="raio-x-title">
                        <img src="${data.logo}" alt="${data.ticker}" onerror="this.onerror=null; this.src='${fallbackAvatar}';">
                        <div>
                            <h2>${data.nome}</h2>
                            <span style="color:#94A3B8; font-weight:600;">Código B3: <strong style="color:#10B981">${data.ticker}</strong></span>
                        </div>
                    </div>
                    <div class="raio-x-preco">
                        <p style="margin:0; color:#94A3B8; font-size:0.8rem; text-transform:uppercase;">Cotação Atual</p>
                        <h3>R$ ${f.preco.toFixed(2)}</h3>
                    </div>
                </div>

                <div class="raio-x-grid">
                    <div class="raio-x-section">
                        <h4>Múltiplos e Preço</h4>
                        <div class="raio-x-item"><span style="color:#94A3B8;">Preço sobre Lucro (P/L)</span><span class="raio-x-val">${fmtX(f.pl)}</span></div>
                        <div class="raio-x-item"><span style="color:#94A3B8;">Preço sobre Patrimônio (P/VP)</span><span class="raio-x-val">${fmtX(f.pvp)}</span></div>
                        <div class="raio-x-item"><span style="color:#94A3B8;">EV sobre EBIT</span><span class="raio-x-val">${fmtX(f.evebit)}</span></div>
                        <div class="raio-x-item"><span style="color:#94A3B8;">Lucro por Ação (LPA)</span><span class="raio-x-val">R$ ${f.lpa.toFixed(2)}</span></div>
                        <div class="raio-x-item"><span style="color:#94A3B8;">Valor Patrimonial (VPA)</span><span class="raio-x-val">R$ ${f.vpa.toFixed(2)}</span></div>
                    </div>

                    <div class="raio-x-section">
                        <h4>Rentabilidade e Eficiência</h4>
                        <div class="raio-x-item"><span style="color:#94A3B8;">Retorno Operacional (ROIC)</span><span class="raio-x-val" style="color:#34D399;">${fmtP(f.roic)}</span></div>
                        <div class="raio-x-item"><span style="color:#94A3B8;">Retorno sobre Patrimônio (ROE)</span><span class="raio-x-val">${fmtP(f.roe)}</span></div>
                        <div class="raio-x-item"><span style="color:#94A3B8;">Margem Líquida</span><span class="raio-x-val">${fmtP(f.margem)}</span></div>
                        <div class="raio-x-item"><span style="color:#94A3B8;">Dividend Yield (DY)</span><span class="raio-x-val" style="color:#34D399;">${fmtP(f.dy)}</span></div>
                        <div class="raio-x-item"><span style="color:#94A3B8;">Crescimento da Receita (5a)</span><span class="raio-x-val">${fmtP(f.crescimento)}</span></div>
                    </div>

                    <div class="raio-x-section">
                        <h4>Estrutura e Balanço</h4>
                        <div class="raio-x-item"><span style="color:#94A3B8;">Liquidez Diária</span><span class="raio-x-val">${fmtM(f.liquidez)}</span></div>
                        <div class="raio-x-item"><span style="color:#94A3B8;">Patrimônio Líquido</span><span class="raio-x-val">${fmtM(f.patrimonio)}</span></div>
                        <div class="raio-x-item"><span style="color:#94A3B8;">Dívida Líquida sobre Patrimônio</span><span class="raio-x-val">${fmtX(f.divida_patrimonio)}</span></div>
                    </div>
                </div>

                ${proventosHtml}

                <div class="raio-x-actions" style="display:flex; gap:12px; justify-content:flex-end; margin-top:20px;">
                    <a href="${f.links.site_ri}" target="_blank" class="btn-ri">Relações com Investidores</a>
                    <a href="${f.links.relatorio_oficial}" target="_blank" class="btn-relatorio">Abrir no TradingView</a>
                </div>
            </div>
        `;

        if (window.lucide) lucide.createIcons();

        if(cd && cd.dates.length > 0) {
            chartContainer.innerHTML = ''; 
            
            const traceCandle = {
                x: cd.dates, open: cd.open, high: cd.high, low: cd.low, close: cd.close,
                type: 'candlestick', name: 'Cotação',
                increasing: {line: {color: '#10B981'}}, decreasing: {line: {color: '#F43F5E'}}, yaxis: 'y'
            };

            const traceMA50 = {
                x: cd.dates, y: cd.ma50, type: 'scatter', mode: 'lines',
                line: {color: '#F59E0B', width: 1.5}, name: 'MM 50d', yaxis: 'y'
            };
            const traceMA200 = {
                x: cd.dates, y: cd.ma200, type: 'scatter', mode: 'lines',
                line: {color: '#3B82F6', width: 2}, name: 'MM 200d', yaxis: 'y'
            };

            const traceVolume = {
                x: cd.dates, y: cd.volume, type: 'bar', name: 'Volume',
                marker: {color: 'rgba(255, 255, 255, 0.1)'}, yaxis: 'y2'
            };

            const traceRSI = {
                x: cd.dates, y: cd.rsi, type: 'scatter', mode: 'lines',
                line: {color: '#A855F7', width: 2}, name: 'IFR (14)', xaxis: 'x', yaxis: 'y3'
            };

            const lineSobrecompra = {
                x: [cd.dates[0], cd.dates[cd.dates.length - 1]], y: [70, 70],
                type: 'scatter', mode: 'lines', line: {color: '#F43F5E', width: 1, dash: 'dot'},
                name: 'Sobrecompra (70)', yaxis: 'y3', showlegend: false
            };
            const lineSobrevenda = {
                x: [cd.dates[0], cd.dates[cd.dates.length - 1]], y: [30, 30],
                type: 'scatter', mode: 'lines', line: {color: '#10B981', width: 1, dash: 'dot'},
                name: 'Sobrevenda (30)', yaxis: 'y3', showlegend: false
            };

            const layout = {
                template: 'plotly_dark', plot_bgcolor: '#0F172A', paper_bgcolor: '#111827',
                height: 560, margin: {l: 50, r: 20, t: 20, b: 30},
                grid: { rows: 2, columns: 1, subplots: [['xy'], ['xy3']], roworder: 'top to bottom' },
                xaxis: { type: 'category', nticks: 10, gridcolor: '#1E293B', rangeslider: {visible: false} },
                yaxis: { title: 'Preço (R$)', domain: [0.38, 1], gridcolor: '#1E293B' },
                yaxis2: { overlaying: 'y', side: 'right', showgrid: false, showticklabels: false },
                yaxis3: { title: 'IFR (0-100)', domain: [0, 0.26], range: [0, 100], gridcolor: '#1E293B' },
                legend: { orientation: 'h', yanchor: 'bottom', y: 1.02, xanchor: 'right', x: 1 }
            };

            Plotly.newPlot('chart-container', [traceCandle, traceMA50, traceMA200, traceVolume, traceRSI, lineSobrecompra, lineSobrevenda], layout);
        }
    } catch (e) {
        fichaContainer.innerHTML = `<div class="placeholder-box"><h3 style="color:#ef5350;">Erro ao carregar os dados do ativo</h3></div>`;
    }
}

window.onload = () => {
    atualizarTooltipMetodo();
    carregarDados();
};