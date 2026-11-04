/* (04/11/2026) GarraGuia — tour guiado dentro do próprio sistema (padrão "demonstração" tipo Getdemo),
 * rodando na tela REAL: escurece o fundo, destaca o elemento do passo, mostra o balão com o texto,
 * "n de N", Anterior/Próximo, narração com a voz do navegador (speechSynthesis) e velocidade.
 * Uso:  <script src="/operacional/static/js/guia.js"></script>
 *       GarraGuia.iniciar({ titulo, passos:[ { titulo, texto, alvo, antes, posicao } ... ] })
 *   alvo   : seletor CSS ou função que devolve o elemento (sem alvo = cartão centralizado)
 *   antes  : função (pode ser async) executada antes de mostrar o passo — abre a tela certa
 *   bloquear (padrão true): em modo demonstração o elemento destacado NÃO recebe cliques —
 *            o tour mostra, não executa. Passo com bloquear:false deixa o usuário clicar no alvo.
 * Nada aqui grava dados: quem grava é o próprio sistema, e só se o passo liberar o clique. */
(function () {
  const CSS = `
  .gg-sombra{position:fixed;z-index:99990;border-radius:8px;box-shadow:0 0 0 9999px rgba(15,23,42,.62);transition:all .25s ease;pointer-events:auto;outline:3px solid #FF9500;outline-offset:3px}
  .gg-sombra.livre{pointer-events:none}
  .gg-bloq{position:fixed;inset:0;z-index:99989}
  .gg-balao{position:fixed;z-index:99995;width:380px;max-width:calc(100vw - 24px);background:#fff;color:#0F172A;border-radius:14px;box-shadow:0 18px 50px rgba(15,23,42,.35);font-family:system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;padding:18px 20px 14px}
  .gg-balao.cartao{left:50%;top:50%;transform:translate(-50%,-50%);width:520px;text-align:center;padding:28px 30px 20px}
  .gg-balao h3{margin:0 0 8px;font-size:18px;color:#1A2A5E}
  .gg-balao.cartao h3{font-size:24px}
  .gg-balao p{margin:0 0 12px;font-size:14px;line-height:1.5}
  .gg-balao .gg-pe{display:flex;align-items:center;gap:8px;margin-top:6px}
  .gg-balao .gg-n{color:#16A34A;font-weight:700;font-size:12px;margin-right:auto}
  .gg-balao button{border:none;border-radius:8px;padding:8px 14px;font-size:13px;font-weight:700;cursor:pointer}
  .gg-balao .gg-prox{background:#16A34A;color:#fff}
  .gg-balao.cartao .gg-prox{padding:12px 26px;font-size:16px;border-radius:30px}
  .gg-balao .gg-ant{background:#F1F5F9;color:#334155}
  .gg-balao .gg-sair{position:absolute;top:8px;right:10px;background:none;color:#94A3B8;font-size:18px;padding:2px 6px}
  .gg-balao .gg-seta{position:absolute;width:14px;height:14px;background:#fff;transform:rotate(45deg)}
  .gg-ctl{position:fixed;top:10px;right:12px;z-index:99996;display:flex;gap:6px}
  .gg-ctl button{width:40px;height:40px;border-radius:50%;border:none;background:#1A2A5E;color:#fff;font-size:15px;font-weight:800;cursor:pointer;box-shadow:0 4px 12px rgba(0,0,0,.3)}
  .gg-ctl button.off{background:#64748B}
  .gg-ctl button.go{background:#16A34A}`;
  let roteiro = null, i = 0, el = {}, voz = true, vel = 1, falando = null;

  function css() { if (!document.getElementById('gg-css')) { const s = document.createElement('style'); s.id = 'gg-css'; s.textContent = CSS; document.head.appendChild(s); } }
  function q(sel) { if (!sel) return null; if (typeof sel === 'function') { try { return sel(); } catch (e) { return null; } } return document.querySelector(sel); }
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c])); }

  function falar(t) {
    parar();
    if (!voz || !('speechSynthesis' in window)) return;
    const u = new SpeechSynthesisUtterance(String(t).replace(/<[^>]+>/g, ''));
    u.lang = 'pt-BR'; u.rate = vel;
    const v = speechSynthesis.getVoices().find(x => /pt[-_]BR/i.test(x.lang)); if (v) u.voice = v;
    falando = u; speechSynthesis.speak(u);
  }
  function parar() { try { speechSynthesis.cancel(); } catch (e) { } falando = null; }

  function montar() {
    css();
    el.bloq = document.createElement('div'); el.bloq.className = 'gg-bloq';
    el.sombra = document.createElement('div'); el.sombra.className = 'gg-sombra';
    el.balao = document.createElement('div'); el.balao.className = 'gg-balao';
    el.ctl = document.createElement('div'); el.ctl.className = 'gg-ctl';
    el.ctl.innerHTML = '<button class="gg-voz" title="Narração">🔊</button><button class="gg-vel" title="Velocidade">1x</button><button class="go gg-re" title="Ouvir de novo">▶</button>';
    document.body.append(el.bloq, el.sombra, el.balao, el.ctl);
    el.ctl.querySelector('.gg-voz').onclick = function () { voz = !voz; this.textContent = voz ? '🔊' : '🔇'; this.classList.toggle('off', !voz); if (!voz) parar(); else falar(roteiro.passos[i].texto); };
    el.ctl.querySelector('.gg-vel').onclick = function () { vel = vel >= 1.5 ? 1 : +(vel + 0.25).toFixed(2); this.textContent = vel + 'x'; falar(roteiro.passos[i].texto); };
    el.ctl.querySelector('.gg-re').onclick = () => falar(roteiro.passos[i].texto);
    window.addEventListener('resize', posicionar); window.addEventListener('scroll', posicionar, true);
    document.addEventListener('keydown', teclas);
  }
  function teclas(e) { if (e.key === 'Escape') sair(); else if (e.key === 'ArrowRight' || e.key === 'Enter') proximo(); else if (e.key === 'ArrowLeft') anterior(); }

  let alvoAtual = null;
  function posicionar() {
    if (!roteiro) return;
    const p = roteiro.passos[i];
    const a = alvoAtual;
    if (!a) { el.sombra.style.display = 'none'; el.balao.classList.add('cartao'); el.balao.style.left = el.balao.style.top = ''; return; }
    el.balao.classList.remove('cartao');
    const r = a.getBoundingClientRect(), m = 6;
    el.sombra.style.display = ''; el.sombra.style.left = (r.left - m) + 'px'; el.sombra.style.top = (r.top - m) + 'px';
    el.sombra.style.width = (r.width + 2 * m) + 'px'; el.sombra.style.height = (r.height + 2 * m) + 'px';
    el.sombra.classList.toggle('livre', p.bloquear === false);
    // balão: abaixo do alvo se couber, senão acima; alinhado à esquerda do alvo, dentro da janela
    const bw = Math.min(380, window.innerWidth - 24), bh = el.balao.offsetHeight || 180;
    let top = r.bottom + 16, seta = 'cima';
    if (top + bh > window.innerHeight - 12) { top = r.top - bh - 16; seta = 'baixo'; }
    if (top < 12) { top = 12; seta = ''; }
    let left = Math.max(12, Math.min(r.left, window.innerWidth - bw - 12));
    if (p.posicao === 'esquerda' && r.left - bw - 16 > 12) { left = r.left - bw - 16; top = Math.max(12, r.top); seta = 'dir'; }
    el.balao.style.left = left + 'px'; el.balao.style.top = top + 'px';
    const s = el.balao.querySelector('.gg-seta'); if (s) {
      const cx = Math.min(Math.max(r.left + r.width / 2 - left - 7, 14), bw - 28);
      if (seta === 'cima') s.style.cssText = 'top:-7px;left:' + cx + 'px';
      else if (seta === 'baixo') s.style.cssText = 'bottom:-7px;left:' + cx + 'px';
      else if (seta === 'dir') s.style.cssText = 'right:-7px;top:18px';
      else s.style.display = 'none';
    }
  }

  async function mostrar(n) {
    if (!roteiro) return;
    i = Math.max(0, Math.min(n, roteiro.passos.length - 1));
    const p = roteiro.passos[i];
    parar();
    try { if (p.antes) await p.antes(); } catch (e) { console.warn('guia: passo', i, e); }
    alvoAtual = q(p.alvo);
    if (p.alvo && !alvoAtual) { await new Promise(r => setTimeout(r, 600)); alvoAtual = q(p.alvo); }   // tela ainda montando
    if (alvoAtual) { try { alvoAtual.scrollIntoView({ block: 'center', inline: 'nearest' }); } catch (e) { } }
    const ult = i === roteiro.passos.length - 1, pri = i === 0;
    el.balao.innerHTML = '<div class="gg-seta"></div><button class="gg-sair" title="Sair (Esc)">✕</button>'
      + '<h3>' + esc(p.titulo || roteiro.titulo) + '</h3><p>' + p.texto + '</p>'
      + '<div class="gg-pe"><span class="gg-n">' + (i + 1) + ' de ' + roteiro.passos.length + '</span>'
      + (pri ? '' : '<button class="gg-ant">‹ Anterior</button>')
      + '<button class="gg-prox">' + (ult ? 'Concluir' : (pri ? (p.botao || 'Começar demonstração') : 'Próximo ›')) + '</button></div>';
    el.balao.querySelector('.gg-sair').onclick = sair;
    el.balao.querySelector('.gg-prox').onclick = ult ? sair : proximo;
    const b = el.balao.querySelector('.gg-ant'); if (b) b.onclick = anterior;
    setTimeout(posicionar, 30); setTimeout(posicionar, 400);
    falar(p.texto);
    if (p.bloquear === false && alvoAtual) { const h = () => { alvoAtual.removeEventListener('click', h); setTimeout(proximo, 400); }; alvoAtual.addEventListener('click', h); }
  }
  function proximo() { if (roteiro && i < roteiro.passos.length - 1) mostrar(i + 1); else sair(); }
  function anterior() { if (roteiro && i > 0) mostrar(i - 1); }
  function sair() {
    parar();
    const fim = roteiro && roteiro.aoSair; roteiro = null;
    ['bloq', 'sombra', 'balao', 'ctl'].forEach(k => { if (el[k]) el[k].remove(); }); el = {};
    window.removeEventListener('resize', posicionar); window.removeEventListener('scroll', posicionar, true); document.removeEventListener('keydown', teclas);
    try { if (fim) fim(); } catch (e) { }
  }
  function iniciar(r) { if (roteiro) sair(); roteiro = r; montar(); mostrar(0); }
  if ('speechSynthesis' in window) speechSynthesis.getVoices();   // carrega as vozes
  window.GarraGuia = { iniciar, sair, proximo, anterior, ativo: () => !!roteiro };
})();
