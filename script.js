let dadosGlobais = [];
let paginaAtual = 1;
const itensPorPagina = 20;

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

function atualizarTooltipMetodo() {
    const m = document.getElementById('metodo').value;
    const tt = document.getElementById('tooltip-metodo');
    
    if(m === 'graham') {
        tt.title = "Benjamin Graham: Calcula o valor intrínseco baseado no patrimônio e lucro da empresa, ordenando pela margem de segurança.";
    } else if(m === 'bazin') {
        tt.title = "Décio Bazin: Focado em renda passiva, calcula o Preço Teto para garantir um retorno de dividendos mínimo de 6% ao ano.";
    } else if(m === 'greenblatt') {
        tt.title = "Joel Greenblatt: Ranqueia as empresas combinando alta rentabilidade operacional com baixo múltiplo de preço.";
    } else if(m === 'lynch') {
        tt.title = "Peter Lynch: Identifica ações de crescimento negociadas a preços razoáveis dividindo o P/L pelo crescimento da receita.";
    }
}

async function carregarDados() {
    const tbody = document.querySelector('#tabela-resultados tbody');
    tbody.innerHTML = `<tr><td colspan="13"><div class="spinner-box"><div class="spinner"></div><strong>Consultando base de dados do mercado...</strong></div></td></tr>`;
    
    const p = param => document.getElementById(param).value;
    
    // ATUALIZADO PARA A API DE PRODUÇÃO NO RENDER
    const url = `https://jmpinvestimentos.onrender.com/api/rankings?metodo=${p('metodo')}&liq_min=${p('liq_min')}&pl_max=${p('pl_max')}&pvp_max=${p('pvp_max')}&dy_min=${p('dy_min')}&roe_min=${p('roe_min')}&roic_min=${p('roic_min')}&margem_min=${p('margem_min')}&cagr_min=${p('cagr_min')}`;
    
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
        col1.innerHTML = 'Valor Justo Graham <span class="tooltip" title="Preço teto estimado pela fórmula de Benjamin Graham baseada em LPA e VPA.">ⓘ</span>';
        col2.innerHTML = 'Margem de Segurança <span class="tooltip" title="Percentual de desconto do preço atual em relação ao Valor Justo estimado.">ⓘ</span>';
    } else if(m === 'bazin') {
        col1.innerHTML = 'Preço Teto Bazin <span class="tooltip" title="Preço máximo para garantir um retorno de dividendos mínimo de 6% ao ano.">ⓘ</span>';
        col2.innerHTML = 'Potencial de Renda <span class="tooltip" title="Margem percentual entre a cotação atual e o Preço Teto calculado.">ⓘ</span>';
    } else if(m === 'greenblatt') {
        col1.innerHTML = 'Pontuação Geral <span class="tooltip" title="Soma dos rankings de ROIC e EV/EBIT. Quanto menor a pontuação, melhor.">ⓘ</span>';
        col2.style.display = "none"; 
    } else if(m === 'lynch') {
        col1.innerHTML = 'PEG Ratio <span class="tooltip" title="P/L dividido pelo crescimento da receita. Valores abaixo de 1.0 indicam subvalorização.">ⓘ</span>';
        col2.innerHTML = 'Crescimento (CAGR) <span class="tooltip" title="Taxa de crescimento anual composta da receita usada como base para o método.">ⓘ</span>';
    }

    renderizarTabela();
}

function renderizarTabela() {
    const tbody = document.querySelector('#tabela-resultados tbody');
    tbody.innerHTML = '';
    
    const inicio = (paginaAtual - 1) * itensPorPagina;
    const fim = inicio + itensPorPagina;
    const itens = dadosGlobais.slice(inicio, fim);
    const m = document.getElementById('metodo').value;

    itens.forEach(item => {
        let tdCol1 = '';
        let tdCol2 = '';

        if(m === 'graham') {
            tdCol1 = `<td><strong>R$ ${item.valor_justo.toFixed(2)}</strong></td>`;
            tdCol2 = `<td style="color: #4cd681; font-weight: bold;">${(item.potencial * 100).toFixed(1)}%</td>`;
        } else if(m === 'bazin') {
            tdCol1 = `<td><strong>R$ ${item.preco_teto.toFixed(2)}</strong></td>`;
            tdCol2 = `<td style="color: #4cd681; font-weight: bold;">${(item.potencial * 100).toFixed(1)}%</td>`;
        } else if(m === 'greenblatt') {
            tdCol1 = `<td><strong>${item.potencial} Pontos</strong></td>`;
        } else if(m === 'lynch') {
            tdCol1 = `<td><strong>${item.peg_ratio.toFixed(2)}</strong></td>`;
            tdCol2 = `<td style="color: #4cd681; font-weight: bold;">${(item.potencial * 100).toFixed(1)}% ao ano</td>`;
        }

        const fallbackAvatar = `https://ui-avatars.com/api/?name=${item.ticker.slice(0,2)}&background=21262d&color=4cd681&bold=true&size=64`;
        
        tbody.innerHTML += `
            <tr>
                <td><strong>${item.rank}</strong></td>
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
}

function mudarPagina(direcao) {
    const totalPaginas = Math.ceil(dadosGlobais.length / itensPorPagina);
    paginaAtual += direcao;
    if(paginaAtual < 1) paginaAtual = 1;
    if(paginaAtual > totalPaginas) paginaAtual = totalPaginas;
    renderizarTabela();
}

async function carregarTickers() {
    // ATUALIZADO PARA A API DE PRODUÇÃO NO RENDER
    const res = await fetch('https://jmpinvestimentos.onrender.com/api/tickers');
    const tickers = await res.json();
    const datalist = document.getElementById('lista-tickers');
    datalist.innerHTML = '';
    tickers.forEach(t => datalist.innerHTML += `<option value="${t}">`);
}

async function carregarAnalise() {
    const t = document.getElementById('input-ticker').value;
    const p = document.getElementById('select-periodo').value;
    if(!t) return;

    const fichaContainer = document.getElementById('ficha-container');
    const chartSection = document.getElementById('chart-section');
    const chartContainer = document.getElementById('chart-container');

    chartSection.style.display = "block";
    chartContainer.innerHTML = `<div class="spinner-box" style="min-height: 400px;"><div class="spinner"></div><strong>Processando gráfico técnico...</strong></div>`;
    
    if(fichaContainer.innerHTML.includes('placeholder-box') || fichaContainer.innerHTML === '') {
        fichaContainer.innerHTML = `<div class="spinner-box"><div class="spinner"></div><strong>Extraindo Raio-X fundamentalista...</strong></div>`;
    }

    try {
        // ATUALIZADO PARA A API DE PRODUÇÃO NO RENDER
        const res = await fetch(`https://jmpinvestimentos.onrender.com/api/analise?ticker=${t}&periodo=${p}`);
        const data = await res.json();

        if(data.error) {
            fichaContainer.innerHTML = `<div class="placeholder-box"><h3 style="color:#ef5350;">Atenção: ${data.error}</h3><p>Verifique se o código do ativo foi digitado corretamente.</p></div>`;
            chartSection.style.display = "none";
            return;
        }

        const f = data.fundamentos;
        const cd = data.chart_data;
        const fallbackAvatar = `https://ui-avatars.com/api/?name=${data.ticker.slice(0,2)}&background=21262d&color=4cd681&bold=true&size=64`;

        const fmtM = val => `R$ ${(val/1000000).toFixed(1)}M`;
        const fmtP = val => `${(val*100).toFixed(1)}%`;
        const fmtX = val => `${val.toFixed(2)}x`;

        fichaContainer.innerHTML = `
            <div class="raio-x-box">
                <div class="raio-x-header">
                    <div class="raio-x-title">
                        <img src="${data.logo}" alt="${data.ticker}" onerror="this.onerror=null; this.src='${fallbackAvatar}';">
                        <div>
                            <h2>${data.nome}</h2>
                            <span style="margin-top:5px; display:inline-block;">Ativo: <strong>${data.ticker}</strong></span>
                        </div>
                    </div>
                    <div class="raio-x-preco">
                        <p>Cotação Atual</p>
                        <h3>R$ ${f.preco.toFixed(2)}</h3>
                    </div>
                </div>

                <div class="raio-x-grid">
                    <div class="raio-x-section">
                        <h4>Múltiplos e Preço</h4>
                        <div class="raio-x-item">
                            <span class="raio-x-label">Preço / Lucro (P/L) <span class="tooltip" title="Preço da ação dividido pelo lucro anual por ação.">ⓘ</span></span> 
                            <span class="raio-x-val">${fmtX(f.pl)}</span>
                        </div>
                        <div class="raio-x-item">
                            <span class="raio-x-label">Preço / Patrimônio (P/VP) <span class="tooltip" title="Preço da ação dividido pelo patrimônio líquido por ação.">ⓘ</span></span> 
                            <span class="raio-x-val">${fmtX(f.pvp)}</span>
                        </div>
                        <div class="raio-x-item">
                            <span class="raio-x-label">EV / EBIT <span class="tooltip" title="Valor da firma (Preço + Dívidas) dividido pelo lucro operacional.">ⓘ</span></span> 
                            <span class="raio-x-val">${fmtX(f.evebit)}</span>
                        </div>
                        <div class="raio-x-item">
                            <span class="raio-x-label">Lucro por Ação (LPA) <span class="tooltip" title="Lucro líquido total da empresa dividido pelo número de ações.">ⓘ</span></span> 
                            <span class="raio-x-val">R$ ${f.lpa.toFixed(2)}</span>
                        </div>
                        <div class="raio-x-item">
                            <span class="raio-x-label">V. Patrimonial (VPA) <span class="tooltip" title="Patrimônio líquido total dividido pelo número de ações.">ⓘ</span></span> 
                            <span class="raio-x-val">R$ ${f.vpa.toFixed(2)}</span>
                        </div>
                    </div>

                    <div class="raio-x-section">
                        <h4>Rentabilidade e Eficiência</h4>
                        <div class="raio-x-item">
                            <span class="raio-x-label">Retorno Operacional (ROIC) <span class="tooltip" title="Eficiência em gerar lucros com todo o capital alocado (próprio + dívidas).">ⓘ</span></span> 
                            <span class="raio-x-val" style="color:#4cd681;">${fmtP(f.roic)}</span>
                        </div>
                        <div class="raio-x-item">
                            <span class="raio-x-label">Retorno Patrimônio (ROE) <span class="tooltip" title="Rentabilidade do dinheiro investido diretamente pelos acionistas.">ⓘ</span></span> 
                            <span class="raio-x-val">${fmtP(f.roe)}</span>
                        </div>
                        <div class="raio-x-item">
                            <span class="raio-x-label">Margem Líquida <span class="tooltip" title="Percentual de lucro líquido gerado para cada real faturado na receita.">ⓘ</span></span> 
                            <span class="raio-x-val">${fmtP(f.margem)}</span>
                        </div>
                        <div class="raio-x-item">
                            <span class="raio-x-label">Dividend Yield (DY) <span class="tooltip" title="Rendimento percentual gerado por dividendos pagos nos últimos 12 meses.">ⓘ</span></span> 
                            <span class="raio-x-val" style="color:#4cd681;">${fmtP(f.dy)}</span>
                        </div>
                        <div class="raio-x-item">
                            <span class="raio-x-label">Cresc. Receita (5 Anos) <span class="tooltip" title="Taxa anual composta de crescimento da receita líquida em 5 anos.">ⓘ</span></span> 
                            <span class="raio-x-val">${fmtP(f.crescimento)}</span>
                        </div>
                    </div>

                    <div class="raio-x-section">
                        <h4>Estrutura e Balanço</h4>
                        <div class="raio-x-item">
                            <span class="raio-x-label">Liquidez Diária <span class="tooltip" title="Volume financeiro médio negociado diariamente na bolsa.">ⓘ</span></span> 
                            <span class="raio-x-val">${fmtM(f.liquidez)}</span>
                        </div>
                        <div class="raio-x-item">
                            <span class="raio-x-label">Patrimônio Líquido <span class="tooltip" title="Valor total dos bens e direitos da empresa descontando as obrigações.">ⓘ</span></span> 
                            <span class="raio-x-val">${fmtM(f.patrimonio)}</span>
                        </div>
                        <div class="raio-x-item">
                            <span class="raio-x-label">Dívida Líq / Patrimônio <span class="tooltip" title="Grau de alavancagem. Valores acima de 1.0 exigem atenção ao endividamento.">ⓘ</span></span> 
                            <span class="raio-x-val">${fmtX(f.divida_patrimonio)}</span>
                        </div>
                    </div>
                </div>

                <div class="raio-x-actions">
                    <a href="${f.links.site_ri}" target="_blank" class="btn-ri">Site da Empresa (RI)</a>
                    <a href="${f.links.relatorio_oficial}" target="_blank" class="btn-relatorio">Obter Relatório Completo</a>
                </div>
            </div>
        `;

        if(cd && cd.dates.length > 0) {
            chartContainer.innerHTML = ''; 
            
            const traceCandle = {
                x: cd.dates, open: cd.open, high: cd.high, low: cd.low, close: cd.close,
                type: 'candlestick', name: 'Cotação (OHLC)',
                increasing: {line: {color: '#26a69a'}}, decreasing: {line: {color: '#ef5350'}}, yaxis: 'y'
            };
            const traceMA50 = {
                x: cd.dates, y: cd.ma50, type: 'scatter', mode: 'lines',
                line: {color: '#ff9800', width: 1.5}, name: 'Média Curta (50d)', yaxis: 'y'
            };
            const traceMA200 = {
                x: cd.dates, y: cd.ma200, type: 'scatter', mode: 'lines',
                line: {color: '#2962ff', width: 2}, name: 'Média Longa (200d)', yaxis: 'y'
            };
            const traceVolume = {
                x: cd.dates, y: cd.volume, type: 'bar', name: 'Volume',
                marker: {color: 'rgba(255, 255, 255, 0.15)'}, yaxis: 'y2'
            };
            const traceRSI = {
                x: cd.dates, y: cd.rsi, type: 'scatter', mode: 'lines',
                line: {color: '#b39ddb', width: 2}, name: 'RSI (Força)', xaxis: 'x', yaxis: 'y3'
            };

            const layout = {
                template: 'plotly_dark', plot_bgcolor: '#161b22', paper_bgcolor: '#0d1117',
                height: 550, margin: {l: 50, r: 20, t: 20, b: 30},
                grid: { rows: 2, columns: 1, subplots: [['xy'], ['xy3']], roworder: 'top to bottom' },
                xaxis: { type: 'category', nticks: 10, gridcolor: '#30363d', rangeslider: {visible: false} },
                yaxis: { title: 'Cotação (R$)', domain: [0.35, 1], gridcolor: '#30363d' },
                yaxis2: { overlaying: 'y', side: 'right', showgrid: false, showticklabels: false },
                yaxis3: { title: 'RSI (0-100)', domain: [0, 0.25], range: [0, 100], gridcolor: '#30363d' },
                shapes: [
                    {type: 'line', xref: 'paper', x0: 0, x1: 1, yref: 'y3', y0: 70, y1: 70, line: {color: '#ef5350', dash: 'dot', width: 1.5}},
                    {type: 'line', xref: 'paper', x0: 0, x1: 1, yref: 'y3', y0: 30, y1: 30, line: {color: '#26a69a', dash: 'dot', width: 1.5}}
                ],
                legend: {orientation: 'h', yanchor: 'bottom', y: 1.02, xanchor: 'right', x: 1}
            };

            Plotly.newPlot('chart-container', [traceCandle, traceMA50, traceMA200, traceVolume, traceRSI], layout);
        } else {
            chartContainer.innerHTML = `<div style="text-align:center; padding:30px; color:#8b949e;">Dados históricos insuficientes para traçar o gráfico técnico neste período.</div>`;
        }
    } catch (e) {
        fichaContainer.innerHTML = `<div class="placeholder-box"><h3 style="color:#ef5350;">Erro de comunicação</h3><p>Não foi possível processar a análise detalhada. Tente novamente.</p></div>`;
        chartSection.style.display = "none";
    }
}

window.onload = () => {
    atualizarTooltipMetodo();
    carregarDados();
};
