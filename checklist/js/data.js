/* ═══════════════════════════════════════════
   data.js — Check lists padrão e dados base
   Garra Terraplenagem e Caçambas
═══════════════════════════════════════════ */

// ── FROTA PADRÃO (pode ser editada pelo gestor) ──
const DEFAULT_FLEET = {
  maquinas: [
    { id:'EH-02', desc:'Escavadeira Hidráulica', active:true },
    { id:'EH-03', desc:'Escavadeira Hidráulica', active:true },
    { id:'EH-39', desc:'Escavadeira Hidráulica', active:true },
    { id:'EH-50', desc:'Escavadeira Hidráulica', active:true },
    { id:'PC-43', desc:'Patrol / Motoniveladora', active:true },
    { id:'PC-49', desc:'Patrol / Motoniveladora', active:true },
    { id:'RE-29', desc:'Retroescavadeira', active:true },
    { id:'RE-45', desc:'Retroescavadeira', active:true },
  ],
  carro: [
    { id:'CA-12', desc:'Carro de Apoio', active:true },
    { id:'CA-21', desc:'Carro de Apoio', active:true },
    { id:'CA-32', desc:'Carro de Apoio', active:true },
    { id:'CA-40', desc:'Carro de Apoio', active:true },
    { id:'CA-42', desc:'Carro de Apoio', active:true },
    { id:'CA-44', desc:'Carro de Apoio', active:true },
    { id:'CA-47', desc:'Carro de Apoio', active:true },
    { id:'CA-48', desc:'Carro de Apoio', active:true },
  ],
  caminhao: [
    { id:'CB-05',  desc:'Caminhão Basculante', active:true },
    { id:'CB-06',  desc:'Caminhão Basculante', active:true },
    { id:'CB-015', desc:'Caminhão Basculante', active:true },
    { id:'CB-016', desc:'Caminhão Basculante', active:true },
    { id:'CB-024', desc:'Caminhão Basculante', active:true },
    { id:'CB-030', desc:'Caminhão Basculante', active:true },
    { id:'CB-037', desc:'Caminhão Basculante', active:true },
    { id:'CP-019', desc:'Caminhão Pipa', active:true },
    { id:'CPO-022',desc:'Caminhão Pipa / Oficina', active:true },
    { id:'CPO-026',desc:'Caminhão Pipa / Oficina', active:true },
    { id:'CPO-036',desc:'Caminhão Pipa / Oficina', active:true },
    { id:'CA-023', desc:'Caminhão de Apoio', active:true },
    { id:'CA-040', desc:'Caminhão de Apoio', active:true },
    { id:'CR-07',  desc:'Caminhão Reboque', active:true },
  ],
};

// ── CHECK LISTS PADRÃO (sistema, não editáveis) ──
const DEFAULT_CHECKLISTS = {
  maquinas: {
    id: 'maquinas', isDefault: true,
    label: 'Máquinas', icon: '🚜', desc: 'Escavadeiras, retroescavadeiras, patrol',
    vehicleCat: 'maquinas',
    scoreRules: { full: 100, nc: 60, obs: 20, ontime: 10 },
    steps: [
      {
        title: 'Identificação', sub: 'Preencha os dados iniciais', type: 'meta',
        fields: [
          { id:'operador', label:'Operador Responsável', type:'text', placeholder:'Seu nome' },
          { id:'local',    label:'Local da Operação',    type:'text', placeholder:'Ex.: Florestal, Probase...' },
          { id:'data',     label:'Data',                 type:'date' },
          { id:'equipamento', label:'Equipamento',       type:'select', options:'vehicles' },
          { id:'horimetro',   label:'Horímetro Atual',   type:'number', placeholder:'0000.0' },
          { id:'tipo', label:'Tipo de Check List', type:'select', options:['Preventivo','Corretivo','Entrega','Assumindo Operação'] },
        ]
      },
      {
        title:'Sistemas Gerais', sub:'Lubrificação, abastecimento e limpeza', type:'checklist',
        items:[
          {id:'lubrificacao', label:'Lubrificação geral', pts:1, photoMode:'nc_only'},
          {id:'abastecimento', label:'Abastecimento / Tampa do combustível', pts:1, photoMode:'nc_only'},
          {id:'limpeza', label:'Limpeza e higiene (interior da cabine)', pts:1, photoMode:'nc_only'},
          {id:'filtro_ar', label:'Filtros de Ar', pts:1, photoMode:'nc_only'},
          {id:'filtro_oleo', label:'Filtro de Óleo do Motor', pts:1, photoMode:'nc_only'},
          {id:'nivel_oleo', label:'Nível de óleo (Motor e Transmissão)', pts:2, photoMode:'nc_only'},
          {id:'radiador', label:'Radiador (água / tampa)', pts:1, photoMode:'nc_only'},
        ]
      },
      {
        title:'Rodagem e Estrutura', sub:'Pneus, rodas, esteiras e suspensão', type:'checklist',
        items:[
          {id:'pneu_rodas', label:'Pneu / Rodas ou Esteiras', pts:2, photoMode:'nc_only'},
          {id:'suspensao', label:'Suspensão / Pistons', pts:2, photoMode:'nc_only'},
          {id:'material_rodante', label:'Material rodante (roldanas / desgastes)', pts:1, photoMode:'nc_only'},
          {id:'mangueiras', label:'Conexões / Mangueiras', pts:1, photoMode:'nc_only'},
          {id:'protecao_helice', label:'Proteção de Hélice', pts:1, photoMode:'nc_only'},
        ]
      },
      {
        title:'Cabine e Segurança', sub:'Visibilidade, alarmes e proteções', type:'checklist',
        items:[
          {id:'retrovisor', label:'Retrovisor Externo', pts:1, photoMode:'nc_only'},
          {id:'farois', label:'Faróis (Alto / Baixo / Setas / Luzes)', pts:2, photoMode:'nc_only'},
          {id:'buzina', label:'Buzina', pts:1, photoMode:'nc_only'},
          {id:'bancos', label:'Bancos / Cinto de segurança', pts:2, photoMode:'nc_only'},
          {id:'alarmes', label:'Alarmes Sonoros (Buzina / Ré)', pts:1, photoMode:'nc_only'},
          {id:'limpador', label:'Limpador e água de Para-brisa', pts:1, photoMode:'nc_only'},
          {id:'pedais', label:'Pedais / Alavancas', pts:1, photoMode:'nc_only'},
          {id:'vidros', label:'Vidros (Para-brisa / Laterais)', pts:1, photoMode:'nc_only'},
          {id:'travas', label:'Travas de Segurança (calços)', pts:2, photoMode:'nc_only'},
        ]
      },
      {
        title:'Elétrico e Implementos', sub:'Painel, giro e implementos de trabalho', type:'checklist',
        items:[
          {id:'eletrico', label:'Sistema Elétrico', pts:2, photoMode:'nc_only'},
          {id:'painel', label:'Painel / Velocímetro', pts:1, photoMode:'nc_only'},
          {id:'giro', label:'Sistema de Giro', pts:1, photoMode:'nc_only'},
          {id:'angular', label:'Indicador Angular da Lança / Nivelador', pts:1, photoMode:'nc_only'},
          {id:'implementos', label:'Conchas / Unhas / Perfuratriz / Lâminas', pts:2, photoMode:'nc_only'},
          {id:'estado_geral', label:'Estado Geral de Conservação / Lataria', pts:1, photoMode:'nc_only'},
        ]
      },
      {
        title:'Observações Finais', sub:'Anote não conformidades', type:'obs',
        fields:[
          {id:'observacoes', label:'Observações / Não Conformidades', type:'textarea', placeholder:'Descreva aqui qualquer não conformidade ou observação relevante...'},
          {id:'ot',          label:'Número da OT (se houver)',        type:'text',     placeholder:'OT000'},
        ]
      }
    ]
  },

  carro: {
    id: 'carro', isDefault: true,
    label: 'Carro de Apoio', icon: '🚗', desc: 'Veículos leves de suporte',
    vehicleCat: 'carro',
    scoreRules: { full: 100, nc: 60, obs: 20, ontime: 10 },
    steps: [
      {
        title:'Identificação', sub:'Dados do veículo', type:'meta',
        fields:[
          {id:'operador', label:'Responsável', type:'text', placeholder:'Seu nome'},
          {id:'local',    label:'Local',       type:'text', placeholder:'Ex.: Escritório, Obra...'},
          {id:'data',     label:'Data',        type:'date'},
          {id:'veiculo',  label:'Veículo',     type:'select', options:'vehicles'},
          {id:'km',       label:'KM Atual',    type:'number', placeholder:'000000'},
          {id:'situacao', label:'Situação',    type:'select', options:['Estou fixo (rotina)','Pegando o veículo (assumindo)','Entregando o veículo']},
        ]
      },
      {
        title:'Documentação e Segurança', sub:'Itens obrigatórios', type:'checklist',
        items:[
          {id:'crv', label:'CRV (Certificado de Registro)', pts:2, photoMode:'nc_only'},
          {id:'triangulo', label:'Triângulo / Macaco / Chave de Roda / Estepe', pts:2, photoMode:'nc_only'},
          {id:'extintor', label:'Extintor de Incêndio (validade e pressão)', pts:2, photoMode:'nc_only'},
        ]
      },
      {
        title:'Estado Físico e Pneus', sub:'Lataria, pneus e suspensão', type:'checklist',
        items:[
          {id:'lataria', label:'Lataria / Pintura', pts:1, photoMode:'nc_only'},
          {id:'pneus', label:'Pneus / Rodas (calibragem)', pts:2, photoMode:'nc_only'},
          {id:'amortecedores', label:'Amortecedores', pts:1, photoMode:'nc_only'},
          {id:'abastecimento', label:'Abastecimento', pts:1, photoMode:'nc_only'},
        ]
      },
      {
        title:'Cabine e Visibilidade', sub:'Espelhos, vidros e fluidos', type:'checklist',
        items:[
          {id:'limpeza', label:'Limpeza e higiene interior', pts:1, photoMode:'nc_only'},
          {id:'retrovisor', label:'Retrovisor Externo e Interno', pts:1, photoMode:'nc_only'},
          {id:'limpador', label:'Limpador / Água de Para-brisa / Palhetas', pts:1, photoMode:'nc_only'},
          {id:'parabrisa', label:'Para-brisa (trincas e estado)', pts:1, photoMode:'nc_only'},
          {id:'nivel_oleo', label:'Nível de Óleo', pts:2, photoMode:'nc_only'},
          {id:'radiador', label:'Radiador (Água / Tampa)', pts:1, photoMode:'nc_only'},
          {id:'mangueiras', label:'Conexões / Mangueiras (vazamentos)', pts:1, photoMode:'nc_only'},
        ]
      },
      {
        title:'Luzes, Freios e Painel', sub:'Iluminação e frenagem', type:'checklist',
        items:[
          {id:'luzes', label:'Luzes (Faróis / Freio / Lanternas / Ré)', pts:2, photoMode:'nc_only'},
          {id:'buzina', label:'Buzina', pts:1, photoMode:'nc_only'},
          {id:'freios', label:'Freios de Pé / Mão', pts:2, photoMode:'nc_only'},
          {id:'painel', label:'Painel / Velocímetro', pts:1, photoMode:'nc_only'},
        ]
      },
      {
        title:'Observações Finais', sub:'Registre não conformidades', type:'obs',
        fields:[
          {id:'observacoes', label:'Observações / Não Conformidades', type:'textarea', placeholder:'Descreva problemas encontrados...'},
        ]
      }
    ]
  },

  caminhao: {
    id: 'caminhao', isDefault: true,
    label: 'Caminhão – Semanal', icon: '🚛', desc: 'Caminhões basculantes e pipa',
    vehicleCat: 'caminhao',
    scoreRules: { full: 100, nc: 60, obs: 20, ontime: 10 },
    steps: [
      {
        title:'Identificação', sub:'Dados do caminhão e motorista', type:'meta',
        fields:[
          {id:'operador', label:'Motorista Responsável', type:'text', placeholder:'Seu nome'},
          {id:'local',    label:'Local',                 type:'text', placeholder:'Ex.: Galpão, Obra Lev...'},
          {id:'data',     label:'Data',                  type:'date'},
          {id:'veiculo',  label:'Veículo',               type:'select', options:'vehicles'},
          {id:'km',       label:'KM Atual',              type:'number', placeholder:'000000'},
          {id:'situacao', label:'Situação',              type:'select', options:['Estou fixo no caminhão','Pegando o caminhão (assumindo)','Entregando o caminhão']},
        ]
      },
      {
        title:'Fluidos e Motor', sub:'Lubrificação, óleo e combustível', type:'checklist',
        items:[
          {id:'lubrificacao', label:'Lubrificação geral', pts:1, photoMode:'nc_only'},
          {id:'abastecimento', label:'Abastecimento / Tampa do combustível', pts:1, photoMode:'nc_only'},
          {id:'filtro_ar', label:'Filtro de Ar', pts:1, photoMode:'nc_only'},
          {id:'nivel_oleo', label:'Nível de Óleo (Motor e Transmissão)', pts:2, photoMode:'nc_only'},
          {id:'radiador', label:'Radiador (Água / Tampa)', pts:1, photoMode:'nc_only'},
          {id:'mangueiras', label:'Conexões / Mangueiras (vazamentos)', pts:1, photoMode:'nc_only'},
          {id:'temperatura', label:'Marcador de Temperatura', pts:1, photoMode:'nc_only'},
        ]
      },
      {
        title:'Pneus, Rodas e Suspensão', sub:'Estado dos pneus', type:'checklist',
        items:[
          {id:'pneus', label:'Pneus / Rodas (calibragem e estado)', pts:2, photoMode:'nc_only'},
          {id:'suspensao', label:'Suspensão / Molas (trincas)', pts:2, photoMode:'nc_only'},
          {id:'faixa_refletiva', label:'Faixa Refletiva da Carroceria', pts:1, photoMode:'nc_only'},
          {id:'limpeza', label:'Limpeza e higiene', pts:1, photoMode:'nc_only'},
        ]
      },
      {
        title:'Documentação e Segurança', sub:'Itens obrigatórios', type:'checklist',
        items:[
          {id:'crv', label:'CRV (Certificado do Registro do Veículo)', pts:2, photoMode:'nc_only'},
          {id:'triangulo', label:'Triângulo de Sinalização', pts:2, photoMode:'nc_only'},
          {id:'extintor', label:'Extintor de Incêndio (validade e pressão)', pts:2, photoMode:'nc_only'},
          {id:'retrovisor', label:'Retrovisor Externo', pts:1, photoMode:'nc_only'},
        ]
      },
      {
        title:'Luzes, Alarmes e Painel', sub:'Iluminação e freios', type:'checklist',
        items:[
          {id:'luzes', label:'Luzes (Faróis / Freio / Lanternas / Ré / Pisca)', pts:2, photoMode:'nc_only'},
          {id:'alarmes', label:'Alarmes Sonoros (Buzina / Ré / Dispositivo Caçamba)', pts:2, photoMode:'nc_only'},
          {id:'freios', label:'Freios de Pé / Mão', pts:2, photoMode:'nc_only'},
          {id:'painel', label:'Painel / Velocímetro / Tacógrafo', pts:1, photoMode:'nc_only'},
          {id:'limpador', label:'Limpador / Água de Para-brisa / Palhetas', pts:1, photoMode:'nc_only'},
        ]
      },
      {
        title:'Observações Finais', sub:'Registre não conformidades', type:'obs',
        fields:[
          {id:'observacoes', label:'Observações / Não Conformidades', type:'textarea', placeholder:'Descreva os problemas encontrados...'},
        ]
      }
    ]
  }
};
