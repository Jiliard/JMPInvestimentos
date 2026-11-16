/* =============================================================================
   JMP INVESTIMENTOS - FRONT-END
   1. Configuração   2. Status da B3   3. Ranking, filtros e colunas   4. Análise detalhada
   ============================================================================= */

/* ----------------------------- 1. CONFIGURAÇÃO ----------------------------- */

// Na Vercel (ou abrindo o arquivo local) usa a API de produção; servido pelo Flask, usa a própria origem
const API_PRODUCAO = 'https://jmpinvestimentos.onrender.com';
const API = (location.origin === API_PRODUCAO || location.port === '5000') ? '' : API_PRODUCAO;

// Filtros padrão (Quadro 4 da monografia)
const FILTROS_PADRAO = { liq_min: 100000, pl_max: 20, pvp_max: 2, divida_max: 1.5, roe_min: 10,
                         roic_min: 10, margem_min: 5, cagr_min: 0, dy_min: 0 };
const ITENS_POR_PAGINA = 25;
const CHAVE_CACHE = 'jmp:buscas:v3';        // últimas respostas por método/filtros, para exibir na hora
const METODO_PADRAO = 'graham';
const CHAVE_PREFERENCIAS = 'jmp:preferencias:v1';

let dados = [];
let pagina = 1;
let feriados = {};          // data (AAAA-MM-DD) -> nome do feriado, vindo da BrasilAPI
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
const cor = v => !ehNumero(v) ? '' : v < 0 ? 'baixa' : 'alta';   // sem dado: sem cor

// Preferências da tela (colunas visíveis) guardadas no navegador
function lerArmazenado(chave) { try { return JSON.parse(localStorage.getItem(chave)); } catch { return null; } }
function gravarArmazenado(chave, valor) { try { localStorage.setItem(chave, JSON.stringify(valor)); } catch { /* sem armazenamento */ } }
const preferencias = Object.assign({ colunas: null }, lerArmazenado(CHAVE_PREFERENCIAS));
const salvarPreferencias = () => gravarArmazenado(CHAVE_PREFERENCIAS, preferencias);

// Indicadores que podem ser exibidos ou ocultados pelo botão "Colunas"
const coluna = (id, titulo, valor, dica, padrao = true) => ({ id, titulo, valor, dica, padrao });
const COLUNAS_OPCIONAIS = [
    coluna('preco', 'Cotação', i => reais(i.preco), 'Último preço negociado (atraso de até 15 min)'),
    coluna('pl', 'P/L', i => num(i.pl, 1), 'Preço ÷ lucro por ação dos últimos 12 meses. Menor = mais barata'),
    coluna('pvp', 'P/VP', i => num(i.pvp, 2), 'Preço ÷ valor patrimonial por ação. Abaixo de 1, custa menos que o patrimônio'),
    coluna('roic', 'ROIC', i => pct(i.roic), 'Lucro operacional ÷ capital investido. Maior = negócio mais eficiente'),
    coluna('roe', 'ROE', i => pct(i.roe), 'Lucro ÷ patrimônio dos acionistas. Maior = melhor'),
    coluna('dy', 'DY', i => pct(i.dy), 'Dividendos dos últimos 12 meses sobre o preço'),
    coluna('cresc', 'Cresc. 5a', i => pct(i.crescimento), 'Crescimento anual do lucro por ação em 5 anos'),
    coluna('evebit', 'EV/EBIT', i => num(i.evebit, 2), 'Valor da empresa ÷ lucro operacional. Menor = mais barata', false),
    coluna('margem', 'Margem', i => pct(i.margem), 'Parcela da receita que vira lucro', false),
    coluna('divida', 'Dív./PL', i => num(i.divida_patrimonio, 2), 'Dívida bruta ÷ patrimônio líquido. Menor = menos endividada', false),
    coluna('liquidez', 'Liquidez', i => grande(i.liquidez), 'Volume médio negociado por dia (30 pregões)', false),
];
if (!preferencias.colunas) preferencias.colunas = COLUNAS_OPCIONAIS.filter(c => c.padrao).map(c => c.id);

// Cada método: texto do balão (fórmula + 3 linhas curtas) e as duas colunas que mostram o resultado
const resultado = (titulo, valor, dica, classe = () => '') => ({ titulo, valor, dica, classe });
const METODOS = {
    graham: {
        balao: { formula: 'Preço Justo = √(22,5 × LPA médio de 3 anos × VPA)', busca: 'ações abaixo do valor intrínseco.',
                 regra: 'lucro médio de 3 anos e patrimônio positivos.', ordem: 'maior margem de segurança.' },
        colunas: [resultado('Valor Justo Graham', i => reais(i.valor_justo), 'Valor intrínseco de Graham'),
                  resultado('Margem de Segurança', i => pct(i.potencial), 'Quanto o preço justo está acima da cotação', i => cor(i.potencial))],
    },
    bazin: {
        balao: { formula: 'Preço Teto = dividendos de 12 meses ÷ 6%', busca: 'renda com dividendos.',
                 regra: 'Dividend Yield de pelo menos 6%.', ordem: 'maior desconto em relação ao teto.' },
        colunas: [resultado('Preço Teto Bazin', i => reais(i.preco_teto), 'Preço máximo para render 6% ao ano'),
                  resultado('Desconto ao Teto', i => pct(i.potencial), 'Quanto o preço teto está acima da cotação', i => cor(i.potencial))],
    },
    greenblatt: {
        balao: { formula: 'Pontuação = posição em ROIC + posição em EV/EBIT', busca: 'empresas boas e baratas.',
                 regra: 'exclui bancos, seguradoras e utilidade pública.', ordem: 'menor pontuação.' },
        colunas: [resultado('Pontuação', i => num(i.potencial, 0) + ' pts', 'Soma das duas posições'),
                  resultado('Posições ROIC + EV/EBIT', i => `${num(i.pos_roic, 0)}º + ${num(i.pos_evebit, 0)}º`, 'Posição no ranking de ROIC + posição no de EV/EBIT')],
    },
    lynch: {
        balao: { formula: 'PEG = P/L ÷ crescimento anual do lucro (%)', busca: 'crescimento a preço razoável.',
                 regra: 'lucro e crescimento positivos.', ordem: 'menor PEG (abaixo de 1 é barato).' },
        colunas: [resultado('PEG Ratio', i => num(i.peg_ratio), 'P/L dividido pelo crescimento'),
                  resultado('Crescimento (CAGR)', i => pct(i.crescimento) + ' a.a.', 'Crescimento anual do lucro por ação em 5 anos', i => cor(i.crescimento))],
    },
};

// Explicação de cada indicador (ícone "?" na tela de análise)
const EXPLICA = {
    pl: 'Preço da ação ÷ lucro por ação dos últimos 12 meses. Mostra quantos anos de lucro equivalem ao preço. Quanto menor, mais barata.',
    pvp: 'Preço da ação ÷ patrimônio por ação. Abaixo de 1, a ação custa menos que o patrimônio da empresa. Quanto menor, mais barata.',
    evebit: 'Valor da empresa (ações + dívidas − caixa) ÷ lucro operacional. Quanto menor, mais barata. Usado por Greenblatt.',
    lpa: 'Lucro dos últimos 12 meses dividido pelo número de ações: quanto do lucro cabe a cada ação.',
    lpa_medio_3a: 'Média do lucro por ação dos três últimos exercícios anuais. É o lucro usado por Graham no cálculo do preço justo.',
    vpa: 'Patrimônio dividido pelo número de ações: quanto do patrimônio cabe a cada ação.',
    roe: 'Lucro ÷ patrimônio: quanto a empresa lucra com o dinheiro dos sócios. Quanto maior, melhor.',
    roic: 'Lucro operacional ÷ capital investido (dos sócios e de dívidas): mede a eficiência do negócio. Quanto maior, melhor.',
    margem: 'De cada R$ 100 que a empresa vende, quanto vira lucro. Quanto maior, melhor.',
    crescimento: 'Quanto o lucro por ação cresceu, em média, por ano nos últimos 5 anos.',
    dy: 'Dividendos pagos nos últimos 12 meses ÷ preço atual: o rendimento em dinheiro que a ação pagou.',
    patrimonio: 'O que a empresa tem (bens e direitos) menos o que deve: o valor que pertence aos sócios.',
    divida_total: 'Total de empréstimos e financiamentos, de curto e de longo prazo.',
    divida_patrimonio: 'Dívida ÷ patrimônio. Acima de 1, a empresa deve mais do que o patrimônio. Quanto menor, mais segura.',
    liquidez: 'Valor médio negociado por dia nos últimos 30 pregões. Com liquidez baixa, é difícil comprar e vender a ação.',
    dpa_12m: 'Dividendos pagos por ação nos últimos 12 meses, em reais.',
};
const info = texto => `<button type="button" class="info" data-tip="${escapar(texto)}" aria-label="Ajuda">?</button>`;

/* Balão de ajuda: um só elemento para todos os itens com data-tip, posicionado a cada uso para
   ficar inteiro na tela e sem cobrir o item explicado. Abre com o mouse, o teclado (Tab) e o toque. */
let balao;
const esconderBalao = () => { if (balao) balao.hidden = true; };

function mostrarBalao(alvo) {
    if (!alvo.dataset.tip) return esconderBalao();
    balao.textContent = alvo.dataset.tip;
    Object.assign(balao.style, { left: '0px', top: '0px' });
    balao.hidden = false;
    const item = alvo.getBoundingClientRect();
    const caixa = balao.getBoundingClientRect();
    const margem = 8;
    const cabeAbaixo = item.bottom + margem + caixa.height <= window.innerHeight;
    const topo = cabeAbaixo ? item.bottom + margem : Math.max(margem, item.top - margem - caixa.height);
    const esquerda = Math.min(Math.max(margem, item.left + item.width / 2 - caixa.width / 2), window.innerWidth - caixa.width - margem);
    Object.assign(balao.style, { left: `${esquerda}px`, top: `${topo}px` });
}

function iniciarBaloes() {
    balao = Object.assign(document.createElement('div'), { id: 'balao', hidden: true });
    balao.setAttribute('role', 'tooltip');
    document.body.append(balao);
    const alvoDe = evento => evento.target.closest?.('[data-tip]');
    document.addEventListener('mouseover', e => { const alvo = alvoDe(e); if (alvo) mostrarBalao(alvo); });
    document.addEventListener('mouseout', e => { const alvo = alvoDe(e); if (alvo && !alvo.contains(e.relatedTarget)) esconderBalao(); });
    document.addEventListener('focusin', e => { const alvo = alvoDe(e); alvo ? mostrarBalao(alvo) : esconderBalao(); });
    document.addEventListener('focusout', esconderBalao);
    document.addEventListener('click', e => { const alvo = alvoDe(e); alvo ? mostrarBalao(alvo) : esconderBalao(); });  // toque no celular
    window.addEventListener('scroll', esconderBalao, true);
}

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
    `<img src="${escapar(url)}" alt="" loading="lazy" onerror="this.onerror=null;this.src='https://ui-avatars.com/api/?name=${escapar(ticker).slice(0, 2)}&background=1e293f&color=16c784&bold=true'">`;


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
    $('relogio').textContent = `${agora.toLocaleTimeString('pt-BR', opcoes)} - ${agora.toLocaleDateString('pt-BR', opcoes)}`;
}

function atualizarMercado() {
    const { data, semana, minutos } = horaB3();
    // A B3 segue o horário de verão dos EUA: pregão até 17h com Nova York em horário de verão, senão até 18h
    const nyVerao = new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', timeZoneName: 'short' })
        .format(new Date()).includes('EDT');
    const fechamento = nyVerao ? 17 : 18;
    const horario = `O pregão regular da B3 vai das 10h às ${fechamento}h (horário de Brasília), de segunda a sexta-feira.`;
    const feriado = feriados[data] || { '12-24': 'Véspera de Natal', '12-31': 'Último dia do ano' }[data.slice(5)];
    let texto = 'B3 Fechada';
    let motivo = `Fora do horário de negociação. ${horario}`;
    let aberto = false;

    if (['Sat', 'Sun'].includes(semana)) {
        motivo = `Fim de semana: não há pregão aos sábados e domingos. ${horario}`;
    } else if (feriado) {
        motivo = `Feriado (${feriado}): a B3 não abre neste dia.`;
    } else if (minutos >= 600 && minutos < fechamento * 60) {
        aberto = true;
        texto = 'B3 Aberta';
        motivo = `Pregão em andamento até as ${fechamento}h. Os indicadores têm atraso de até 15 minutos.`;
    } else if (minutos >= 585 && minutos < 600) {
        texto = 'B3 Pré-abertura';
        motivo = 'Pré-abertura: as ofertas já são registradas, mas os negócios começam às 10h.';
    }

    $('status-mercado').className = `mercado com-balao ${aberto ? 'aberto' : 'fechado'}`;
    $('status-mercado').dataset.tip = motivo;
    $('texto-mercado').textContent = texto;
}

async function carregarFeriados() {
    try {
        const resposta = await fetch(`https://brasilapi.com.br/api/feriados/v1/${horaB3().data.slice(0, 4)}`);
        if (resposta.ok) feriados = Object.fromEntries((await resposta.json()).map(f => [f.date, f.name]));
    } catch { /* sem feriados: considera apenas fins de semana */ }
}


/* -------------------- 3. RANKING, FILTROS E COLUNAS ------------------------ */
function mudarAba(aba) {
    document.querySelectorAll('.botao-aba').forEach(b => b.classList.toggle('ativa', b.dataset.aba === aba));
    $('aba-ranking').classList.toggle('ativa', aba === 'ranking');
    $('aba-detalhes').classList.toggle('ativa', aba === 'detalhes');
    if (aba === 'detalhes' && !$('lista-tickers').children.length) carregarTickers();
}

function marcarEstrategia(metodo) {
    document.querySelectorAll('.estrategia').forEach(b => {
        b.classList.toggle('ativa', b.dataset.metodo === metodo);
        b.setAttribute('aria-checked', b.dataset.metodo === metodo);
    });
    $('metodo').value = metodo;
    montarCabecalho();
}

// Coloca em cada botão de estratégia o balão com a explicação do método (aparece ao passar o mouse)
function montarExplicacoesMetodos() {
    document.querySelectorAll('.estrategia').forEach(botao => {
        const b = METODOS[botao.dataset.metodo].balao;
        botao.insertAdjacentHTML('beforeend', `<span class="explicacao-metodo" role="tooltip"><b>${b.formula}</b>
            <span><em>Busca:</em> ${b.busca}</span><span><em>Regra:</em> ${b.regra}</span><span><em>Ranking:</em> ${b.ordem}</span></span>`);
    });
}

function selecionarEstrategia(metodo) {
    marcarEstrategia(metodo);
    carregarDados();
}

// Colunas visíveis: as duas do método (sempre) + as opcionais marcadas no menu "Colunas"
const colunasVisiveis = () => COLUNAS_OPCIONAIS.filter(c => preferencias.colunas.includes(c.id));

function montarCabecalho() {
    const th = (c, classe = '') => `<th class="${classe} com-balao" data-tip="${escapar(c.dica)}" tabindex="0">${c.titulo}</th>`;
    const [preco, ...outras] = [colunasVisiveis().find(c => c.id === 'preco'), ...colunasVisiveis().filter(c => c.id !== 'preco')];
    $('cabecalho-tabela').innerHTML = '<th>#</th><th class="esq">Empresa</th>' + (preco ? th(preco) : '') +
        METODOS[$('metodo').value].colunas.map(c => th(c, 'coluna-metodo')).join('') + outras.map(c => th(c)).join('');
}

function montarMenuColunas() {
    $('lista-colunas').innerHTML = '<p>Exibir colunas</p>' + COLUNAS_OPCIONAIS.map(c => `
        <label><input type="checkbox" value="${c.id}" ${preferencias.colunas.includes(c.id) ? 'checked' : ''}
                      onchange="alternarColuna(this)"> ${c.titulo}</label>`).join('') +
        '<button class="link" onclick="restaurarColunas()"><i data-lucide="rotate-ccw"></i> Colunas padrão</button>';
    icones();
}

function alternarMenuColunas(evento) {
    evento.stopPropagation();
    const aberto = $('lista-colunas').hidden;
    $('lista-colunas').hidden = !aberto;
    $('botao-colunas').setAttribute('aria-expanded', aberto);
}

function alternarColuna(caixa) {
    preferencias.colunas = COLUNAS_OPCIONAIS.map(c => c.id)
        .filter(id => id === caixa.value ? caixa.checked : preferencias.colunas.includes(id));
    salvarPreferencias();
    montarCabecalho();
    renderizarTabela();
}

function restaurarColunas() {
    preferencias.colunas = COLUNAS_OPCIONAIS.filter(c => c.padrao).map(c => c.id);
    salvarPreferencias();
    montarMenuColunas();
    montarCabecalho();
    renderizarTabela();
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
        : `Última atualização: ${data.toLocaleDateString('pt-BR')} às ${data.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })}`;
}

function mensagemTabela(titulo, texto = '', carregando = false) {
    const colunas = colunasVisiveis().length + 4;
    $('tabela-resultados').tBodies[0].innerHTML = `<tr><td colspan="${colunas}" class="mensagem">
        ${carregando ? '<div class="carregador"></div>' : ''}<h3>${escapar(titulo)}</h3><p>${escapar(texto)}</p></td></tr>`;
}

// Cache das últimas buscas no navegador: { filtros: últimos filtros usados, buscas: { chave: resposta } }
const lerCache = () => Object.assign({ filtros: null, buscas: {} }, lerArmazenado(CHAVE_CACHE));
const chaveBusca = filtros => JSON.stringify(filtros);

function guardarBusca(filtros, dados, dataBase) {
    const cache = lerCache();
    cache.filtros = filtros;
    cache.buscas[chaveBusca(filtros)] = { dados, dataBase, quando: Date.now() };
    // Mantém só as 30 buscas mais recentes
    const chaves = Object.keys(cache.buscas).sort((a, b) => cache.buscas[b].quando - cache.buscas[a].quando);
    chaves.slice(30).forEach(c => delete cache.buscas[c]);
    gravarArmazenado(CHAVE_CACHE, cache);
}

/**
 * Consulta o ranking com resposta imediata: se esta mesma busca (método + filtros) já
 * foi feita, mostra na hora o último resultado e atualiza por baixo dos panos. Na
 * primeira visita, o servidor responde com a última coleta gravada no banco.
 * forcar = true (ícone de atualizar) pede ao servidor uma coleta nova antes de responder.
 */
async function carregarDados(forcar = false) {
    atualizarSeloFiltros();
    const filtros = lerTela();
    const anterior = lerCache().buscas[chaveBusca(filtros)];
    const id = ++ultimaRequisicao;
    $('botao-atualizar').classList.add('girando');

    let avisoLento;
    if (anterior) {
        exibir(anterior.dados);
        status(`${descreverData(anterior.dataBase, !!filtros.data)} · atualizando...`, 'carregando');
    } else {
        mensagemTabela('Consultando...', '', true);
        status('Consultando...', 'carregando');
        // O servidor gratuito pode estar reiniciando: explica a espera em vez de parecer travado
        avisoLento = setTimeout(() => id === ultimaRequisicao &&
            mensagemTabela('O servidor está iniciando', 'A primeira consulta pode levar até 1 minuto. As próximas são imediatas.', true), 4000);
    }

    try {
        const parametros = forcar && !filtros.data ? { ...filtros, atualizar: '1' } : filtros;
        const resposta = await fetch(`${API}/api/rankings?${new URLSearchParams(parametros)}`);
        const corpo = await resposta.json().catch(() => null);
        if (id !== ultimaRequisicao) return;          // o usuário já mudou o filtro
        if (!resposta.ok || !Array.isArray(corpo)) throw new Error(corpo?.erro || `Erro ${resposta.status} no servidor.`);

        const dataBase = resposta.headers.get('X-Data-Base') || '';   // com data de referência: o pregão usado
        exibir(corpo);
        status(descreverData(dataBase, !!filtros.data));
        guardarBusca(filtros, corpo, dataBase);
    } catch (erro) {
        if (id !== ultimaRequisicao) return;
        const semConexao = erro instanceof TypeError;   // falha de rede (ex.: servidor acordando)
        const texto = semConexao ? 'Servidor iniciando. Tente novamente em alguns segundos.' : erro.message;
        // Sem conexão, mantém a última busca na tela; erro do servidor (ex.: data sem histórico) é mostrado na tabela
        if (semConexao && anterior) return status(`${texto} Exibindo a última busca.`, 'erro');
        exibir([], false);
        status(texto, 'erro');
        mensagemTabela(filtros.data ? 'Sem dados para a data de referência' : 'Não foi possível carregar o ranking', texto);
    } finally {
        clearTimeout(avisoLento);
        if (id === ultimaRequisicao) $('botao-atualizar').classList.remove('girando');
    }
}

function exibir(lista, renderizar = true) {
    dados = lista;
    pagina = 1;
    $('contagem-acoes').textContent = dados.length;
    if (renderizar) renderizarTabela();
}

function renderizarTabela() {
    if (!dados.length) {
        mensagemTabela('Nenhuma ação atende aos filtros', 'Afrouxe algum filtro ou use "Restaurar padrão".');
        $('info-paginacao').textContent = '0 ações';
        $('pagina-atual').textContent = '1';
        return;
    }
    const doMetodo = METODOS[$('metodo').value].colunas;
    const visiveis = colunasVisiveis();
    const preco = visiveis.find(c => c.id === 'preco');
    const outras = visiveis.filter(c => c.id !== 'preco');
    const inicio = (pagina - 1) * ITENS_POR_PAGINA;
    const fim = Math.min(inicio + ITENS_POR_PAGINA, dados.length);

    $('tabela-resultados').tBodies[0].innerHTML = dados.slice(inicio, fim).map(item => {
        // Cada célula é um link para a análise: o clique abre em nova aba e o botão direito
        // oferece "Abrir em nova aba/janela". Só a célula da empresa entra na navegação por teclado.
        const url = linkAnalise(item.ticker);
        const celula = (conteudo, classe = '', foco = false) => `<td class="${classe}"><a class="celula-link" href="${url}"
            target="_blank" rel="noopener" ${foco ? '' : 'tabindex="-1"'}>${conteudo}</a></td>`;
        return `<tr title="Abrir a análise de ${escapar(item.ticker)} em nova aba">
            ${celula(`<span class="posicao">${item.rank}º</span>`)}
            ${celula(`<div class="empresa">${logoOuIniciais(item.logo, item.ticker)}
                <div><strong>${escapar(item.ticker)}</strong><small>${escapar(item.nome)}</small></div></div>`, 'esq', true)}
            ${preco ? celula(preco.valor(item)) : ''}
            ${doMetodo.map((c, i) => celula(c.valor(item), `coluna-metodo ${i === 0 ? 'destaque' : ''} ${c.classe(item)}`)).join('')}
            ${outras.map(c => celula(c.valor(item))).join('')}
        </tr>`;
    }).join('');

    $('info-paginacao').textContent = `${inicio + 1}–${fim} de ${dados.length} ações`;
    $('pagina-atual').textContent = `${pagina} de ${Math.ceil(dados.length / ITENS_POR_PAGINA)}`;
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


/* --------------------------- 4. ANÁLISE DETALHADA -------------------------- */
async function carregarTickers() {
    try {
        const tickers = await (await fetch(`${API}/api/tickers`)).json();
        $('lista-tickers').innerHTML = tickers.map(t => `<option value="${escapar(t)}">`).join('');
    } catch { /* a busca continua funcionando digitando o código */ }
}

// Endereço da análise de uma ação (abre direto nela: index.html?acao=PETR4)
const linkAnalise = ticker => `?acao=${encodeURIComponent(ticker)}`;

function abrirAnalise(ticker) {
    $('campo-ticker').value = ticker;
    mudarAba('detalhes');
    carregarAnalise();
    window.scrollTo({ top: 0, behavior: 'smooth' });
}

function selecionarPeriodo(periodo) {
    document.querySelectorAll('.periodo').forEach(b => b.classList.toggle('ativa', b.dataset.periodo === periodo));
    $('periodo-grafico').value = periodo;
    carregarAnalise();
}

// Variação do último pregão, a partir dos dois últimos fechamentos diários do gráfico
function variacaoDoDia(grafico, periodo) {
    if (!grafico || ['5 Anos', '10 Anos'].includes(periodo) || grafico.close.length < 2) return '';
    const [anterior, atual] = grafico.close.slice(-2);
    const variacao = atual / anterior - 1;
    const sinal = variacao > 0 ? '+' : '';
    return `<span class="variacao ${cor(variacao)}">${sinal}${pct(variacao, 2)} no último pregão</span>`;
}

function montarCabecalhoAcao(d, periodo) {
    const f = d.fundamentos;
    return `
    <div class="cartao ficha">
        <div class="ficha-topo">
            <div class="empresa">${logoOuIniciais(d.logo, d.ticker)}
                <div><h2>${escapar(d.nome)}</h2>
                    <div class="etiquetas"><span class="etiqueta">${escapar(d.ticker)}</span>
                    <span class="etiqueta">${escapar(SETORES[d.setor] || d.setor || 'Setor não informado')}</span></div></div></div>
            <div class="preco-atual"><span class="dica">Cotação</span><strong>${reais(f.preco)}</strong>${variacaoDoDia(d.grafico, periodo)}</div>
        </div>
        <div class="links">
            <a class="botao-secundario" href="${escapar(f.links.site_ri)}" target="_blank" rel="noopener"><i data-lucide="building-2"></i> Relações com Investidores</a>
            <a class="botao-secundario" href="${escapar(f.links.tradingview)}" target="_blank" rel="noopener"><i data-lucide="external-link"></i> Ver no TradingView</a>
        </div>
    </div>`;
}

// Valor justo de Graham e preço teto de Bazin (mesmas fórmulas do back-end); null quando não se aplicam
const valorJusto = f => f.lpa_medio_3a > 0 && f.vpa > 0 ? Math.sqrt(22.5 * f.lpa_medio_3a * f.vpa) : null;
const precoTeto = f => f.dpa_12m > 0 ? f.dpa_12m / 0.06 : null;

// O que cada estratégia conclui sobre a ação (mesmas fórmulas do back-end, aplicadas aos dados atuais)
function montarLeituraMetodos(d) {
    const f = d.fundamentos;
    const mercado = d.comparativo?.indicadores || {};
    const selo = (texto, tipo = '') => `<span class="selo-metodo ${tipo}">${texto}</span>`;
    const caixa = (titulo, ajuda, seloHtml, valor, rotulo, texto) => `<div class="metodo-acao">
        <h4><span>${titulo} ${info(ajuda)}</span>${seloHtml}</h4>
        <span class="valor-grande">${valor}</span><span class="rotulo-valor">${rotulo}</span><p>${texto}</p></div>`;
    const fora = (titulo, ajuda, motivo) => caixa(titulo, ajuda, selo('Fora do método'), 'N/A', 'não se aplica', motivo);

    const justo = valorJusto(f);
    const margem = justo ? justo / f.preco - 1 : null;
    const teto = precoTeto(f);
    const peg = f.pl > 0 && f.crescimento > 0 ? f.pl / (f.crescimento * 100) : null;
    const boaRoic = f.roic > (mercado.roic?.mercado ?? Infinity);
    const barataEv = f.evebit > 0 && f.evebit < (mercado.evebit?.mercado ?? -Infinity);

    const ajudaGraham = 'Preço justo = √(22,5 × LPA médio dos 3 últimos anos × VPA). Se a cotação está abaixo dele, a margem de segurança é positiva.';
    const graham = justo
        ? caixa('Graham', ajudaGraham, margem > 0 ? selo('Abaixo do preço justo', 'positivo') : selo('Acima do preço justo', 'negativo'),
                reais(justo), 'preço justo',
                `A cotação (${reais(f.preco)}) está ${margem > 0 ? 'abaixo' : 'acima'} do preço justo: margem de segurança de <b class="${cor(margem)}">${pct(margem)}</b>.`)
        : fora('Graham', ajudaGraham, ehNumero(f.lpa_medio_3a) ? 'O método exige lucro médio de 3 anos e patrimônio positivos.' : 'O lucro dos três últimos anos não foi informado pela fonte.');

    const ajudaBazin = 'Preço teto = dividendos dos últimos 12 meses ÷ 6%: o maior preço que ainda rende 6% ao ano em dividendos.';
    const bazin = teto
        ? caixa('Bazin', ajudaBazin, f.dy >= 0.06 ? selo('DY ≥ 6%', 'positivo') : selo('DY abaixo de 6%', 'negativo'),
                reais(teto), 'preço teto',
                f.dy >= 0.06
                    ? `Os dividendos de 12 meses renderam ${pct(f.dy)}. O preço teto está <b class="alta">${pct(teto / f.preco - 1)}</b> acima da cotação.`
                    : `Os dividendos de 12 meses renderam ${pct(f.dy)}, abaixo dos 6% exigidos: a ação fica fora do ranking de Bazin.`)
        : fora('Bazin', ajudaBazin, 'Não pagou dividendos nos últimos 12 meses.');

    const ajudaGreenblatt = 'Ordena as empresas por ROIC (rentabilidade: maior é melhor) e por EV/EBIT (preço: menor é melhor) e soma as duas posições.';
    const greenblatt = d.excluida_greenblatt
        ? fora('Greenblatt', ajudaGreenblatt, 'O método exclui bancos, seguradoras e empresas de utilidade pública.')
        : !(f.roic > 0 && f.evebit > 0)
            ? fora('Greenblatt', ajudaGreenblatt, 'ROIC ou EV/EBIT não informado ou não positivo.')
            : caixa('Greenblatt', ajudaGreenblatt,
                    boaRoic && barataEv ? selo('Boa e barata', 'positivo')
                        : selo(boaRoic || barataEv ? 'Atende em parte' : 'Não atende', boaRoic || barataEv ? '' : 'negativo'),
                    `${pct(f.roic)} <small>ROIC</small> · ${num(f.evebit, 1, 'x')} <small>EV/EBIT</small>`, 'rentabilidade e preço',
                    `Rentabilidade ${boaRoic ? 'acima' : 'abaixo'} e preço ${barataEv ? 'abaixo' : 'acima'} da mediana da B3. A posição no ranking aparece na tela de ranking.`);

    const ajudaLynch = 'PEG = P/L ÷ crescimento anual do lucro (em %). Abaixo de 1, o preço é baixo para o ritmo de crescimento.';
    const lynch = peg
        ? caixa('Lynch', ajudaLynch, peg < 1 ? selo('PEG < 1', 'positivo') : peg <= 2 ? selo('PEG entre 1 e 2') : selo('PEG > 2', 'negativo'),
                num(peg), 'PEG',
                `P/L de ${num(f.pl, 1, 'x')} para um lucro que cresceu ${pct(f.crescimento)} ao ano: preço ${peg < 1 ? 'baixo' : peg <= 2 ? 'razoável' : 'alto'} para esse crescimento.`)
        : fora('Lynch', ajudaLynch, ehNumero(f.crescimento) ? 'O método exige lucro e crescimento positivos.' : 'O crescimento do lucro não foi informado pela fonte.');

    return `<div class="cartao">
        <div class="cabecalho-cartao"><h3 class="titulo">O que cada método diz sobre a ação</h3>
            <span class="dica">Fórmulas das quatro estratégias aplicadas aos dados atuais, sem os filtros da tela de ranking.</span></div>
        <div class="metodos-acao">${graham}${bazin}${greenblatt}${lynch}</div>
        ${justo || teto ? `<div id="grafico-metodos"></div>
        <p class="dica">Cotação atual comparada aos preços calculados por Graham e Bazin. Barra verde: o preço calculado está acima da cotação.</p>` : ''}
    </div>`;
}

// Gráfico: cotação x valor justo de Graham x preço teto de Bazin
function desenharGraficoMetodos(f) {
    const barras = [['Cotação atual', f.preco], ['Valor justo (Graham)', valorJusto(f)], ['Preço teto (Bazin)', precoTeto(f)]]
        .filter(b => ehNumero(b[1]));
    if (!$('grafico-metodos') || barras.length < 2) return;
    Plotly.newPlot('grafico-metodos', [{
        type: 'bar', orientation: 'h', y: barras.map(b => b[0]), x: barras.map(b => b[1]),
        text: barras.map(b => reais(b[1])), textposition: 'outside', cliponaxis: false, hoverinfo: 'skip',
        marker: { color: barras.map((b, i) => i === 0 ? '#5b6b88' : b[1] >= f.preco ? '#16c784' : '#ea3943') },
    }], {
        height: 150, margin: { l: 140, r: 70, t: 4, b: 24 }, separators: ',.',
        paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)', font: { color: '#8a96ab', family: 'Inter' },
        xaxis: { gridcolor: '#222c40', tickprefix: 'R$ ' }, yaxis: { autorange: 'reversed' }, bargap: 0.35,
    }, { displayModeBar: false, responsive: true });
}

// Leitura da comparação com o setor, por indicador: [melhor, pior]
const LEITURA = {
    pl: ['mais barata', 'mais cara'], pvp: ['mais barata', 'mais cara'], evebit: ['mais barata', 'mais cara'],
    divida_patrimonio: ['menos endividada', 'mais endividada'], dy: ['paga mais dividendos', 'paga menos dividendos'],
};

// Todos os indicadores da empresa, por grupo, com a mediana do setor e da B3 quando há comparação
function montarIndicadores(d) {
    const f = d.fundamentos;
    const c = d.comparativo || { indicadores: {} };
    const vezes = casas => v => num(v, casas, 'x');
    // [chave, rótulo, formato, menor é melhor? (ausente = indicador sem comparação)]
    const grupos = [
        ['Preço', [['pl', 'Preço sobre lucro (P/L)', vezes(1), true], ['pvp', 'Preço sobre patrimônio (P/VP)', vezes(2), true],
                   ['evebit', 'EV/EBIT', vezes(1), true], ['lpa', 'Lucro por ação (LPA, 12 meses)', reais],
                   ['lpa_medio_3a', 'LPA médio (3 últimos anos)', reais],
                   ['vpa', 'Valor patrimonial por ação (VPA)', reais]]],
        ['Rentabilidade e crescimento', [['roe', 'ROE', pct, false], ['roic', 'ROIC', pct, false], ['margem', 'Margem líquida', pct, false],
                   ['crescimento', 'Crescimento do lucro (5 anos)', v => ehNumero(v) ? pct(v) + ' a.a.' : 'N/D']]],
        ['Dividendos', [['dy', 'Dividend Yield (12 meses)', pct, false],
                   ['dpa_12m', 'Dividendos por ação (12 meses)', v => f.dy > 0 ? reais(v) : 'Sem dividendos']]],
        ['Endividamento e liquidez', [['divida_patrimonio', 'Dívida / Patrimônio', vezes(2), true],
                   ['divida_total', 'Dívida bruta', grande], ['patrimonio', 'Patrimônio líquido', grande],
                   ['liquidez', 'Liquidez média diária', grande]]],
    ];

    const linha = ([chave, rotulo, fmt, menorMelhor]) => {
        const valor = f[chave];
        const mediana = menorMelhor === undefined ? null : c.indicadores[chave];
        let leitura = '';
        let classe = '';
        const valido = ehNumero(valor) && !(['pl', 'pvp', 'evebit'].includes(chave) && valor <= 0);
        if (mediana && valido && ehNumero(mediana.setor)) {
            const melhor = menorMelhor ? valor < mediana.setor : valor > mediana.setor;
            const [bom, ruim] = LEITURA[chave] || ['mais rentável', 'menos rentável'];
            classe = melhor ? 'alta' : 'baixa';
            leitura = `${melhor ? bom : ruim} que a mediana do setor`;
            leitura = leitura[0].toUpperCase() + leitura.slice(1);
        }
        const mediaCelula = v => mediana ? `<td>${fmt(v)}</td>` : '<td class="sem-comparacao">—</td>';
        return `<tr><td><span class="rotulo">${rotulo} ${info(EXPLICA[chave])}</span></td>
            <td class="valor ${classe}">${fmt(valor)}</td>${mediaCelula(mediana?.setor)}${mediaCelula(mediana?.mercado)}
            <td class="leitura-indicador">${leitura}</td></tr>`;
    };

    const corpo = grupos.map(([titulo, itens]) =>
        `<tr class="grupo-linha"><th colspan="5">${titulo}</th></tr>${itens.map(linha).join('')}`).join('');
    return `<div class="cartao">
        <div class="cabecalho-cartao"><h3 class="titulo">Indicadores da empresa</h3>
            <span class="dica">Comparação com a mediana de ${c.empresas_setor ?? 0} empresas do setor e de ${c.empresas_mercado ?? 0} da B3
            com liquidez mínima de R$ 100 mil/dia. Verde: melhor que o setor; vermelho: pior; “—”: sem comparação.</span></div>
        <table class="comparativo indicadores"><thead><tr><th>Indicador</th><th>${escapar(d.ticker)}</th><th>Mediana do setor</th>
            <th>Mediana da B3</th><th class="leitura-indicador">Comparação com o setor</th></tr></thead>
        <tbody>${corpo}</tbody></table>
    </div>`;
}

// Evolução dos indicadores registrados a cada pregão no banco (dados da API)
function montarEvolucao(historico) {
    const pontos = (historico || []).length;
    const desde = pontos ? historico[0].dia.split('-').reverse().join('/') : '';
    const texto = !pontos ? 'Ainda não há registros desta ação. A plataforma grava um ponto por pregão.'
        : pontos < 2 ? `Registrado desde ${desde}. O gráfico aparece a partir do segundo pregão registrado.`
        : `Um ponto por pregão, desde ${desde}. Passe o mouse sobre um dia para ver todos os valores.`;
    const graficos = pontos < 2 ? '' : `<div class="evolucao-graficos">
        <div><h4>Preço: P/L e P/VP (vezes)</h4><div id="evolucao-multiplos"></div></div>
        <div><h4>Rentabilidade e dividendos: ROE e DY (%)</h4><div id="evolucao-percentuais"></div></div></div>`;
    return `<div class="cartao">
        <div class="cabecalho-cartao"><h3 class="titulo">Evolução dos indicadores</h3><span class="dica">${texto}</span></div>
        ${graficos}
    </div>`;
}

// Dois gráficos, cada um na sua escala, para que variações pequenas fiquem visíveis.
// Com até 15 pregões, cada ponto mostra o valor; com mais, só o último, para não poluir.
function desenharEvolucao(historico) {
    if (!$('evolucao-multiplos')) return;
    const datas = historico.map(h => h.dia.split('-').reverse().slice(0, 2).join('/'));
    const comValorEmTodos = historico.length <= 15;
    const vezes = v => formato(v < 10 ? 2 : 1).format(v) + 'x';
    const percentual = v => formato(1).format(v) + '%';
    const serie = (nome, campo, cor, fmt, fator = 1) => {
        const y = historico.map(h => ehNumero(h[campo]) ? h[campo] * fator : null);
        return {
            name: nome, x: datas, y, mode: 'lines+markers+text', cliponaxis: false,
            line: { color: cor, width: 2 }, marker: { size: 6 },
            text: y.map((v, i) => v === null || (!comValorEmTodos && i < y.length - 1) ? '' : fmt(v)),
            textposition: 'top center', textfont: { size: 11, color: cor },
            customdata: y.map(v => v === null ? 'N/D' : fmt(v)), hovertemplate: `${nome}: %{customdata}<extra></extra>`,
        };
    };
    const layout = {
        height: 260, margin: { l: 44, r: 16, t: 28, b: 32 }, separators: ',.', hovermode: 'x unified', showlegend: true,
        paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)', font: { color: '#8a96ab', family: 'Inter' },
        legend: { orientation: 'h', y: 1.18, x: 0 },
        xaxis: { gridcolor: '#222c40', type: 'category' },
        yaxis: { gridcolor: '#222c40', zeroline: false, automargin: true },
    };
    const opcoes = { displayModeBar: false, responsive: true };
    Plotly.newPlot('evolucao-multiplos', [serie('P/L', 'pl', '#60a5fa', vezes), serie('P/VP', 'pvp', '#f59e0b', vezes)], layout, opcoes);
    Plotly.newPlot('evolucao-percentuais', [serie('ROE', 'roe', '#a78bfa', percentual, 100), serie('DY', 'dy', '#16c784', percentual, 100)],
                   { ...layout, yaxis: { ...layout.yaxis, ticksuffix: '%' } }, opcoes);
}

// Resumo do período exibido no gráfico
function resumoPeriodo(g) {
    const fechamentos = g.close.filter(ehNumero);
    if (fechamentos.length < 2) return '';
    const maxima = Math.max(...g.high.filter(ehNumero));
    const minima = Math.min(...g.low.filter(ehNumero));
    const atual = fechamentos.at(-1);
    const variacao = atual / fechamentos[0] - 1;
    const volumes = g.volume.filter(ehNumero);
    const volumeMedio = volumes.length ? volumes.reduce((a, b) => a + b, 0) / volumes.length : null;
    const item = (rotulo, valor, dica, classe = '') => `<div data-tip="${dica}" tabindex="0"><span>${rotulo}</span><strong class="${classe}">${valor}</strong></div>`;
    const ibov = (g.ibov || []).filter(ehNumero);
    const variacaoIbov = ibov.length > 1 ? ibov.at(-1) / ibov[0] - 1 : null;
    const sinal = v => (v > 0 ? '+' : '') + pct(v);
    return item('Variação no período', sinal(variacao), 'Do primeiro ao último fechamento exibido', cor(variacao)) +
        item('Ibovespa no período', ehNumero(variacaoIbov) ? sinal(variacaoIbov) : 'N/D', 'Variação do índice no mesmo período', cor(variacaoIbov)) +
        item('Máxima', reais(maxima), 'Maior preço negociado no período') +
        item('Mínima', reais(minima), 'Menor preço negociado no período') +
        item('Distância da máxima', pct(atual / maxima - 1), 'Quanto a cotação atual está abaixo da máxima do período', cor(atual / maxima - 1)) +
        item('Ação x Ibovespa', ehNumero(variacaoIbov) ? sinal((1 + variacao) / (1 + variacaoIbov) - 1) : 'N/D',
             'Quanto a ação foi melhor (+) ou pior (−) que o Ibovespa no período', ehNumero(variacaoIbov) ? cor((1 + variacao) / (1 + variacaoIbov) - 1) : '') +
        item('Volume médio', ehNumero(volumeMedio) ? new Intl.NumberFormat('pt-BR', { notation: 'compact', maximumFractionDigits: 1 }).format(volumeMedio) + ' ações' : 'N/D',
             'Média de ações negociadas por candle no período');
}

function desenharGrafico(g, periodo) {
    const semanal = ['5 Anos', '10 Anos'].includes(periodo);
    const subiu = g.close.map((c, i) => c >= g.open[i]);
    const linhaRef = (y, cor) => ({ x: [g.datas[0], g.datas.at(-1)], y: [y, y], yaxis: 'y3', mode: 'lines',
                                    line: { color: cor, width: 1, dash: 'dot' }, hoverinfo: 'skip', showlegend: false });
    const series = [
        { type: 'candlestick', name: 'Preço', x: g.datas, open: g.open, high: g.high, low: g.low, close: g.close,
          increasing: { line: { color: '#16c784' } }, decreasing: { line: { color: '#ea3943' } } },
        { name: semanal ? 'Média 10 sem.' : 'Média 50', x: g.datas, y: g.media_curta, mode: 'lines', line: { color: '#f59e0b', width: 1.5 } },
        { name: semanal ? 'Média 40 sem.' : 'Média 200', x: g.datas, y: g.media_longa, mode: 'lines', line: { color: '#60a5fa', width: 1.5 } },
        { type: 'bar', name: 'Volume', x: g.datas, y: g.volume, yaxis: 'y2', showlegend: false,
          marker: { color: subiu.map(s => s ? 'rgba(22,199,132,.25)' : 'rgba(234,57,67,.25)') } },
        { name: 'IFR (14)', x: g.datas, y: g.ifr, yaxis: 'y3', mode: 'lines', line: { color: '#a78bfa', width: 1.5 } },
        linhaRef(70, '#ea3943'), linhaRef(30, '#16c784'),
        // Ibovespa na mesma base da ação (começa no mesmo preço): mostra quem subiu mais no período
        ...(g.ibov_base?.some(ehNumero) ? [{ name: 'Ibovespa (mesma base)', x: g.datas, y: g.ibov_base, customdata: g.ibov,
            mode: 'lines', line: { color: '#cbd5e1', width: 1.5, dash: 'dot' },
            hovertemplate: 'Ibovespa: %{customdata:,.0f} pts<extra></extra>' }] : []),
    ];
    const eixo = { gridcolor: '#222c40', zerolinecolor: '#222c40' };
    const layout = {
        height: 560, margin: { l: 56, r: 16, t: 8, b: 32 }, separators: ',.',
        paper_bgcolor: '#111827', plot_bgcolor: '#111827', font: { color: '#8a96ab', family: 'Inter' },
        hovermode: 'x unified', legend: { orientation: 'h', y: 1.06 },
        xaxis: { ...eixo, type: 'category', nticks: 8, rangeslider: { visible: false } },
        yaxis: { ...eixo, domain: [0.32, 1], tickprefix: 'R$ ' },
        // Volume sobreposto ao preço, ocupando só os 20% inferiores (escala 5x o maior volume)
        yaxis2: { overlaying: 'y', range: [0, Math.max(...g.volume) * 5 || 1], showgrid: false, showticklabels: false },
        yaxis3: { ...eixo, domain: [0, 0.24], range: [0, 100], tickvals: [30, 70], title: 'IFR' },
    };
    $('grafico-precos').innerHTML = '';
    Plotly.newPlot('grafico-precos', series, layout, { displaylogo: false, responsive: true });
}

async function carregarAnalise() {
    const ticker = $('campo-ticker').value.toUpperCase().trim().replace('.SA', '');
    const periodo = $('periodo-grafico').value;
    if (!ticker) return;

    $('secao-grafico').hidden = false;
    $('grafico-precos').innerHTML = '<div class="mensagem"><div class="carregador"></div></div>';
    if (!$('ficha-container').querySelector('.ficha')) {
        $('ficha-container').innerHTML = '<div class="cartao mensagem"><div class="carregador"></div><p>Carregando indicadores...</p></div>';
    }

    try {
        const d = await (await fetch(`${API}/api/analise?ticker=${encodeURIComponent(ticker)}&periodo=${encodeURIComponent(periodo)}`)).json();
        const extras = ['leitura-container', 'indicadores-container', 'evolucao-container'];
        if (d.erro) {
            $('ficha-container').innerHTML = `<div class="cartao vazio"><h3>${escapar(d.erro)}</h3><p>Confira o código digitado.</p></div>`;
            $('secao-grafico').hidden = true;
            extras.forEach(id => { $(id).innerHTML = ''; });
            return;
        }
        document.title = `${d.ticker} · ${d.nome} | JMP Investimentos`;
        $('ficha-container').innerHTML = montarCabecalhoAcao(d, periodo);
        $('leitura-container').innerHTML = montarLeituraMetodos(d);
        $('indicadores-container').innerHTML = montarIndicadores(d);
        $('evolucao-container').innerHTML = montarEvolucao(d.historico);
        desenharGraficoMetodos(d.fundamentos);
        desenharEvolucao(d.historico);
        if (d.grafico?.datas.length) {
            $('resumo-periodo').innerHTML = resumoPeriodo(d.grafico);
            desenharGrafico(d.grafico, periodo);
        } else {
            $('resumo-periodo').innerHTML = '';
            $('grafico-precos').innerHTML = `<div class="mensagem"><p>Não foi possível obter o histórico de preços agora (fonte: Yahoo Finance).</p>
                <button class="botao-secundario" onclick="carregarAnalise()"><i data-lucide="refresh-cw"></i> Tentar novamente</button></div>`;
        }
    } catch {
        $('ficha-container').innerHTML = '<div class="cartao vazio"><h3>Não foi possível carregar a ação</h3><p>Tente novamente em alguns segundos.</p></div>';
    }
    icones();
}


/* ------------------------------ INICIALIZAÇÃO ------------------------------ */
document.addEventListener('DOMContentLoaded', () => {
    iniciarBaloes();
    // Abre no método Graham, com os últimos filtros usados; se essa busca já foi feita, aparece na hora
    const cache = lerCache();
    preencherFiltros(cache.filtros || FILTROS_PADRAO);
    $('data_ref').value = cache.filtros?.data || '';
    if ($('data_ref').value) $('data_ref').closest('details').open = true;
    montarMenuColunas();
    montarExplicacoesMetodos();
    marcarEstrategia(METODO_PADRAO);
    carregarDados();
    // Aberta pelo link de uma ação (nova aba): vai direto para a análise dela
    const acao = new URLSearchParams(location.search).get('acao');
    if (acao) abrirAnalise(acao.toUpperCase());
    configurarDataReferencia();

    // Fecha o menu de colunas ao clicar fora dele ou apertar Esc
    document.addEventListener('click', e => { if (!e.target.closest('.menu-colunas')) $('lista-colunas').hidden = true; });
    document.addEventListener('keydown', e => { if (e.key === 'Escape') $('lista-colunas').hidden = true; });

    atualizarRelogio();
    setInterval(atualizarRelogio, 1000);
    atualizarMercado();
    setInterval(atualizarMercado, 30000);
    carregarFeriados().then(atualizarMercado);
    icones();
});
