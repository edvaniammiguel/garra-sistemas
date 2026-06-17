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
