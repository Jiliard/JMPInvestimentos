/* =============================================================================
   JMP INVESTIMENTOS - FRONT-END
   1. Configuração e formatação   2. Status da B3   3. Ranking e filtros   4. Análise de ação
   ============================================================================= */

/* ----------------------------- 1. CONFIGURAÇÃO ----------------------------- */

// Na Vercel (ou abrindo o arquivo local) usa a API de produção; servido pelo Flask, usa a própria origem
const API_PRODUCAO = 'https://jmpinvestimentos.onrender.com';
const API = (location.origin === API_PRODUCAO || location.port === '5000') ? '' : API_PRODUCAO;

// Filtros padrão (Tabela 3 da monografia)
const FILTROS_PADRAO = { liq_min: 100000, pl_max: 20, pvp_max: 2, divida_max: 1.5, roe_min: 10,
                         roic_min: 10, margem_min: 5, cagr_min: 0, dy_min: 0 };
const ITENS_POR_PAGINA = 25;
const CHAVE_CACHE = 'jmp:ultima-busca:v2';

let dados = [];
let pagina = 1;
let feriados = [];
let ultimaRequisicao = 0;

const $ = id => document.getElementById(id);
const icones = () => window.lucide && lucide.createIcons();
const ehNumero = v => typeof v === 'number' && Number.isFinite(v);
const escapar = t => String(t ?? '').replace(/[&<>"']/g, c => `&#${c.charCodeAt(0)};`);

// Formatação brasileira (1.234,56); dado ausente aparece como "N/D"
const formato = casas => new Intl.NumberFormat('pt-BR', { minimumFractionDigits: casas, maximumFractionDigits: casas });
const num = (v, casas = 2, sufixo = '') => ehNumero(v) ? formato(casas).format(v) + sufixo : 'N/D';
const pct = (v, casas = 1) => ehNumero(v) ? formato(casas).format(v * 100) + '%' : 'N/D';
const reais = v => ehNumero(v) ? 'R$ ' + formato(2).format(v) : 'N/D';
const grande = v => {   // valores grandes: R$ 1,2 bi / R$ 350,0 mi
    if (!ehNumero(v)) return 'N/D';
    const [divisor, sufixo] = Math.abs(v) >= 1e9 ? [1e9, ' bi'] : Math.abs(v) >= 1e6 ? [1e6, ' mi'] : [1e3, ' mil'];
    return 'R$ ' + formato(1).format(v / divisor) + sufixo;
};
const cor = v => ehNumero(v) && v < 0 ? 'baixa' : 'alta';

// Colunas da tabela: as duas do método (em destaque) e os indicadores fixos, iguais para todos
const coluna = (titulo, valor, dica = '', classe = () => '') => ({ titulo, valor, dica, classe });
const COLUNAS_FIXAS = [
    coluna('P/L', i => num(i.pl, 1), 'Preço sobre lucro'),
    coluna('P/VP', i => num(i.pvp, 2), 'Preço sobre valor patrimonial'),
    coluna('ROIC', i => pct(i.roic), 'Retorno sobre o capital investido'),
    coluna('ROE', i => pct(i.roe), 'Retorno sobre o patrimônio líquido'),
    coluna('DY', i => pct(i.dy), 'Dividendos dos últimos 12 meses sobre o preço'),
    coluna('Cresc. 5a', i => pct(i.crescimento), 'Crescimento anual composto do lucro por ação em 5 anos'),
];
const METODOS = {
    graham: {
        descricao: '<b>Preço Justo = √(22,5 × LPA × VPA)</b>. Considera empresas com lucro e patrimônio positivos e ordena pela maior margem de segurança.',
        colunas: [coluna('Valor Justo Graham', i => reais(i.valor_justo), 'Valor intrínseco de Graham', () => 'destaque'),
                  coluna('Margem de Segurança', i => pct(i.potencial), 'Quanto o preço justo está acima da cotação', i => cor(i.potencial))],
    },
    bazin: {
        descricao: '<b>Preço Teto = dividendos dos últimos 12 meses ÷ 6%</b>. Considera ações com Dividend Yield de pelo menos 6% e ordena pelo maior desconto ao teto.',
        colunas: [coluna('Preço Teto Bazin', i => reais(i.preco_teto), 'Preço máximo para render 6% ao ano em dividendos', () => 'destaque'),
                  coluna('Desconto ao Teto', i => pct(i.potencial), 'Quanto o preço teto está acima da cotação', i => cor(i.potencial))],
    },
    greenblatt: {
        descricao: '<b>Pontuação = posição em ROIC + posição em EV/EBIT</b> (menor é melhor). Exclui bancos, seguradoras, holdings financeiras e utilidade pública.',
        colunas: [coluna('Pontuação', i => num(i.potencial, 0) + ' pts', 'Soma das duas posições', () => 'destaque'),
                  coluna('Posições ROIC + EV/EBIT', i => `${num(i.pos_roic, 0)}º + ${num(i.pos_evebit, 0)}º`, 'Posição no ranking de ROIC + posição no de EV/EBIT')],
    },
    lynch: {
        descricao: '<b>PEG = P/L ÷ crescimento anual do lucro por ação em 5 anos (%)</b>. Abaixo de 1 indica crescimento a preço razoável. Ordena do menor PEG.',
        colunas: [coluna('PEG Ratio', i => num(i.peg_ratio), 'P/L dividido pelo crescimento', () => 'destaque'),
                  coluna('Crescimento (CAGR)', i => pct(i.crescimento) + ' a.a.', 'CAGR do lucro por ação em 5 anos', i => cor(i.crescimento))],
    },
};

const SETORES = {
    'Commercial Services': 'Serviços Comerciais', 'Communications': 'Comunicações',
    'Consumer Durables': 'Bens Duráveis', 'Consumer Non-Durables': 'Bens Não Duráveis',
    'Consumer Services': 'Serviços ao Consumidor', 'Distribution Services': 'Distribuição',
    'Electronic Technology': 'Tecnologia Eletrônica', 'Energy Minerals': 'Petróleo e Gás',
    'Finance': 'Financeiro e Imobiliário', 'Health Services': 'Serviços de Saúde',
    'Health Technology': 'Tecnologia em Saúde', 'Industrial Services': 'Serviços Industriais',
    'Miscellaneous': 'Diversos', 'Non-Energy Minerals': 'Mineração e Siderurgia',
    'Process Industries': 'Indústrias de Processo', 'Producer Manufacturing': 'Bens Industriais',
    'Retail Trade': 'Varejo', 'Technology Services': 'Tecnologia', 'Transportation': 'Transporte',
    'Utilities': 'Utilidade Pública',
};

const logoOuIniciais = (url, ticker) =>
    `<img src="${escapar(url)}" alt="" loading="lazy" onerror="this.onerror=null;this.src='https://ui-avatars.com/api/?name=${escapar(ticker).slice(0, 2)}&background=16223a&color=10b981&bold=true'">`;

function lerCache() { try { return JSON.parse(localStorage.getItem(CHAVE_CACHE)); } catch { return null; } }
function gravarCache(valor) { try { localStorage.setItem(CHAVE_CACHE, JSON.stringify(valor)); } catch { /* sem cache */ } }


/* ---------------------------- 2. STATUS DA B3 ------------------------------ */
function horaB3() {
    const p = Object.fromEntries(new Intl.DateTimeFormat('en-US', {
        timeZone: 'America/Sao_Paulo', weekday: 'short', year: 'numeric', month: '2-digit',
        day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
    }).formatToParts(new Date()).map(x => [x.type, x.value]));
    return { data: `${p.year}-${p.month}-${p.day}`, semana: p.weekday, minutos: (+p.hour % 24) * 60 + +p.minute };
}

function atualizarRelogio() {
    const agora = new Date();
    const opcoes = { timeZone: 'America/Sao_Paulo' };
    $('clock-display').textContent = `${agora.toLocaleTimeString('pt-BR', opcoes)} - ${agora.toLocaleDateString('pt-BR', opcoes)}`;
}

function atualizarMercado() {
    const { data, semana, minutos } = horaB3();
    // A B3 segue o horário de verão dos EUA: pregão até 17h com Nova York em horário de verão, senão até 18h
    const nyVerao = new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', timeZoneName: 'short' })
        .format(new Date()).includes('EDT');
    const fechamento = (nyVerao ? 17 : 18) * 60;
    let texto = 'B3 Fechada';
    let aberto = false;

    if (['Sat', 'Sun'].includes(semana)) texto = 'B3 Fechada (fim de semana)';
    else if (feriados.includes(data) || ['12-24', '12-31'].includes(data.slice(5))) texto = 'B3 Fechada (feriado)';
    else if (minutos >= 600 && minutos < fechamento) { aberto = true; texto = 'B3 Aberta'; }
    else if (minutos >= 585 && minutos < 600) texto = 'B3 Pré-abertura';

    $('market-status').className = `mercado ${aberto ? 'aberto' : 'fechado'}`;
    $('status-text').textContent = texto;
}

async function carregarFeriados() {
    try {
        const resposta = await fetch(`https://brasilapi.com.br/api/feriados/v1/${horaB3().data.slice(0, 4)}`);
        if (resposta.ok) feriados = (await resposta.json()).map(f => f.date);
    } catch { /* sem feriados: considera apenas fins de semana */ }
}


/* --------------------------- 3. RANKING E FILTROS -------------------------- */
function mudarAba(aba) {
    document.querySelectorAll('.aba-btn').forEach(b => b.classList.toggle('ativa', b.dataset.aba === aba));
    $('aba-screener').classList.toggle('ativa', aba === 'screener');
    $('aba-detalhes').classList.toggle('ativa', aba === 'detalhes');
    if (aba === 'detalhes' && !$('lista-tickers').children.length) carregarTickers();
}

function marcarEstrategia(metodo) {
    document.querySelectorAll('.estrategia').forEach(b => {
        b.classList.toggle('ativa', b.dataset.metodo === metodo);
        b.setAttribute('aria-checked', b.dataset.metodo === metodo);
    });
    $('metodo').value = metodo;
    $('descricao-metodo').innerHTML = METODOS[metodo].descricao;
    const titulo = (c, classe = '') => `<th class="${classe}" title="${c.dica}">${c.titulo}</th>`;
    $('cabecalho-tabela').innerHTML = '<th>#</th><th class="esq">Empresa</th><th>Cotação</th>' +
        METODOS[metodo].colunas.map(c => titulo(c, 'coluna-metodo')).join('') + COLUNAS_FIXAS.map(c => titulo(c)).join('');
}

function selecionarEstrategia(metodo) {
    marcarEstrategia(metodo);
    carregarDados();
}

// Mostra no título dos filtros quantos estão diferentes do padrão
function atualizarSeloFiltros() {
    const alterados = Object.keys(FILTROS_PADRAO).filter(c => $(c).value !== String(FILTROS_PADRAO[c])).length;
    $('selo-filtros').hidden = alterados === 0;
    $('selo-filtros').textContent = `${alterados} alterado${alterados > 1 ? 's' : ''}`;
}

function preencherFiltros(filtros) {
    Object.keys(FILTROS_PADRAO).forEach(campo => { $(campo).value = filtros[campo] ?? ''; });
}

function restaurarPadrao() {
    preencherFiltros(FILTROS_PADRAO);
    carregarDados();
}

function limparDataReferencia() {
    $('data_ref').value = '';
    carregarDados();
}

function lerTela() {
    const filtros = { metodo: $('metodo').value, data: $('data_ref').value };
    Object.keys(FILTROS_PADRAO).forEach(campo => { filtros[campo] = $(campo).value; });
    return filtros;
}

function status(texto, tipo = '') {
    $('status-dados').className = `status ${tipo}`;
    $('status-dados').textContent = texto;
}

function descreverData(dataBase, historico) {
    if (!dataBase) return '';
    const data = new Date(historico ? dataBase + 'T12:00' : dataBase);
    return historico
        ? `Modo de teste: dados do pregão de ${data.toLocaleDateString('pt-BR')}`
        : `Dados de ${data.toLocaleDateString('pt-BR')} às ${data.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })}`;
}

function mensagemTabela(titulo, texto = '', carregando = false) {
    const colunas = METODOS[$('metodo').value].colunas.length + COLUNAS_FIXAS.length + 3;
    $('tabela-resultados').tBodies[0].innerHTML = `<tr><td colspan="${colunas}" class="mensagem">
        ${carregando ? '<div class="carregador"></div>' : ''}<h3>${escapar(titulo)}</h3><p>${escapar(texto)}</p></td></tr>`;
}

/**
 * Consulta o ranking. Na abertura da página (usarCache = true) mostra na hora a
 * última busca salva no navegador e atualiza os dados por baixo dos panos.
 */
async function carregarDados(usarCache = false) {
    atualizarSeloFiltros();
    const filtros = lerTela();
    const id = ++ultimaRequisicao;
    const cache = usarCache && lerCache();

    if (cache?.dados) {
        exibir(cache.dados);
        status(`${descreverData(cache.dataBase, !!cache.filtros.data)} · atualizando...`, 'carregando');
    } else {
        mensagemTabela('Consultando o mercado...', '', true);
        status('Consultando...', 'carregando');
    }

    try {
        const resposta = await fetch(`${API}/api/rankings?${new URLSearchParams(filtros)}`);
        const corpo = await resposta.json().catch(() => null);
        if (id !== ultimaRequisicao) return;          // o usuário já mudou o filtro
        if (!resposta.ok || !Array.isArray(corpo)) throw new Error(corpo?.error || `Erro ${resposta.status} no servidor.`);

        const dataBase = resposta.headers.get('X-Data-Base') || '';
        // Com data de referência, o servidor devolve o dia usado (AAAA-MM-DD). Se não devolver,
        // ele ignorou a data (ex.: versão antiga publicada) e os dados seriam os atuais.
        if (filtros.data && !/^\d{4}-\d{2}-\d{2}$/.test(dataBase)) {
            throw new Error('O servidor não aplicou a data de referência (a versão publicada ainda não tem o modo de teste). Os dados atuais não foram exibidos para evitar confusão.');
        }
        exibir(corpo);
        status(descreverData(dataBase, !!filtros.data));
        gravarCache({ filtros, dados: corpo, dataBase });
    } catch (erro) {
        if (id !== ultimaRequisicao) return;
        const semConexao = erro instanceof TypeError;   // falha de rede (ex.: servidor acordando)
        const texto = semConexao ? 'Servidor iniciando. Tente novamente em alguns segundos.' : erro.message;
        // Sem conexão, mantém a última busca na tela; erro do servidor (ex.: data sem histórico) é mostrado na tabela
        if (semConexao && cache?.dados) return status(`${texto} Exibindo a última busca.`, 'erro');
        exibir([], false);
        status(texto, 'erro');
        mensagemTabela(filtros.data ? 'Sem dados para a data de referência' : 'Não foi possível carregar o ranking', texto);
    }
}

// Posição de cada ação num critério (empates recebem a mesma posição, como no back-end)
function posicoes(lista, campo, crescente) {
    const valores = lista.map(i => i[campo]);
    return valores.map(v => 1 + valores.filter(o => crescente ? o < v : o > v).length);
}

function exibir(lista, renderizar = true) {
    // Servidores antigos não enviam as posições da Magic Formula: calcula sobre a própria lista
    if ($('metodo').value === 'greenblatt' && lista.length && lista[0].pos_roic == null) {
        const roic = posicoes(lista, 'roic', false);
        const evebit = posicoes(lista, 'evebit', true);
        lista.forEach((item, i) => { item.pos_roic = roic[i]; item.pos_evebit = evebit[i]; });
    }
    dados = lista;
    pagina = 1;
    const valores = campo => dados.map(d => d[campo]).filter(ehNumero);
    const media = campo => { const v = valores(campo); return v.length ? v.reduce((a, b) => a + b) / v.length : null; };
    const pls = valores('pl').sort((a, b) => a - b);

    $('kpi-count').textContent = dados.length;
    $('kpi-pl').textContent = pls.length ? num(pls[Math.floor(pls.length / 2)], 1, 'x') : '-';
    $('kpi-roic').textContent = dados.length ? pct(media('roic')) : '-';
    $('kpi-dy').textContent = dados.length ? pct(media('dy')) : '-';
    if (renderizar) renderizarTabela();
}

function renderizarTabela() {
    if (!dados.length) {
        mensagemTabela('Nenhuma ação atende aos filtros', 'Afrouxe algum filtro ou use "Restaurar padrão".');
        $('pag-info').textContent = '0 ações';
        $('pag-atual').textContent = '1';
        return;
    }
    const colunas = METODOS[$('metodo').value].colunas;
    const inicio = (pagina - 1) * ITENS_POR_PAGINA;
    const fim = Math.min(inicio + ITENS_POR_PAGINA, dados.length);

    $('tabela-resultados').tBodies[0].innerHTML = dados.slice(inicio, fim).map(item => `
        <tr onclick="abrirAnalise('${escapar(item.ticker)}')" title="Ver análise de ${escapar(item.ticker)}">
            <td><span class="posicao">${item.rank}º</span></td>
            <td class="esq"><div class="empresa">${logoOuIniciais(item.logo, item.ticker)}
                <div><strong>${escapar(item.ticker)}</strong><small>${escapar(item.nome)}</small></div></div></td>
            <td>${reais(item.preco)}</td>
            ${colunas.map(c => `<td class="coluna-metodo ${c.classe(item)}">${c.valor(item)}</td>`).join('')}
            ${COLUNAS_FIXAS.map(c => `<td>${c.valor(item)}</td>`).join('')}
        </tr>`).join('');

    $('pag-info').textContent = `${inicio + 1}–${fim} de ${dados.length} ações`;
    $('pag-atual').textContent = `${pagina} de ${Math.ceil(dados.length / ITENS_POR_PAGINA)}`;
    icones();
}

function mudarPagina(direcao) {
    const total = Math.max(1, Math.ceil(dados.length / ITENS_POR_PAGINA));
    pagina = Math.min(Math.max(pagina + direcao, 1), total);
    renderizarTabela();
}

// Limita o campo de data ao período do histórico e informa esse período ao usuário
async function configurarDataReferencia() {
    const campo = $('data_ref');
    campo.max = horaB3().data;
    try {
        const { inicio, fim } = await (await fetch(`${API}/api/historico/periodo`)).json();
        const br = d => d.split('-').reverse().join('/');
        if (inicio) {
            campo.min = inicio;   // depois do último dia coletado, vale o último pregão disponível
            $('periodo-historico').textContent = `Histórico disponível de ${br(inicio)} a ${br(fim)}.`;
        } else {
            $('periodo-historico').textContent = 'Histórico ainda vazio: as coletas diárias começam a ser gravadas após a implantação.';
        }
    } catch { /* sem informação do período: o servidor também valida a data */ }
}


/* --------------------------- 4. ANÁLISE DE AÇÃO ---------------------------- */
async function carregarTickers() {
    try {
        const tickers = await (await fetch(`${API}/api/tickers`)).json();
        $('lista-tickers').innerHTML = tickers.map(t => `<option value="${escapar(t)}">`).join('');
    } catch { /* a busca continua funcionando digitando o código */ }
}

function abrirAnalise(ticker) {
    $('input-ticker').value = ticker;
    mudarAba('detalhes');
    carregarAnalise();
    window.scrollTo({ top: 0, behavior: 'smooth' });
}

function selecionarPeriodo(periodo) {
    document.querySelectorAll('.periodo').forEach(b => b.classList.toggle('ativa', b.dataset.periodo === periodo));
    $('select-periodo').value = periodo;
    carregarAnalise();
}

function montarFicha(d) {
    const f = d.fundamentos;
    const linha = (rotulo, valor, dica = '') => `<div class="linha" title="${dica}"><span>${rotulo}</span><span class="valor">${valor}</span></div>`;
    const temProventos = ehNumero(f.dy) && f.dy > 0;

    return `
    <div class="cartao">
        <div class="ficha-topo">
            <div class="empresa">${logoOuIniciais(d.logo, d.ticker)}
                <div><h2>${escapar(d.nome)}</h2><small>${escapar(d.ticker)} · ${escapar(SETORES[d.setor] || d.setor || 'Setor não informado')}</small></div></div>
            <div class="preco-atual"><span class="dica">Cotação</span><br><strong>${reais(f.preco)}</strong></div>
        </div>
        <div class="grupos">
            <div class="grupo"><h4>Preço e valuation</h4>
                ${linha('P/L', num(f.pl, 2, 'x'), 'Preço sobre lucro dos últimos 12 meses')}
                ${linha('P/VP', num(f.pvp, 2, 'x'), 'Preço sobre valor patrimonial')}
                ${linha('EV/EBIT', num(f.evebit, 2, 'x'), 'Valor da firma sobre lucro operacional')}
                ${linha('LPA', reais(f.lpa), 'Lucro por ação (12 meses)')}
                ${linha('VPA', reais(f.vpa), 'Valor patrimonial por ação')}</div>
            <div class="grupo"><h4>Rentabilidade e crescimento</h4>
                ${linha('ROE', pct(f.roe), 'Retorno sobre o patrimônio líquido')}
                ${linha('ROIC', pct(f.roic), 'Retorno sobre o capital investido')}
                ${linha('Margem líquida', pct(f.margem))}
                ${linha('Crescimento do lucro (5 anos)', ehNumero(f.crescimento) ? pct(f.crescimento) + ' a.a.' : 'N/D', 'CAGR do lucro por ação')}
                ${linha('Dividend Yield (12 meses)', pct(f.dy))}</div>
            <div class="grupo"><h4>Balanço e liquidez</h4>
                ${linha('Patrimônio líquido', grande(f.patrimonio))}
                ${linha('Dívida bruta', grande(f.divida_total))}
                ${linha('Dívida / Patrimônio', num(f.divida_patrimonio, 2, 'x'))}
                ${linha('Liquidez média diária', grande(f.liquidez), 'Média dos últimos 30 pregões')}
                ${linha('Dividendos por ação (12 meses)', temProventos ? reais(f.dpa_12m) : 'Sem proventos')}</div>
        </div>
        <div class="links">
            <a class="botao-secundario" href="${escapar(f.links.site_ri)}" target="_blank" rel="noopener">Site de RI</a>
            <a class="botao-secundario" href="${escapar(f.links.relatorio_oficial)}" target="_blank" rel="noopener">Ver no TradingView</a>
        </div>
    </div>`;
}

function desenharGrafico(g, periodo) {
    const semanal = ['5 Anos', '10 Anos'].includes(periodo);
    const subiu = g.close.map((c, i) => c >= g.open[i]);
    const linhaRef = (y, cor) => ({ x: [g.dates[0], g.dates.at(-1)], y: [y, y], yaxis: 'y3', mode: 'lines',
                                    line: { color: cor, width: 1, dash: 'dot' }, hoverinfo: 'skip', showlegend: false });
    const series = [
        { type: 'candlestick', name: 'Preço', x: g.dates, open: g.open, high: g.high, low: g.low, close: g.close,
          increasing: { line: { color: '#22c55e' } }, decreasing: { line: { color: '#f05252' } } },
        { name: semanal ? 'Média 10 sem.' : 'Média 50', x: g.dates, y: g.ma50, mode: 'lines', line: { color: '#f59e0b', width: 1.5 } },
        { name: semanal ? 'Média 40 sem.' : 'Média 200', x: g.dates, y: g.ma200, mode: 'lines', line: { color: '#60a5fa', width: 1.5 } },
        { type: 'bar', name: 'Volume', x: g.dates, y: g.volume, yaxis: 'y2', showlegend: false,
          marker: { color: subiu.map(s => s ? 'rgba(34,197,94,.25)' : 'rgba(240,82,82,.25)') } },
        { name: 'IFR (14)', x: g.dates, y: g.rsi, yaxis: 'y3', mode: 'lines', line: { color: '#a78bfa', width: 1.5 } },
        linhaRef(70, '#f05252'), linhaRef(30, '#22c55e'),
    ];
    const eixo = { gridcolor: '#1e2a42', zerolinecolor: '#1e2a42' };
    const layout = {
        height: 600, margin: { l: 56, r: 16, t: 8, b: 32 }, separators: ',.',
        paper_bgcolor: '#111a2b', plot_bgcolor: '#111a2b', font: { color: '#94a3b8', family: 'Inter' },
        hovermode: 'x unified', legend: { orientation: 'h', y: 1.06 },
        xaxis: { ...eixo, type: 'category', nticks: 8, rangeslider: { visible: false } },
        yaxis: { ...eixo, domain: [0.32, 1], tickprefix: 'R$ ' },
        // Volume sobreposto ao preço, ocupando só os 20% inferiores (escala 5x o maior volume)
        yaxis2: { overlaying: 'y', range: [0, Math.max(...g.volume) * 5 || 1], showgrid: false, showticklabels: false },
        yaxis3: { ...eixo, domain: [0, 0.24], range: [0, 100], tickvals: [30, 70], title: 'IFR' },
    };
    $('chart-container').innerHTML = '';
    Plotly.newPlot('chart-container', series, layout, { displaylogo: false, responsive: true });
}

async function carregarAnalise() {
    const ticker = $('input-ticker').value.toUpperCase().trim().replace('.SA', '');
    const periodo = $('select-periodo').value;
    if (!ticker) return;

    $('chart-section').hidden = false;
    $('chart-container').innerHTML = '<div class="mensagem"><div class="carregador"></div></div>';
    if (!$('ficha-container').querySelector('.ficha-topo')) {
        $('ficha-container').innerHTML = '<div class="cartao mensagem"><div class="carregador"></div><p>Carregando indicadores...</p></div>';
    }

    try {
        const d = await (await fetch(`${API}/api/analise?ticker=${encodeURIComponent(ticker)}&periodo=${encodeURIComponent(periodo)}`)).json();
        if (d.error) {
            $('ficha-container').innerHTML = `<div class="cartao vazio"><h3>${escapar(d.error)}</h3><p>Confira o código digitado.</p></div>`;
            $('chart-section').hidden = true;
            return;
        }
        $('ficha-container').innerHTML = montarFicha(d);
        if (d.chart_data?.dates.length) desenharGrafico(d.chart_data, periodo);
        else $('chart-container').innerHTML = '<div class="mensagem">Histórico de preços indisponível para esta ação.</div>';
    } catch {
        $('ficha-container').innerHTML = '<div class="cartao vazio"><h3>Não foi possível carregar a ação</h3><p>Tente novamente em alguns segundos.</p></div>';
    }
    icones();
}


/* ------------------------------ INICIALIZAÇÃO ------------------------------ */
document.addEventListener('DOMContentLoaded', () => {
    // Abre com a última busca (filtros e resultados) e atualiza em segundo plano
    const cache = lerCache();
    preencherFiltros(cache?.filtros || FILTROS_PADRAO);
    $('data_ref').value = cache?.filtros?.data || '';
    if ($('data_ref').value) $('data_ref').closest('details').open = true;
    marcarEstrategia(cache?.filtros?.metodo || 'graham');
    carregarDados(true);
    configurarDataReferencia();

    atualizarRelogio();
    setInterval(atualizarRelogio, 1000);
    atualizarMercado();
    setInterval(atualizarMercado, 30000);
    carregarFeriados().then(atualizarMercado);
    icones();
});
