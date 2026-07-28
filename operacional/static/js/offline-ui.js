/**
 * offline-ui.js — UI feedback para GarraDB
 * Gerencia badges, toasts, e eventos de sincronização
 * 
 * Adicionar antes de cualquer outro JS de app:
 * <script src="./js/offline-ui.js"></script>
 */

// ============================================================
// TOAST — Notificações temporárias
// ============================================================

function toast(message, type = 'info', duration = 3000) {
  const toastContainer = document.getElementById('toast-container') || criarToastContainer();
  
  const toast = document.createElement('div');
  toast.className = `toast toast-${type}`;
  toast.innerHTML = `
    <div style="display: flex; align-items: center; gap: 8px;">
      <span>${getIconForType(type)}</span>
      <span>${message}</span>
    </div>
  `;

  toastContainer.appendChild(toast);

  // Estilo
  Object.assign(toast.style, {
    background: getColorForType(type, 'bg'),
    color: getColorForType(type, 'text'),
    border: `1px solid ${getColorForType(type, 'border')}`,
    borderRadius: '8px',
    padding: '12px 16px',
    marginBottom: '8px',
    fontSize: '13px',
    fontWeight: '500',
    boxShadow: '0 2px 8px rgba(0,0,0,.12)',
    animation: 'slideIn .3s ease-out'
  });

  // Auto remove
  setTimeout(() => {
    toast.style.animation = 'slideOut .3s ease-in forwards';
    setTimeout(() => toast.remove(), 300);
  }, duration);
}

function criarToastContainer() {
  const container = document.createElement('div');
  container.id = 'toast-container';
  Object.assign(container.style, {
    position: 'fixed',
    bottom: '16px',
    right: '16px',
    zIndex: '2000',
    maxWidth: '300px'
  });
  document.body.appendChild(container);

  // CSS de animação
  const style = document.createElement('style');
  style.innerHTML = `
    @keyframes slideIn {
      from { transform: translateX(400px); opacity: 0; }
      to { transform: translateX(0); opacity: 1; }
    }
    @keyframes slideOut {
      from { transform: translateX(0); opacity: 1; }
      to { transform: translateX(400px); opacity: 0; }
    }
  `;
  document.head.appendChild(style);

  return container;
}

function getIconForType(type) {
  const icons = {
    'success': '✓',
    'error': '❌',
    'info': 'ℹ️',
    'warning': '⚠️'
  };
  return icons[type] || icons['info'];
}

function getColorForType(type, part) {
  const colors = {
    'success': { bg: '#DCFCE7', text: '#16A34A', border: '#86EFAC' },
    'error': { bg: '#FEE2E2', text: '#DC2626', border: '#FECACA' },
    'info': { bg: '#DBEAFE', text: '#0284C7', border: '#BAE6FD' },
    'warning': { bg: '#FFF7ED', text: '#EA580C', border: '#FED7AA' }
  };
  return colors[type]?.[part] || colors['info'][part];
}

// ============================================================
// BADGE SINCRONIZANDO — visibilidade e animação
// ============================================================

function mostrarBadgeSincronizando() {
  const badge = document.getElementById('badge-sincronizando');
  if (badge) {
    badge.style.display = 'flex';
    badge.style.animation = 'fadeIn .3s ease-in';
  }
}

function esconderBadgeSincronizando() {
  const badge = document.getElementById('badge-sincronizando');
  if (badge) {
    badge.style.animation = 'fadeOut .3s ease-out forwards';
    setTimeout(() => {
      badge.style.display = 'none';
    }, 300);
  }
}

// ============================================================
// QUEUE COUNTER — mostrar quantidade de itens pendentes
// ============================================================

async function atualizarQueueBadges() {
  if (!GarraDB || !GarraDB.getQueue) return;

  try {
    const queue = await GarraDB.getQueue();
    const pendentes = queue.filter(item => item.status === 'pending').length;

    const countBadge = document.getElementById('queue-count');
    if (countBadge) {
      if (pendentes > 0) {
        countBadge.textContent = pendentes > 9 ? '9+' : pendentes;
        countBadge.style.display = 'flex';
      } else {
        countBadge.style.display = 'none';
      }
    }

    // Mostrar badge sincronizando se há itens
    if (pendentes > 0 && !navigator.onLine) {
      mostrarBadgeSincronizando();
    } else if (pendentes === 0) {
      esconderBadgeSincronizando();
    }
  } catch (err) {
    console.warn('[OfflineUI] Erro ao atualizar badges:', err);
  }
}

// ============================================================
// EVENT LISTENERS — sincronização
// ============================================================

// Quando sync completa com sucesso
window.addEventListener('garradb:synced', (e) => {
  const { url, method } = e.detail;
  console.log(`[OfflineUI] ✓ Sincronizado: ${method} ${url}`);

  // Feedback visual
  setTimeout(async () => {
    await atualizarQueueBadges();
    
    // Se fila vazia, esconder badge
    const queue = await GarraDB.getQueue();
    const pendentes = queue.filter(item => item.status === 'pending').length;
    if (pendentes === 0) {
      esconderBadgeSincronizando();
      toast('✓ Tudo sincronizado!', 'success', 2000);
    }
  }, 500);
});

// Quando sync falha permanentemente
window.addEventListener('garradb:failed', (e) => {
  const { url, method, attempts } = e.detail;
  console.error(`[OfflineUI] ✗ Falha permanente: ${method} ${url}`);
  
  toast(
    `❌ Erro ao sincronizar após ${attempts} tentativas. Tente novamente mais tarde.`,
    'error',
    5000
  );
});

// Detecção online/offline
window.addEventListener('online', () => {
  console.log('[OfflineUI] ✓ Online detectado');
  toast('✓ Conexão restaurada - sincronizando...', 'success', 2000);
  
  // Tentar sincronizar
  if (GarraDB && GarraDB.syncPendentes) {
    setTimeout(() => GarraDB.syncPendentes(), 500);
  }
});

window.addEventListener('offline', () => {
  console.log('[OfflineUI] ✗ Offline detectado');
  toast('⚠️ Você está offline. Os registros serão salvos localmente.', 'warning', 3000);
  mostrarBadgeSincronizando();
});

// ============================================================
// INIT — executar ao carregar página
// ============================================================

document.addEventListener('DOMContentLoaded', async () => {
  console.log('[OfflineUI] Inicializando...');

  // Esperar GarraDB inicializar
  let attempts = 0;
  while (!GarraDB || !GarraDB.db) {
    if (attempts > 10) {
      console.warn('[OfflineUI] GarraDB não disponível');
      return;
    }
    await new Promise(r => setTimeout(r, 100));
    attempts++;
  }

  // Atualizar badges iniciais
  await atualizarQueueBadges();

  // Monitorar fila a cada 5s (fallback)
  setInterval(atualizarQueueBadges, 5000);

  // Se há itens na fila e está offline, mostrar badge
  if (!navigator.onLine) {
    const queue = await GarraDB.getQueue();
    if (queue.filter(i => i.status === 'pending').length > 0) {
      mostrarBadgeSincronizando();
    }
  }

  console.log('[OfflineUI] Pronto');
});

// ============================================================
// DEBUG — console methods
// ============================================================

window.DEBUG_OFFLINE = {
  fila: async () => {
    const queue = await GarraDB.getQueue();
    console.table(queue);
  },
  limpar: async () => {
    await GarraDB.clearQueue();
    console.log('Fila limpa');
  },
  status: async () => {
    const queue = await GarraDB.getQueue();
    const pendentes = queue.filter(i => i.status === 'pending').length;
    console.log({
      online: navigator.onLine,
      pendentes,
      total: queue.length,
      isSyncing: GarraDB.isSyncing
    });
  },
  sync: async () => {
    await GarraDB.syncPendentes();
    console.log('Sincronização iniciada');
  }
};

console.log('[OfflineUI] Debug: window.DEBUG_OFFLINE.fila() / status() / sync() / limpar()');

/* ══════════════════════════════════════════════════════════════
   PAINEL DE FALHAS DE ENVIO (24/07/2026)
   Nada morre em silêncio: ao abrir o app, itens com falha permanente
   aparecem num banner com Reenviar / Descartar por item. Reenvio é
   seguro (client_id idempotente no servidor); Descartar é para quando
   o registro já foi lançado manualmente pelo Admin (evita duplicar).
   ══════════════════════════════════════════════════════════════ */
function _falhaResumo(item) {
  try {
    const b = JSON.parse(item.body || '{}');
    const partes = [];
    if (b.obra) partes.push('Nova OS: ' + b.obra);
    if (b.data) partes.push(b.data.split('-').reverse().join('/'));
    if (b.tipo_medicao) partes.push(b.tipo_medicao);
    if (b.qtd_viagens > 0) partes.push(b.qtd_viagens + ' viag.');
    if (b.qtd_metros > 0) partes.push(b.qtd_metros + ' m');
    if (b.equipamento_terceiro) partes.push('🚚 ' + b.equipamento_terceiro);
    if (b.hora_inicio && b.hora_fim) partes.push(b.hora_inicio.slice(0,5) + '–' + b.hora_fim.slice(0,5));
    return partes.join(' · ') || (item.method + ' ' + (item.url||'').split('/').slice(-2).join('/'));
  } catch (e) {
    return item.method + ' ' + (item.url||'').split('/').slice(-2).join('/');
  }
}

async function renderPainelFalhas() {
  if (!window.GarraDB || !GarraDB.listarFalhas) return;
  let falhas = [];
  try { falhas = await GarraDB.listarFalhas(); } catch (e) { return; }
  let el = document.getElementById('painel-falhas-envio');
  if (!falhas.length) { if (el) el.remove(); return; }
  if (!el) {
    el = document.createElement('div');
    el.id = 'painel-falhas-envio';
    el.style.cssText = 'position:fixed;left:12px;right:12px;bottom:70px;z-index:9500;background:#FFF7ED;border:2px solid #FB923C;border-radius:12px;padding:12px;box-shadow:0 8px 24px rgba(0,0,0,.18);max-height:45vh;overflow:auto';
    document.body.appendChild(el);
  }
  el.innerHTML = `
    <div style="font-weight:800;font-size:14px;color:#9A3412;margin-bottom:2px">⚠️ ${falhas.length} registro(s) não enviado(s)</div>
    <div style="font-size:11px;color:#9A3412;margin-bottom:10px">Falharam após várias tentativas. Reenvie — ou descarte se já foi lançado pelo escritório (evita duplicar).</div>
    ${falhas.map(f => `
      <div style="display:flex;align-items:center;gap:8px;background:#fff;border:1px solid #FED7AA;border-radius:8px;padding:8px 10px;margin-bottom:6px">
        <div style="flex:1;font-size:12px;font-weight:600;color:#1A2A5E">${_falhaResumo(f)}</div>
        <button onclick="_falhaReenviar(${f.id})" style="background:#16A34A;color:#fff;border:none;border-radius:6px;padding:6px 10px;font-size:12px;font-weight:700">↻ Reenviar</button>
        <button onclick="_falhaDescartar(${f.id})" style="background:#fff;color:#DC2626;border:1.5px solid #DC2626;border-radius:6px;padding:6px 10px;font-size:12px;font-weight:700">✕</button>
      </div>`).join('')}`;
}

async function _falhaReenviar(id) {
  await GarraDB.reenviarFalha(id);
  toast('Reenviando registro...', 'info', 3000);
  setTimeout(renderPainelFalhas, 4000);
}

async function _falhaDescartar(id) {
  if (!confirm('Descartar este registro?\n\nUse apenas se ele JÁ foi lançado pelo escritório — descartar apaga do aparelho sem enviar.')) return;
  await GarraDB.descartarFalha(id);
  toast('Registro descartado', 'info', 2500);
  renderPainelFalhas();
}

// Ao abrir o app e após cada ciclo de sync, reavaliar o painel
window.addEventListener('load', () => setTimeout(renderPainelFalhas, 1500));
window.addEventListener('garradb:synced', () => setTimeout(renderPainelFalhas, 500));
window.addEventListener('garradb:failed', () => setTimeout(renderPainelFalhas, 500));
