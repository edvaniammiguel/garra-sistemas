/* garra-periodo.js — (29/10/2026) Campo de PERÍODO único do Garra Sistemas.
 *
 * Um campo de texto ("01/09/2026 - 30/09/2026") abre um calendário com dois
 * meses lado a lado (um no celular): 1º clique = início, 2º clique = fim.
 * Atalhos: Hoje, Ontem, Esta semana, Este mês, Mês passado, Últimos 30 dias.
 * Também aceita digitar o período ou uma data só.
 *
 * O componente NÃO muda o que a tela envia: escreve em dois <input type="hidden">
 * (AAAA-MM-DD) com os mesmos ids dos antigos "De / Até" e dispara `change` neles —
 * os onchange que já existiam continuam valendo.
 *
 * Uso (anexa sozinho no carregamento da página):
 *   <input type="text" id="x-periodo" data-periodo-de="x-de" data-periodo-ate="x-ate">
 *   <input type="hidden" id="x-de" onchange="..."><input type="hidden" id="x-ate" onchange="...">
 * Mudou os hidden por código?  GarraPeriodo.atualizar('x-periodo')
 *
 * (29/10/2026) DIA ÚNICO: <input type="text" id="y-campo" data-periodo-unico="y-data"> + hidden y-data.
 * Um clique escolhe o dia; sem atalhos. GarraPeriodo.permitir('y-campo', ['2026-10-27', …])
 * deixa clicáveis só esses dias (ex.: dias com registro na OS).
 */
(function () {
  if (window.GarraPeriodo) return;

  const MESES = ['Janeiro', 'Fevereiro', 'Março', 'Abril', 'Maio', 'Junho', 'Julho',
                 'Agosto', 'Setembro', 'Outubro', 'Novembro', 'Dezembro'];
  const LARANJA = '#E8820C', AZUL = '#1A2A5E';

  const css = `
.gp-pop{position:absolute;z-index:1000;background:#fff;border:1.5px solid #CBD5E1;border-radius:10px;
  box-shadow:0 8px 24px rgba(0,0,0,.14);padding:12px;font-family:inherit;color:${AZUL};user-select:none}
.gp-pop.hidden{display:none}
.gp-atalhos{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:10px}
.gp-atalhos button,.gp-limpar{border:1.5px solid #CBD5E1;background:#fff;border-radius:6px;padding:4px 9px;
  font-size:12px;cursor:pointer;color:${AZUL};font-family:inherit}
.gp-atalhos button:hover,.gp-limpar:hover,.gp-nav:hover{border-color:${LARANJA};color:${LARANJA}}
.gp-corpo{display:flex;align-items:flex-start;gap:16px}
.gp-nav{border:1.5px solid #CBD5E1;background:#fff;border-radius:6px;width:30px;height:30px;cursor:pointer;
  font-size:16px;line-height:1;color:${AZUL};flex-shrink:0;margin-top:22px}
.gp-mes-tit{text-align:center;font-size:13px;font-weight:700;margin-bottom:6px}
.gp-grade{display:grid;grid-template-columns:repeat(7,32px);gap:2px 0}
.gp-sem{height:22px;display:flex;align-items:center;justify-content:center;font-size:11px;color:#64748B;font-weight:600}
.gp-dia{height:30px;display:flex;align-items:center;justify-content:center;font-size:13px;cursor:pointer;border-radius:6px}
.gp-dia:hover{background:#F1F5F9}
.gp-fora{visibility:hidden}
.gp-off{color:#CBD5E1;cursor:default}
.gp-off:hover{background:none}
.gp-com{font-weight:700;background:#FFF4E6}
.gp-hoje{font-weight:800;text-decoration:underline}
.gp-meio{background:#FDE7CC;border-radius:0;color:#7A3E00}
.gp-borda{background:${LARANJA};color:#fff;font-weight:700}
.gp-rodape{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-top:8px;font-size:12px;color:#64748B}
input.gp-invalido{border-color:#DC2626 !important}`;

  const pad = n => String(n).padStart(2, '0');
  const iso = d => d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate());
  const br = d => pad(d.getDate()) + '/' + pad(d.getMonth() + 1) + '/' + d.getFullYear();
  const hoje = () => { const d = new Date(); return new Date(d.getFullYear(), d.getMonth(), d.getDate()); };
  const somaDias = (d, n) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + n);
  const deIso = s => {
    const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(s || '');
    return m ? new Date(+m[1], +m[2] - 1, +m[3]) : null;
  };
  const deBr = s => {
    const m = /^(\d{1,2})\/(\d{1,2})\/(\d{4})$/.exec((s || '').trim());
    if (!m) return null;
    const d = new Date(+m[3], +m[2] - 1, +m[1]);
    return d.getMonth() === +m[2] - 1 ? d : null;   // 31/02 não vira 03/03
  };

  const ATALHOS = [
    ['Hoje', () => [hoje(), hoje()]],
    ['Ontem', () => { const d = somaDias(hoje(), -1); return [d, d]; }],
    ['Esta semana', () => { const h = hoje(); return [somaDias(h, -h.getDay()), h]; }],
    ['Este mês', () => { const h = hoje(); return [new Date(h.getFullYear(), h.getMonth(), 1), h]; }],
    ['Mês passado', () => { const h = hoje(); return [new Date(h.getFullYear(), h.getMonth() - 1, 1), new Date(h.getFullYear(), h.getMonth(), 0)]; }],
    ['Últimos 30 dias', () => { const h = hoje(); return [somaDias(h, -29), h]; }],
  ];

  let pop = null, ativo = null;          // um calendário só na página, do campo aberto
  let base = null, ini = null, fim = null; // mês visível + seleção em andamento
  let permitidos = null;                   // dia único: dias clicáveis (Set ISO) ou null = todos

  function montarPopup() {
    const st = document.createElement('style');
    st.textContent = css;
    document.head.appendChild(st);
    pop = document.createElement('div');
    pop.className = 'gp-pop hidden';
    pop.addEventListener('mousedown', e => e.preventDefault());   // não tira o foco do campo
    pop.addEventListener('click', aoClicar);
    document.body.appendChild(pop);
    document.addEventListener('mousedown', e => {
      if (ativo && !pop.contains(e.target) && e.target !== ativo.campo) fechar();
    });
    document.addEventListener('keydown', e => { if (e.key === 'Escape' && ativo) fechar(); });
    window.addEventListener('resize', () => { if (ativo) fechar(); });
  }

  function htmlMes(ref) {
    const y = ref.getFullYear(), m = ref.getMonth(), h = hoje();
    let s = `<div><div class="gp-mes-tit">${MESES[m]} ${y}</div><div class="gp-grade">`;
    'DSTQQSS'.split('').forEach(c => { s += `<div class="gp-sem">${c}</div>`; });
    const p = new Date(y, m, 1);
    for (let i = 0; i < p.getDay(); i++) s += '<div class="gp-dia gp-fora"></div>';
    for (let d = 1; d <= new Date(y, m + 1, 0).getDate(); d++) {
      const dt = new Date(y, m, d), t = dt.getTime();
      let c = 'gp-dia';
      if (permitidos && !permitidos.has(iso(dt))) { s += `<div class="gp-dia gp-off">${d}</div>`; continue; }
      if (permitidos) c += ' gp-com';
      if (t === h.getTime()) c += ' gp-hoje';
      if ((ini && t === ini.getTime()) || (fim && t === fim.getTime())) c += ' gp-borda';
      else if (ini && fim && t > ini.getTime() && t < fim.getTime()) c += ' gp-meio';
      s += `<div class="${c}" data-gp-dia="${iso(dt)}">${d}</div>`;
    }
    return s + '</div></div>';
  }

  function desenhar() {
    const doisMeses = window.innerWidth >= 640;
    const prox = new Date(base.getFullYear(), base.getMonth() + 1, 1);
    const unico = ativo.unico;
    const n = ini && fim ? Math.round((fim - ini) / 864e5) + 1 : 0;
    const msg = unico ? (permitidos ? 'Só os dias com registro podem ser escolhidos.' : 'Clique no dia.')
      : !ini ? 'Clique no primeiro dia.' : !fim ? 'Agora clique no último dia.'
      : n + (n === 1 ? ' dia selecionado' : ' dias selecionados');
    pop.innerHTML =
      (unico ? '' : `<div class="gp-atalhos">${ATALHOS.map((a, i) => `<button type="button" data-gp-atalho="${i}">${a[0]}</button>`).join('')}</div>`) +
      `<div class="gp-corpo"><button type="button" class="gp-nav" data-gp-nav="-1" aria-label="Mês anterior">‹</button>` +
      htmlMes(base) + (doisMeses ? htmlMes(prox) : '') +
      `<button type="button" class="gp-nav" data-gp-nav="1" aria-label="Próximo mês">›</button></div>` +
      `<div class="gp-rodape"><span>${msg}</span>${unico ? '' : '<button type="button" class="gp-limpar" data-gp-limpar>Limpar</button>'}</div>`;
  }

  function posicionar() {
    const r = ativo.campo.getBoundingClientRect();
    pop.style.top = (r.bottom + window.scrollY + 4) + 'px';
    const larg = pop.offsetWidth;
    const esq = Math.min(r.left, document.documentElement.clientWidth - larg - 8);
    pop.style.left = (Math.max(8, esq) + window.scrollX) + 'px';
  }

  function abrir(inst) {
    if (!pop) montarPopup();
    ativo = inst;
    permitidos = inst.permitidos;
    ini = deIso(inst.de.value);
    fim = deIso(inst.ate.value);
    const ref = ini || hoje();
    base = new Date(ref.getFullYear(), ref.getMonth(), 1);
    pop.classList.remove('hidden');
    desenhar();
    posicionar();
  }

  function fechar() {
    if (!ativo) return;
    const inst = ativo;
    ativo = null;
    pop.classList.add('hidden');
    inst.mostrar();   // seleção pela metade não vale: volta ao período aplicado
  }

  function aplicar(inst, a, b) {
    if (a && b && b < a) [a, b] = [b, a];
    const novoDe = a ? iso(a) : '', novoAte = b ? iso(b) : '';
    const mudou = inst.de.value !== novoDe || inst.ate.value !== novoAte;
    inst.de.value = novoDe;
    inst.ate.value = novoAte;
    inst.mostrar();
    if (mudou) {
      inst.de.dispatchEvent(new Event('change', { bubbles: true }));
      if (inst.ate !== inst.de) inst.ate.dispatchEvent(new Event('change', { bubbles: true }));
    }
  }

  function aoClicar(e) {
    const inst = ativo;
    if (!inst) return;
    const dia = e.target.closest('[data-gp-dia]');
    const nav = e.target.closest('[data-gp-nav]');
    const at = e.target.closest('[data-gp-atalho]');
    if (dia) {
      const d = deIso(dia.dataset.gpDia);
      if (inst.unico) { aplicar(inst, d, d); fechar(); return; }
      if (!ini || fim) { ini = d; fim = null; desenhar(); return; }
      aplicar(inst, ini, d);
      fechar();
    } else if (nav) {
      base = new Date(base.getFullYear(), base.getMonth() + Number(nav.dataset.gpNav), 1);
      desenhar();
    } else if (at) {
      const [a, b] = ATALHOS[Number(at.dataset.gpAtalho)][1]();
      aplicar(inst, a, b);
      fechar();
    } else if (e.target.closest('[data-gp-limpar]')) {
      aplicar(inst, null, null);
      fechar();
    }
  }

  function digitado(inst) {
    const txt = inst.campo.value.trim();
    if (!txt) { if (inst.unico) return false; aplicar(inst, null, null); return true; }
    const partes = txt.split(/\s*(?:-|–|até|a)\s*/i).filter(Boolean);
    const a = deBr(partes[0]), b = partes.length > 1 ? deBr(partes[1]) : a;
    if (!a || !b || partes.length > 2) return false;
    if (inst.unico && (partes.length > 1 || (inst.permitidos && !inst.permitidos.has(iso(a))))) return false;
    aplicar(inst, a, b);
    return true;
  }

  function anexar(campo, de, ate, opcoes) {
    if (!campo || !de || !ate || campo._garraPeriodo) return campo && campo._garraPeriodo;
    const unico = !!(opcoes && opcoes.unico);
    const inst = {
      campo, de, ate, unico, permitidos: null,
      mostrar() {
        const a = deIso(de.value), b = deIso(ate.value);
        campo.value = a ? (br(a) + (b && b.getTime() !== a.getTime() ? ' - ' + br(b) : '')) : '';
        campo.classList.remove('gp-invalido');
      },
    };
    campo.setAttribute('autocomplete', 'off');
    if (!campo.placeholder) campo.placeholder = unico ? 'dd/mm/aaaa' : 'dd/mm/aaaa - dd/mm/aaaa';
    campo.addEventListener('click', () => { if (ativo !== inst) abrir(inst); });
    campo.addEventListener('focus', () => { if (ativo !== inst) abrir(inst); });
    campo.addEventListener('input', () => campo.classList.remove('gp-invalido'));
    campo.addEventListener('keydown', e => {
      if (e.key !== 'Enter') return;
      e.preventDefault();
      if (digitado(inst)) fechar(); else campo.classList.add('gp-invalido');
    });
    campo.addEventListener('blur', () => {
      if (!digitado(inst)) { inst.mostrar(); campo.classList.add('gp-invalido'); setTimeout(() => campo.classList.remove('gp-invalido'), 1500); }
      fechar();
    });
    campo._garraPeriodo = inst;
    inst.mostrar();
    return inst;
  }

  function anexarTodos(raiz) {
    (raiz || document).querySelectorAll('input[data-periodo-de]').forEach(c =>
      anexar(c, document.getElementById(c.dataset.periodoDe), document.getElementById(c.dataset.periodoAte)));
    (raiz || document).querySelectorAll('input[data-periodo-unico]').forEach(c => {
      const h = document.getElementById(c.dataset.periodoUnico);
      anexar(c, h, h, { unico: true });
    });
  }

  function permitir(idCampo, listaIso) {
    const c = document.getElementById(idCampo);
    if (c && c._garraPeriodo) c._garraPeriodo.permitidos = listaIso ? new Set(listaIso) : null;
  }

  function atualizar(idCampo) {
    const c = document.getElementById(idCampo);
    if (c && c._garraPeriodo) c._garraPeriodo.mostrar();
  }

  window.GarraPeriodo = { anexar, anexarTodos, atualizar, permitir };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => anexarTodos());
  else anexarTodos();
})();
