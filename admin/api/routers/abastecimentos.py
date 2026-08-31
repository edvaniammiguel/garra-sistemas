# -*- coding: utf-8 -*-
"""routers.abastecimentos — RECONSTRUÍDO 29/08/2026 (original perdido do repo).

Registro de abastecimentos com leitura de fotos (painel + bomba) via Claude API,
cascata de leitura em 4 níveis e alimentação de horimetro_atual/km_atual — a
leitura que o FMD e as montagens de subsistemas consomem.

Cascata da leitura aplicada (primeiro disponível vence):
  1. digitada   — valor informado pelo colaborador
  2. foto       — extraído da foto do painel (Claude API)
  3. ultima     — última leitura conhecida (abastecimentos + partes diárias)
  4. equipamento— horimetro_atual/km_atual do cadastro

Flags de divergência (nunca bloqueiam, sempre sinalizam):
  - divergencia_leitura: aplicada menor que a última conhecida, ou digitada×foto
    com diferença relevante
  - divergencia_placa: placa lida na foto ≠ placa do equipamento escolhido

Autocontido: página mobile em GET /abastecimento; montar no main.py com 2 linhas:
  from routers.abastecimentos import router as abastecimentos_router
  app.include_router(abastecimentos_router)
Env: ANTHROPIC_API_KEY (obrigatória p/ fotos; sem ela o módulo funciona manual).
"""

import os, re, json, base64, asyncio, uuid as uuid_lib
from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File
from fastapi.responses import HTMLResponse

from core.db import ajard_query
from core.auth import verificar_token
from core.storage import storage_upload

router = APIRouter()

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODELO_VISAO = os.environ.get("ABASTECIMENTO_MODELO", "claude-haiku-4-5-20251001")

# ── DDL idempotente (tolera tabela pré-existente de versão anterior) ──
_DDL_OK = False

_COLUNAS = [
    ("equipamento_id", "UUID"),
    ("data", "TIMESTAMPTZ DEFAULT now()"),
    ("litros", "NUMERIC(10,2)"),
    ("valor_total", "NUMERIC(12,2)"),
    ("leitura", "NUMERIC(12,1)"),
    ("leitura_fonte", "TEXT"),
    ("medicao", "TEXT"),
    ("leitura_digitada", "NUMERIC(12,1)"),
    ("leitura_foto", "NUMERIC(12,1)"),
    ("litros_foto", "NUMERIC(10,2)"),
    ("valor_foto", "NUMERIC(12,2)"),
    ("placa_foto", "TEXT"),
    ("divergencia_leitura", "BOOLEAN DEFAULT false"),
    ("divergencia_placa", "BOOLEAN DEFAULT false"),
    ("foto_painel", "TEXT"),
    ("foto_bomba", "TEXT"),
    ("usuario_id", "UUID"),
    ("usuario_nome", "TEXT"),
    ("observacao", "TEXT"),
    ("criado_em", "TIMESTAMPTZ DEFAULT now()"),
    ("ativo", "BOOLEAN DEFAULT true"),
]

async def _ddl():
    global _DDL_OK
    if _DDL_OK:
        return
    await ajard_query(
        """CREATE TABLE IF NOT EXISTS operacional.abastecimentos (
             id UUID PRIMARY KEY DEFAULT gen_random_uuid())""", fetch="none")
    for col, tipo in _COLUNAS:
        await ajard_query(
            f"ALTER TABLE operacional.abastecimentos ADD COLUMN IF NOT EXISTS {col} {tipo}",
            fetch="none")
    await ajard_query(
        """CREATE INDEX IF NOT EXISTS idx_abast_equip
           ON operacional.abastecimentos (equipamento_id, data DESC)""", fetch="none")
    _DDL_OK = True


# ── Helpers ──────────────────────────────────────────────────────────
def _uid(payload):
    """usuario_id só se for UUID válido — o sub do JWT pode ser o login (texto)."""
    for chave in ("usuario_id", "sub", "id"):
        v = payload.get(chave)
        if v:
            try:
                return str(uuid_lib.UUID(str(v)))
            except (ValueError, AttributeError, TypeError):
                continue
    return None


def _norm_placa(p):
    """Normalização de placa: maiúsculas, só A-Z0-9 (GVP-8969 ≡ gvp 8969)."""
    v = re.sub(r"[^A-Z0-9]", "", (p or "").upper())
    return v or None


def _num(v):
    if v is None or v == "":
        return None
    try:
        return float(str(v).replace(".", "").replace(",", ".")) if isinstance(v, str) and "," in str(v) else float(v)
    except (TypeError, ValueError):
        return None


async def _equip(eq_id):
    r = await ajard_query(
        """SELECT id, codigo, descricao, medicao, placa,
                  horimetro_atual, km_atual
           FROM operacional.equipamentos WHERE id=%s""", (eq_id,))
    if not r:
        raise HTTPException(status_code=404, detail="Equipamento não encontrado")
    return dict(r[0])


async def _ultima_leitura(eq):
    """Nível 3 da cascata: maior leitura conhecida em abastecimentos e
    partes diárias, respeitando a medição do equipamento."""
    campo_parte = "km_final" if (eq.get("medicao") == "km") else "horimetro_final"
    r = await ajard_query(
        f"""SELECT GREATEST(
              COALESCE((SELECT MAX(leitura) FROM operacional.abastecimentos
                        WHERE equipamento_id=%s AND ativo=true), 0),
              COALESCE((SELECT MAX({campo_parte}) FROM operacional.partes_diarias
                        WHERE equipamento_id=%s), 0)) AS ultima""",
        (eq["id"], eq["id"]))
    v = r and r[0]["ultima"]
    return float(v) if v else None


def _cascata(digitada, foto, ultima, cadastro):
    """Devolve (leitura_aplicada, fonte). Primeiro nível disponível vence."""
    if digitada is not None:
        return digitada, "digitada"
    if foto is not None:
        return foto, "foto"
    if ultima:
        return ultima, "ultima"
    if cadastro is not None:
        return float(cadastro), "equipamento"
    return None, None


# ── Claude API (visão) — chamada síncrona rodada em thread p/ paralelismo ──
_PROMPT_PAINEL = (
    "Você está lendo a foto do painel de um equipamento pesado (caminhão ou "
    "máquina de construção). Extraia, se visível: a leitura do horímetro ou "
    "odômetro (número mostrado no mostrador) e a placa do veículo. Responda "
    "SOMENTE com JSON válido, sem markdown: "
    '{"leitura": numero ou null, "tipo": "horimetro" ou "km" ou null, '
    '"placa": "ABC1D23" ou null}')

_PROMPT_BOMBA = (
    "Você está lendo a foto do visor de uma bomba de combustível. Extraia, se "
    "visível: litros abastecidos e valor total em reais. Responda SOMENTE com "
    'JSON válido, sem markdown: {"litros": numero ou null, "valor": numero ou null}')


def _claude_ler(img_bytes: bytes, mime: str, prompt: str) -> dict:
    import requests as req_lib
    if not ANTHROPIC_API_KEY:
        return {"_erro": "ANTHROPIC_API_KEY ausente no ambiente"}
    try:
        r = req_lib.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": ANTHROPIC_API_KEY,
                     "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": MODELO_VISAO, "max_tokens": 300,
                  "messages": [{"role": "user", "content": [
                      {"type": "image",
                       "source": {"type": "base64", "media_type": mime,
                                  "data": base64.b64encode(img_bytes).decode()}},
                      {"type": "text", "text": prompt}]}]},
            timeout=45)
        r.raise_for_status()
        texto = "".join(b.get("text", "") for b in r.json().get("content", [])
                        if b.get("type") == "text")
        texto = texto.replace("```json", "").replace("```", "").strip()
        return json.loads(texto)
    except Exception as e:  # extração nunca derruba o registro manual
        return {"_erro": str(e)[:200]}


# ── PÁGINA MOBILE ────────────────────────────────────────────────────
@router.get("/abastecimento", response_class=HTMLResponse)
async def pagina_abastecimento():
    return _PAGINA


# ── API ──────────────────────────────────────────────────────────────
@router.post("/operacional/api/abastecimentos/extrair")
async def extrair_fotos(foto_painel: UploadFile = File(None),
                        foto_bomba: UploadFile = File(None),
                        payload=Depends(verificar_token)):
    """Sobe as fotos ao Storage e extrai leituras via Claude, em paralelo."""
    await _ddl()
    if not foto_painel and not foto_bomba:
        raise HTTPException(status_code=400, detail="Envie ao menos uma foto")

    from datetime import datetime
    pasta = "abastecimentos/" + datetime.now().strftime("%Y-%m")
    tarefas, chaves, paths = [], [], {}

    for chave, up, prompt in (("painel", foto_painel, _PROMPT_PAINEL),
                              ("bomba", foto_bomba, _PROMPT_BOMBA)):
        if not up:
            continue
        dados = await up.read()
        if not dados:
            continue
        mime = up.content_type or "image/jpeg"
        path = f"{pasta}/{chave}-{os.urandom(6).hex()}.jpg"
        try:
            storage_upload(dados, path, mime)
            paths[chave] = path
        except Exception:
            paths[chave] = None  # storage fora não impede a extração
        tarefas.append(asyncio.to_thread(_claude_ler, dados, mime, prompt))
        chaves.append(chave)

    resultados = await asyncio.gather(*tarefas) if tarefas else []
    saida = {"paths": paths}
    for chave, res in zip(chaves, resultados):
        saida[chave] = res
    if "painel" in saida and saida["painel"].get("placa"):
        saida["painel"]["placa"] = _norm_placa(saida["painel"]["placa"])
    return saida


@router.post("/operacional/api/abastecimentos")
async def registrar_abastecimento(request: Request, payload=Depends(verificar_token)):
    await _ddl()
    d = await request.json()
    eq_id = d.get("equipamento_id")
    if not eq_id:
        raise HTTPException(status_code=400, detail="equipamento_id obrigatório")
    eq = await _equip(eq_id)

    litros = _num(d.get("litros")) or _num(d.get("litros_foto"))
    valor = _num(d.get("valor_total")) or _num(d.get("valor_foto"))
    digitada = _num(d.get("leitura_digitada"))
    foto = _num(d.get("leitura_foto"))
    ultima = await _ultima_leitura(eq)
    cadastro = eq.get("km_atual") if eq.get("medicao") == "km" else eq.get("horimetro_atual")

    leitura, fonte = _cascata(digitada, foto, ultima, cadastro)

    div_leitura = False
    if leitura is not None and ultima and leitura < ultima:
        div_leitura = True
    if digitada is not None and foto is not None and foto:
        if abs(digitada - foto) > max(0.05 * foto, 1):
            div_leitura = True

    placa_foto = _norm_placa(d.get("placa_foto"))
    placa_eq = _norm_placa(eq.get("placa"))
    div_placa = bool(placa_foto and placa_eq and placa_foto != placa_eq)

    r = await ajard_query(
        """INSERT INTO operacional.abastecimentos
             (equipamento_id, litros, valor_total, leitura, leitura_fonte, medicao,
              leitura_digitada, leitura_foto, litros_foto, valor_foto, placa_foto,
              divergencia_leitura, divergencia_placa, foto_painel, foto_bomba,
              usuario_id, usuario_nome, observacao)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
           RETURNING id, leitura, leitura_fonte, divergencia_leitura, divergencia_placa""",
        (eq_id, litros, valor, leitura, fonte, eq.get("medicao"),
         digitada, foto, _num(d.get("litros_foto")), _num(d.get("valor_foto")),
         placa_foto, div_leitura, div_placa,
         d.get("foto_painel"), d.get("foto_bomba"),
         _uid(payload), payload.get("nome") or payload.get("login"),
         (d.get("observacao") or "").strip() or None))

    # Alimenta a leitura viva do equipamento (FMD, montagens) — só avança, nunca recua
    if leitura is not None and not div_leitura:
        campo = "km_atual" if eq.get("medicao") == "km" else "horimetro_atual"
        await ajard_query(
            f"""UPDATE operacional.equipamentos
                SET {campo}=%s, atualizado_em=now()
                WHERE id=%s AND COALESCE({campo},0) < %s""",
            (leitura, eq_id, leitura), fetch="none")

    reg = dict(r[0])
    reg["leitura"] = float(reg["leitura"]) if reg["leitura"] is not None else None
    reg["equipamento"] = eq["codigo"]
    return reg


@router.get("/operacional/api/abastecimentos")
async def listar_abastecimentos(equipamento_id: str = None, limite: int = 30,
                                payload=Depends(verificar_token)):
    await _ddl()
    limite = max(1, min(int(limite or 30), 200))
    filtro, params = "", []
    if equipamento_id:
        filtro = "AND a.equipamento_id=%s"
        params.append(equipamento_id)
    r = await ajard_query(
        f"""SELECT a.id, a.data, a.litros, a.valor_total, a.leitura,
                   a.leitura_fonte, a.medicao, a.divergencia_leitura,
                   a.divergencia_placa, a.usuario_nome, a.observacao,
                   e.codigo AS equipamento, e.descricao AS equipamento_desc
            FROM operacional.abastecimentos a
            LEFT JOIN operacional.equipamentos e ON e.id=a.equipamento_id
            WHERE a.ativo=true {filtro}
            ORDER BY a.data DESC LIMIT {limite}""", tuple(params) or None)
    out = []
    for x in (r or []):
        x = dict(x)
        for k in ("litros", "valor_total", "leitura"):
            x[k] = float(x[k]) if x[k] is not None else None
        x["data"] = x["data"].isoformat() if x.get("data") else None
        out.append(x)
    return out


@router.get("/operacional/api/abastecimentos/ultima/{eq_id}")
async def ultima_leitura(eq_id: str, payload=Depends(verificar_token)):
    await _ddl()
    eq = await _equip(eq_id)
    ultima = await _ultima_leitura(eq)
    cadastro = eq.get("km_atual") if eq.get("medicao") == "km" else eq.get("horimetro_atual")
    return {"equipamento": eq["codigo"], "medicao": eq.get("medicao"),
            "ultima_conhecida": ultima,
            "cadastro": float(cadastro) if cadastro is not None else None,
            "placa": eq.get("placa")}


@router.delete("/operacional/api/abastecimentos/{ab_id}")
async def excluir_abastecimento(ab_id: str, payload=Depends(verificar_token)):
    await _ddl()
    if (payload.get("perfil") or "").lower() not in ("admin", "gestor"):
        raise HTTPException(status_code=403, detail="Só gestão exclui abastecimento")
    await ajard_query(
        "UPDATE operacional.abastecimentos SET ativo=false WHERE id=%s",
        (ab_id,), fetch="none")
    return {"ok": True}


# ── PÁGINA (inline — autocontida, padrão pedido-mobile) ──────────────
_PAGINA = r"""<!DOCTYPE html><html lang="pt-BR"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Garra — Abastecimento</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui,sans-serif;background:#F1F5F9;color:#0F172A;font-size:14px;padding:12px;max-width:520px;margin:0 auto}
h1{font-size:16px;color:#1A2A5E;margin:6px 0 12px}h1 span{color:#E8820C}
.card{background:#fff;border:1px solid #E2E8F0;border-radius:10px;padding:14px;margin-bottom:10px}
label{display:block;font-size:11px;color:#475569;font-weight:600;margin:8px 0 3px}
input,select,textarea{width:100%;padding:9px;border:1px solid #CBD5E1;border-radius:7px;font-size:14px;background:#fff}
input.lido{border-color:#E8820C;background:#FFF7ED}
.btn{width:100%;padding:11px;border:none;border-radius:8px;font-size:14px;font-weight:700;cursor:pointer;margin-top:10px}
.btn-p{background:#1A2A5E;color:#fff}.btn-o{background:#E8820C;color:#fff}.btn-c{background:#E2E8F0;color:#0F172A}
.row{display:flex;gap:8px}.row>*{flex:1}
.muted{font-size:11px;color:#64748B}
.flag{font-size:12px;padding:7px 10px;border-radius:7px;margin-top:8px}
.flag-warn{background:#FEF3C7;color:#92400E}.flag-ok{background:#DCFCE7;color:#166534}
.hist{font-size:12px;border-top:1px solid #E2E8F0;padding:7px 0}
.hist b{color:#1A2A5E}
#toast{position:fixed;bottom:16px;left:50%;transform:translateX(-50%);background:#0F172A;color:#fff;padding:9px 16px;border-radius:8px;font-size:13px;display:none;z-index:99;max-width:92%}
.hide{display:none}
</style></head><body>
<script>(function(){ try {
  const q = new URLSearchParams(location.search);
  const sso = q.get('sso');
  if (sso && sso.length > 10) { localStorage.setItem('garra_tok_abast', sso); }
  if (q.get('embedded') === '1') document.documentElement.classList.add('embedded');
  if (sso || q.get('embedded')) history.replaceState(null, '', location.pathname);
} catch(e) {} })();</script>
<style>.embedded h1{display:none !important}.embedded body{padding-top:6px}</style>
<h1>⛽ Abastecimento <span>Garra</span></h1>

<div class="card" id="tela-login">
  <b>Entrar</b><div class="muted">Use o mesmo login dos aplicativos Garra.</div>
  <label>Login</label><input id="lg-user" autocomplete="username">
  <label>Senha</label><input id="lg-senha" type="password" autocomplete="current-password">
  <button class="btn btn-p" onclick="entrar()">Entrar</button>
</div>

<div class="card hide" id="tela-form">
  <label>Equipamento *</label><select id="f-eq" onchange="carregarUltima()"></select>
  <div class="muted" id="f-info"></div>
  <div class="row">
    <div><label>📷 Foto do painel (horímetro/KM)</label><input type="file" id="f-foto-painel" accept="image/*" capture="environment"></div>
  </div>
  <div class="row">
    <div><label>📷 Foto da bomba (litros/valor)</label><input type="file" id="f-foto-bomba" accept="image/*" capture="environment"></div>
  </div>
  <button class="btn btn-o" onclick="lerFotos()">🔍 Ler fotos</button>
  <div class="row">
    <div><label>Litros</label><input id="f-litros" inputmode="decimal" placeholder="ex.: 180,5"></div>
    <div><label>Valor total (R$)</label><input id="f-valor" inputmode="decimal" placeholder="ex.: 1080,00"></div>
  </div>
  <label>Leitura do horímetro/KM <span class="muted">(vazio = usa a foto ou a última conhecida)</span></label>
  <input id="f-leitura" inputmode="decimal" placeholder="digite só se a foto falhar">
  <label>Observação</label><input id="f-obs" placeholder="opcional">
  <button class="btn btn-p" onclick="salvar()">💾 Registrar abastecimento</button>
  <div id="f-resultado"></div>
</div>

<div class="card hide" id="tela-hist">
  <b>Últimos registros</b><div id="hist"></div>
</div>

<div id="toast"></div>
<script>
const API = location.origin;
let TOK = localStorage.getItem('garra_tok_abast') || '';
let EXTR = {};

function toast(m){ const t=document.getElementById('toast'); t.textContent=m; t.style.display='block'; setTimeout(()=>t.style.display='none',3200); }
function mostrar(id,on){ document.getElementById(id).classList[on?'remove':'add']('hide'); }

async function api(path, opts){
  opts = opts || {};
  opts.headers = Object.assign({'Authorization':'Bearer '+TOK}, opts.headers||{});
  const r = await fetch(API+path, opts);
  if (r.status===401){ TOK=''; localStorage.removeItem('garra_tok_abast'); mostrar('tela-login',true); mostrar('tela-form',false); mostrar('tela-hist',false); throw new Error('Sessão expirada — entre de novo'); }
  if (!r.ok){ let d; try{ d=await r.json(); }catch(e){} throw new Error((d&&d.detail)||('Erro '+r.status)); }
  return r.json();
}

async function entrar(){
  const login=document.getElementById('lg-user').value.trim();
  const senha=document.getElementById('lg-senha').value;
  if(!login||!senha){ toast('Preencha login e senha'); return; }
  try{
    const r=await fetch(API+'/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({login:login,senha:senha})});
    if(!r.ok) throw new Error('Login inválido');
    const d=await r.json(); TOK=d.token||d.access_token;
    localStorage.setItem('garra_tok_abast',TOK);
    iniciar();
  }catch(e){ toast('❌ '+e.message); }
}

async function iniciar(){
  mostrar('tela-login',false); mostrar('tela-form',true); mostrar('tela-hist',true);
  try{
    const eqs=await api('/operacional/api/equipamentos');
    const sel=document.getElementById('f-eq');
    sel.innerHTML='<option value="">— escolha —</option>'+eqs
      .filter(e=>e.ativo!==false && (e.categoria||'')!=='componente' && (e.categoria||'')!=='apoio')
      .map(e=>'<option value="'+e.id+'">'+e.codigo+' — '+(e.descricao||'')+'</option>').join('');
    carregarHist();
  }catch(e){ toast('❌ '+e.message); }
}

async function carregarUltima(){
  const id=document.getElementById('f-eq').value;
  const info=document.getElementById('f-info'); info.textContent='';
  if(!id) return;
  try{
    const u=await api('/operacional/api/abastecimentos/ultima/'+id);
    info.textContent='Última leitura conhecida: '+(u.ultima_conhecida!=null?u.ultima_conhecida.toLocaleString('pt-BR'):'—')
      +(u.medicao==='km'?' km':' h')+(u.placa?' · placa '+u.placa:'');
  }catch(e){}
}

async function lerFotos(){
  const fp=document.getElementById('f-foto-painel').files[0];
  const fb=document.getElementById('f-foto-bomba').files[0];
  if(!fp&&!fb){ toast('Escolha ao menos uma foto'); return; }
  toast('🔍 Lendo fotos…');
  const fd=new FormData();
  if(fp) fd.append('foto_painel',fp);
  if(fb) fd.append('foto_bomba',fb);
  try{
    const r=await api('/operacional/api/abastecimentos/extrair',{method:'POST',body:fd});
    EXTR=r;
    let msg=[];
    if(r.painel&&r.painel.leitura!=null){ const c=document.getElementById('f-leitura'); c.value=r.painel.leitura; c.classList.add('lido'); msg.push('leitura '+r.painel.leitura); }
    if(r.painel&&r.painel._erro) msg.push('painel: '+r.painel._erro);
    if(r.bomba&&r.bomba.litros!=null){ const c=document.getElementById('f-litros'); c.value=r.bomba.litros; c.classList.add('lido'); msg.push(r.bomba.litros+' L'); }
    if(r.bomba&&r.bomba.valor!=null){ const c=document.getElementById('f-valor'); c.value=r.bomba.valor; c.classList.add('lido'); msg.push('R$ '+r.bomba.valor); }
    if(r.bomba&&r.bomba._erro) msg.push('bomba: '+r.bomba._erro);
    toast(msg.length?('📖 Lido: '+msg.join(' · ')+' — CONFIRA antes de salvar'):'Nada legível nas fotos — preencha manual');
  }catch(e){ toast('❌ '+e.message); }
}

async function salvar(){
  const eq=document.getElementById('f-eq').value;
  if(!eq){ toast('Escolha o equipamento'); return; }
  const corpo={
    equipamento_id: eq,
    litros: document.getElementById('f-litros').value||null,
    valor_total: document.getElementById('f-valor').value||null,
    leitura_digitada: document.getElementById('f-leitura').value||null,
    observacao: document.getElementById('f-obs').value||null,
    leitura_foto: EXTR.painel? EXTR.painel.leitura : null,
    litros_foto: EXTR.bomba? EXTR.bomba.litros : null,
    valor_foto: EXTR.bomba? EXTR.bomba.valor : null,
    placa_foto: EXTR.painel? EXTR.painel.placa : null,
    foto_painel: EXTR.paths? EXTR.paths.painel : null,
    foto_bomba: EXTR.paths? EXTR.paths.bomba : null
  };
  // a leitura preenchida pela foto NÃO é "digitada" — só conta como digitada se o usuário alterou
  const campoLeitura=document.getElementById('f-leitura');
  if (EXTR.painel && EXTR.painel.leitura!=null && String(campoLeitura.value)===String(EXTR.painel.leitura))
    corpo.leitura_digitada=null;
  try{
    const r=await api('/operacional/api/abastecimentos',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(corpo)});
    const res=document.getElementById('f-resultado');
    const fontes={digitada:'digitada por você',foto:'lida da foto',ultima:'última conhecida',equipamento:'do cadastro'};
    let html='<div class="flag flag-ok">✅ Registrado em <b>'+r.equipamento+'</b>'
      +(r.leitura!=null?(' · leitura '+r.leitura.toLocaleString('pt-BR')+' ('+(fontes[r.leitura_fonte]||r.leitura_fonte)+')'):'')+'</div>';
    if(r.divergencia_leitura) html+='<div class="flag flag-warn">⚠ Leitura divergente da última conhecida — o cadastro NÃO foi atualizado; a gestão confere.</div>';
    if(r.divergencia_placa) html+='<div class="flag flag-warn">⚠ Placa da foto não bate com o equipamento escolhido — confira se é a máquina certa.</div>';
    res.innerHTML=html;
    ['f-litros','f-valor','f-leitura','f-obs'].forEach(i=>{const c=document.getElementById(i);c.value='';c.classList.remove('lido');});
    document.getElementById('f-foto-painel').value=''; document.getElementById('f-foto-bomba').value='';
    EXTR={}; carregarHist(); carregarUltima();
  }catch(e){ toast('❌ '+e.message); }
}

async function carregarHist(){
  try{
    const r=await api('/operacional/api/abastecimentos?limite=5');
    document.getElementById('hist').innerHTML=(r||[]).map(a=>
      '<div class="hist"><b>'+(a.equipamento||'?')+'</b> · '
      +(a.data? new Date(a.data).toLocaleDateString('pt-BR'):'')
      +(a.litros!=null?' · '+a.litros.toLocaleString('pt-BR')+' L':'')
      +(a.leitura!=null?' · '+a.leitura.toLocaleString('pt-BR')+(a.medicao==='km'?' km':' h'):'')
      +((a.divergencia_leitura||a.divergencia_placa)?' <span style="color:#92400E">⚠</span>':'')
      +'</div>').join('')||'<div class="muted">Nenhum registro ainda.</div>';
  }catch(e){}
}

if (TOK) iniciar();
</script></body></html>"""
