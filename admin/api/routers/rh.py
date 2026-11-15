"""routers.rh — Módulo RH (15/11/2026), etapa 1.

Cadastro de colaboradores (pessoa ≠ login), função e custo da hora (HH), NRs e treinamentos
(presencial/EAD, plataforma, certificado, vencimento), logins das plataformas EAD com senha
CRIPTOGRAFADA e painel geral de NRs (quem tem, quem falta, o que vence).

Fonte da verdade:
  • salário e folha ficam no MAIS (o Garra não guarda salário);
  • função e HH ficam aqui (rh.colaboradores) — a Manutenção lê daqui para a mão de obra das OTs;
  • nome, CPF, nascimento, cargo, admissão e demissão vêm do MAIS (importação da planilha de colaboradores
    hoje; API depois), casando por CPF. Endereço, PIS, RG, estado civil etc. ficam só no MAIS (LGPD: o Garra
    guarda o mínimo de que precisa).
  • NRs exigidas são por CARGO (o do MAIS — todo colaborador tem); a função do organograma é só para o HH.

Acesso: módulo 'rh' (completo) · módulo 'rh_nr' (só ver situação de NRs, sem CPF/HH/senhas).
"""
import base64 as _b64
import hashlib as _hl
import io
import os
import re
import uuid as _uuid
from datetime import date as _date

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File
from fastapi.responses import HTMLResponse, StreamingResponse

from core.auth import verificar_token
from core.db import ajard_query
from core.fuso import hoje as _hoje
from core.permissions import tem_modulo
from core.storage import storage_upload, storage_url

router = APIRouter()


# ══════════════════════════════════════════════════════════════
# ACESSO
# ══════════════════════════════════════════════════════════════
async def verificar_rh(payload=Depends(verificar_token)):
    """RH completo (cadastro, HH, treinamentos, senhas das plataformas)."""
    if await tem_modulo(payload, {"rh"}):
        return payload
    raise HTTPException(status_code=403, detail="Sem permissão para o módulo RH")


async def verificar_rh_ver(payload=Depends(verificar_token)):
    """Ver a situação das NRs (rh ou rh_nr)."""
    if await tem_modulo(payload, {"rh", "rh_nr"}):
        return payload
    raise HTTPException(status_code=403, detail="Sem permissão para o módulo RH")


async def _eh_rh(payload):
    return await tem_modulo(payload, {"rh"})


# ══════════════════════════════════════════════════════════════
# CRIPTOGRAFIA DAS SENHAS DAS PLATAFORMAS
# ══════════════════════════════════════════════════════════════
# Chave: variável RH_CHAVE (Fernet, opcional). Sem ela, deriva-se uma chave do JWT_SECRET.
# MultiFernet: grava sempre com a 1ª chave e lê com qualquer uma — trocar/definir RH_CHAVE
# depois não perde as senhas já gravadas (são regravadas com a chave nova ao editar).
def _fernet():
    from cryptography.fernet import Fernet, MultiFernet
    chaves = []
    k = (os.environ.get("RH_CHAVE") or "").strip()
    if k:
        try:
            chaves.append(Fernet(k.encode()))
        except Exception:
            print("⚠ RH_CHAVE inválida (não é uma chave Fernet) — usando a derivada do JWT_SECRET")
    seg = os.environ.get("JWT_SECRET", "")
    if not seg:
        raise HTTPException(status_code=500, detail="JWT_SECRET ausente — não é possível proteger as senhas")
    chaves.append(Fernet(_b64.urlsafe_b64encode(_hl.sha256(("garra-rh-v1:" + seg).encode()).digest())))
    return MultiFernet(chaves)


def _cifrar(txt):
    return _fernet().encrypt(txt.encode()).decode() if txt else None


def _decifrar(tok):
    if not tok:
        return ""
    try:
        return _fernet().decrypt(tok.encode()).decode()
    except Exception:
        raise HTTPException(status_code=500, detail="Não foi possível abrir a senha (chave de criptografia mudou?)")


# ══════════════════════════════════════════════════════════════
# ESQUEMA (idempotente — nasce do código)
# ══════════════════════════════════════════════════════════════
_RH_OK = False


async def garantir_rh():
    global _RH_OK
    if _RH_OK:
        return
    await ajard_query("CREATE SCHEMA IF NOT EXISTS rh", fetch="none")
    await ajard_query("""
        CREATE TABLE IF NOT EXISTS rh.vinculos (
            codigo TEXT PRIMARY KEY, nome TEXT NOT NULL, ordem INT DEFAULT 0, ativo BOOLEAN DEFAULT true)""", fetch="none")
    await ajard_query("""
        INSERT INTO rh.vinculos (codigo, nome, ordem) VALUES
          ('CLT','CLT',1), ('DIA','Diarista',2), ('TER','Terceiro',3), ('SOC','Sócio',4), ('EST','Estagiário',5)
        ON CONFLICT (codigo) DO NOTHING""", fetch="none")
    await ajard_query("""
        CREATE TABLE IF NOT EXISTS rh.colaboradores (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            nome TEXT NOT NULL,
            cpf TEXT,
            matricula_mais TEXT,
            telefone TEXT,
            data_nascimento DATE,
            data_admissao DATE,
            data_desligamento DATE,
            situacao TEXT NOT NULL DEFAULT 'ativo',
            vinculo TEXT DEFAULT 'CLT',
            usuario_id UUID,
            apelido TEXT,
            cargo_codigo TEXT,
            funcao_codigo TEXT,
            custo_hh NUMERIC(12,2),
            mais_sync_em TIMESTAMPTZ,
            observacao TEXT,
            ativo BOOLEAN NOT NULL DEFAULT true,
            criado_por TEXT,
            criado_em TIMESTAMPTZ DEFAULT now(),
            atualizado_em TIMESTAMPTZ DEFAULT now())""", fetch="none")
    for col, tipo in (("apelido", "TEXT"), ("cargo_codigo", "TEXT"), ("mais_sync_em", "TIMESTAMPTZ")):
        await ajard_query(f"ALTER TABLE rh.colaboradores ADD COLUMN IF NOT EXISTS {col} {tipo}", fetch="none")
    await ajard_query("CREATE UNIQUE INDEX IF NOT EXISTS ux_rh_colab_cpf ON rh.colaboradores (cpf) WHERE cpf IS NOT NULL AND ativo", fetch="none")
    await ajard_query("CREATE UNIQUE INDEX IF NOT EXISTS ux_rh_colab_usuario ON rh.colaboradores (usuario_id) WHERE usuario_id IS NOT NULL AND ativo", fetch="none")
    # Catálogo de NRs e outros treinamentos. Validade em meses = reciclagem (vazio = não vence).
    await ajard_query("""
        CREATE TABLE IF NOT EXISTS rh.cursos (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            codigo TEXT NOT NULL, nome TEXT NOT NULL, tipo TEXT NOT NULL DEFAULT 'NR',
            validade_meses INT, carga_horaria NUMERIC(6,1), ordem INT DEFAULT 0,
            ativo BOOLEAN NOT NULL DEFAULT true, criado_em TIMESTAMPTZ DEFAULT now())""", fetch="none")
    await ajard_query("CREATE UNIQUE INDEX IF NOT EXISTS ux_rh_cursos_codigo ON rh.cursos (upper(codigo))", fetch="none")
    # Sugestão inicial — validade só onde a própria NR fixa a reciclagem; o resto a gestão define na tela.
    await ajard_query("""
        INSERT INTO rh.cursos (codigo, nome, tipo, validade_meses, ordem) VALUES
          ('NR-06','Equipamento de Proteção Individual (EPI)','NR',NULL,6),
          ('NR-10','Segurança em instalações e serviços em eletricidade','NR',24,10),
          ('NR-11','Transporte, movimentação, armazenagem e manuseio de materiais','NR',NULL,11),
          ('NR-12','Segurança no trabalho em máquinas e equipamentos','NR',NULL,12),
          ('NR-18','Segurança e saúde no trabalho na indústria da construção','NR',NULL,18),
          ('NR-20','Segurança e saúde no trabalho com inflamáveis e combustíveis','NR',NULL,20),
          ('NR-33','Segurança e saúde nos trabalhos em espaços confinados','NR',12,33),
          ('NR-35','Trabalho em altura','NR',24,35)
        ON CONFLICT DO NOTHING""", fetch="none")
    # Cargos = os do MAIS (código do MAIS como chave); NRs exigidas por cargo.
    await ajard_query("""
        CREATE TABLE IF NOT EXISTS rh.cargos (
            codigo TEXT PRIMARY KEY, nome TEXT NOT NULL, origem TEXT DEFAULT 'mais',
            ativo BOOLEAN NOT NULL DEFAULT true, criado_em TIMESTAMPTZ DEFAULT now())""", fetch="none")
    await ajard_query("""
        CREATE TABLE IF NOT EXISTS rh.cargo_cursos (
            cargo_codigo TEXT NOT NULL, curso_id UUID NOT NULL,
            PRIMARY KEY (cargo_codigo, curso_id))""", fetch="none")
    await ajard_query("""
        CREATE TABLE IF NOT EXISTS rh.plataformas (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            nome TEXT NOT NULL, site TEXT, observacao TEXT,
            ativo BOOLEAN NOT NULL DEFAULT true, criado_em TIMESTAMPTZ DEFAULT now())""", fetch="none")
    await ajard_query("CREATE UNIQUE INDEX IF NOT EXISTS ux_rh_plataformas_nome ON rh.plataformas (upper(nome))", fetch="none")
    await ajard_query("""
        INSERT INTO rh.plataformas (nome) VALUES ('Engehall'), ('Prime Cursos'), ('Previnir')
        ON CONFLICT DO NOTHING""", fetch="none")
    await ajard_query("""
        CREATE TABLE IF NOT EXISTS rh.acessos_plataforma (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            colaborador_id UUID NOT NULL, plataforma_id UUID NOT NULL,
            login TEXT, senha_cripto TEXT, observacao TEXT,
            atualizado_por TEXT, atualizado_em TIMESTAMPTZ DEFAULT now(),
            UNIQUE (colaborador_id, plataforma_id))""", fetch="none")
    await ajard_query("""
        CREATE TABLE IF NOT EXISTS rh.acessos_log (
            id BIGSERIAL PRIMARY KEY, usuario_login TEXT, acao TEXT NOT NULL,
            colaborador_id UUID, plataforma_id UUID, em TIMESTAMPTZ DEFAULT now())""", fetch="none")
    await ajard_query("""
        CREATE TABLE IF NOT EXISTS rh.treinamentos (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            colaborador_id UUID NOT NULL, curso_id UUID NOT NULL,
            situacao TEXT NOT NULL DEFAULT 'concluido',
            modalidade TEXT, plataforma_id UUID, local TEXT, instrutor TEXT,
            data_inicio DATE, data_conclusao DATE, validade_ate DATE,
            carga_horaria NUMERIC(6,1), custo NUMERIC(12,2),
            certificado_path TEXT, certificado_nome TEXT, observacao TEXT,
            ativo BOOLEAN NOT NULL DEFAULT true,
            criado_por TEXT, criado_em TIMESTAMPTZ DEFAULT now(), atualizado_em TIMESTAMPTZ DEFAULT now())""", fetch="none")
    await ajard_query("CREATE INDEX IF NOT EXISTS ix_rh_trein_colab ON rh.treinamentos (colaborador_id) WHERE ativo", fetch="none")
    # Função/HH: o apontamento que a Manutenção guardava por login (manutencao.colaborador_funcao)
    # passa a morar na ficha. Migra uma vez: quem tinha função ganha ficha (é pessoa real).
    try:
        await ajard_query("""
            INSERT INTO rh.colaboradores (nome, usuario_id, funcao_codigo, custo_hh, situacao, criado_por)
            SELECT u.nome, u.id, cf.funcao_codigo, cf.custo_hh,
                   CASE WHEN u.ativo THEN 'ativo' ELSE 'desligado' END, 'migração função/HH'
              FROM manutencao.colaborador_funcao cf
              JOIN public.usuarios_garra u ON u.id = cf.usuario_id
             WHERE NOT EXISTS (SELECT 1 FROM rh.colaboradores c WHERE c.usuario_id = cf.usuario_id AND c.ativo)""",
            fetch="none")
    except Exception as e:   # tabela antiga ainda não existe (banco novo) — nada a migrar
        print("RH: migração função/HH ignorada:", str(e)[:120])
    _RH_OK = True


# ══════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════
_SITUACOES = ("ativo", "afastado", "desligado")
_SIT_TREIN = ("inscrito", "andamento", "concluido")
_MODALIDADES = ("presencial", "ead", "semipresencial")


def _num(v):
    if v in (None, ""):
        return None
    t = str(v).strip().replace("R$", "").replace(" ", "")
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    try:
        return float(t)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Número inválido: {v}")


def _data(v):
    if v in (None, ""):
        return None
    if isinstance(v, _date):
        return v
    try:
        return _date.fromisoformat(str(v)[:10])
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Data inválida: {v}")


def _cpf_limpo(v):
    """Só dígitos; valida os dígitos verificadores. Vazio = None."""
    d = re.sub(r"\D", "", v or "")
    if not d:
        return None
    if len(d) != 11 or d == d[0] * 11:
        raise HTTPException(status_code=422, detail="CPF inválido")
    for n in (9, 10):
        s = sum(int(d[i]) * (n + 1 - i) for i in range(n))
        dv = (s * 10) % 11 % 10
        if dv != int(d[n]):
            raise HTTPException(status_code=422, detail="CPF inválido (dígito verificador)")
    return d


def _mais_meses(d, meses):
    if not d or not meses:
        return None
    m = d.month - 1 + int(meses)
    a = d.year + m // 12
    m = m % 12 + 1
    import calendar
    return _date(a, m, min(d.day, calendar.monthrange(a, m)[1]))


def _f(v):
    return float(v) if v is not None else None


def _status_trein(t, hoje=None):
    """Situação de um treinamento: valida · vence (≤30 dias) · vencida · andamento · inscrito."""
    hoje = hoje or _hoje()
    if t["situacao"] in ("inscrito", "andamento"):
        return t["situacao"]
    va = t.get("validade_ate")
    if not va:
        return "valida"
    if va < hoje:
        return "vencida"
    if (va - hoje).days <= 30:
        return "vence"
    return "valida"


async def _colab(cid):
    c = await ajard_query("SELECT * FROM rh.colaboradores WHERE id=%s AND ativo", (cid,), fetch="one")
    if not c:
        raise HTTPException(status_code=404, detail="Colaborador não encontrado")
    return c


async def _situacao_nrs(cids=None):
    """{colaborador_id: {curso_id: {st, validade_ate, tid, exigida}}} — o melhor treinamento por curso
    (o de validade mais longa) + as NRs exigidas pela função que não têm treinamento (st='falta')."""
    await garantir_rh()
    filtro, params = "", []
    if cids is not None:
        if not cids:
            return {}
        filtro = " AND t.colaborador_id = ANY(%s::uuid[])"
        params.append([str(x) for x in cids])
    trein = await ajard_query(
        f"""SELECT t.id, t.colaborador_id, t.curso_id, t.situacao, t.validade_ate, t.data_conclusao
              FROM rh.treinamentos t JOIN rh.cursos k ON k.id = t.curso_id
             WHERE t.ativo{filtro}
             ORDER BY t.colaborador_id, t.curso_id,
                      (t.situacao = 'concluido') DESC, t.validade_ate DESC NULLS FIRST, t.data_conclusao DESC NULLS LAST""",
        tuple(params)) or []
    out = {}
    hoje = _hoje()
    for t in trein:
        c, k = str(t["colaborador_id"]), str(t["curso_id"])
        if k in out.setdefault(c, {}):
            continue   # já tem o melhor
        out[c][k] = {"st": _status_trein(t, hoje), "validade_ate": t["validade_ate"].isoformat() if t["validade_ate"] else None,
                     "conclusao": t["data_conclusao"].isoformat() if t["data_conclusao"] else None, "tid": str(t["id"]), "exigida": False}
    filtro2 = " AND c.id = ANY(%s::uuid[])" if cids is not None else ""
    exig = await ajard_query(
        f"""SELECT c.id AS cid, fc.curso_id
              FROM rh.colaboradores c JOIN rh.cargo_cursos fc ON fc.cargo_codigo = c.cargo_codigo
              JOIN rh.cursos k ON k.id = fc.curso_id AND k.ativo
             WHERE c.ativo AND c.situacao <> 'desligado'{filtro2}""", tuple(params)) or []
    for e in exig:
        c, k = str(e["cid"]), str(e["curso_id"])
        cel = out.setdefault(c, {}).setdefault(k, {"st": "falta", "validade_ate": None, "conclusao": None, "tid": None})
        cel["exigida"] = True
    return out


def _resumo(cel_por_curso):
    r = {"falta": 0, "vencida": 0, "vence": 0, "andamento": 0, "valida": 0}
    for cel in (cel_por_curso or {}).values():
        st = cel["st"]
        if st == "inscrito":
            st = "andamento"
        if st in ("falta",) and not cel.get("exigida"):
            continue
        r[st] = r.get(st, 0) + 1
    return r


# ══════════════════════════════════════════════════════════════
# PÁGINA
# ══════════════════════════════════════════════════════════════
@router.get("/rh", response_class=HTMLResponse)
async def pagina_rh():
    raiz = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "rh"))
    for p in (os.path.join(raiz, "static", "rh.html"), os.path.join(raiz, "rh.html")):
        if os.path.isfile(p):
            return HTMLResponse(open(p, encoding="utf-8").read(),
                                headers={"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"})
    raise HTTPException(status_code=404, detail="Página do módulo RH não encontrada")


@router.get("/rh/api/eu")
async def rh_eu(payload=Depends(verificar_rh_ver)):
    await garantir_rh()
    return {"nome": payload.get("nome"), "pode_rh": await _eh_rh(payload)}


# ══════════════════════════════════════════════════════════════
# COLABORADORES
# ══════════════════════════════════════════════════════════════
@router.get("/rh/api/colaboradores")
async def colaboradores_listar(situacao: str = "", cargo: str = "", payload=Depends(verificar_rh_ver)):
    await garantir_rh()
    from routers.manutencao import _garantir_funcoes
    await _garantir_funcoes()
    cond, params = ["c.ativo"], []
    if situacao in _SITUACOES:
        cond.append("c.situacao=%s"); params.append(situacao)
    elif situacao != "todos":
        cond.append("c.situacao <> 'desligado'")
    if cargo:
        cond.append("c.cargo_codigo=%s"); params.append(cargo)
    rows = await ajard_query(
        f"""SELECT c.id, c.nome, c.apelido, c.cpf, c.telefone, c.situacao, c.vinculo, c.funcao_codigo, f.nome AS funcao_nome,
                   f.setor, c.usuario_id, u.login, c.data_admissao, c.cargo_codigo, cg.nome AS cargo_nome, c.matricula_mais
              FROM rh.colaboradores c
              LEFT JOIN manutencao.funcoes f ON f.codigo = c.funcao_codigo
              LEFT JOIN rh.cargos cg ON cg.codigo = c.cargo_codigo
              LEFT JOIN public.usuarios_garra u ON u.id = c.usuario_id
             WHERE {' AND '.join(cond)} ORDER BY c.nome""", tuple(params)) or []
    sit = await _situacao_nrs([r["id"] for r in rows])
    pode = await _eh_rh(payload)
    out = []
    for r in rows:
        d = {"id": str(r["id"]), "nome": r["nome"], "apelido": r["apelido"], "situacao": r["situacao"], "vinculo": r["vinculo"],
             "cargo_codigo": r["cargo_codigo"], "cargo_nome": r["cargo_nome"],
             "funcao_codigo": r["funcao_codigo"], "funcao_nome": r["funcao_nome"], "setor": r["setor"],
             "tem_login": bool(r["usuario_id"]), "nr": _resumo(sit.get(str(r["id"])))}
        if pode:
            d.update({"cpf": r["cpf"], "telefone": r["telefone"], "login": r["login"], "matricula_mais": r["matricula_mais"],
                      "data_admissao": r["data_admissao"].isoformat() if r["data_admissao"] else None})
        out.append(d)
    return out


_CAMPOS_DADOS = ("nome", "cpf", "matricula_mais", "telefone", "data_nascimento", "data_admissao",
                 "data_desligamento", "situacao", "vinculo", "usuario_id", "observacao", "funcao_codigo", "custo_hh",
                 "apelido", "cargo_codigo")


async def _validar_dados(d, cid=None):
    v = {}
    for k in _CAMPOS_DADOS:
        if k not in d:
            continue
        x = d.get(k)
        if k == "nome":
            x = (x or "").strip()
            if not x:
                raise HTTPException(status_code=422, detail="Informe o nome")
        elif k == "cpf":
            x = _cpf_limpo(x)
            if x:
                dup = await ajard_query("SELECT nome FROM rh.colaboradores WHERE cpf=%s AND ativo AND id IS DISTINCT FROM %s",
                                        (x, cid), fetch="one")
                if dup:
                    raise HTTPException(status_code=409, detail=f"CPF já cadastrado para {dup['nome']}")
        elif k in ("data_nascimento", "data_admissao", "data_desligamento"):
            x = _data(x)
        elif k == "situacao":
            if x not in _SITUACOES:
                raise HTTPException(status_code=422, detail="Situação inválida")
        elif k == "custo_hh":
            x = _num(x)
        elif k == "usuario_id":
            x = (x or "").strip() or None
            if x:
                dup = await ajard_query("SELECT nome FROM rh.colaboradores WHERE usuario_id=%s AND ativo AND id IS DISTINCT FROM %s",
                                        (x, cid), fetch="one")
                if dup:
                    raise HTTPException(status_code=409, detail=f"Esse login já está na ficha de {dup['nome']}")
        elif k in ("funcao_codigo", "vinculo", "cargo_codigo"):
            x = (x or "").strip() or None
        else:
            x = (x or "").strip() or None if isinstance(x, str) or x is None else x
        v[k] = x
    return v


@router.post("/rh/api/colaboradores")
async def colaborador_criar(request: Request, payload=Depends(verificar_rh)):
    await garantir_rh()
    d = await request.json()
    v = await _validar_dados(d)
    if not v.get("nome"):
        raise HTTPException(status_code=422, detail="Informe o nome")
    cols = list(v.keys()) + ["criado_por"]
    vals = list(v.values()) + [payload.get("sub")]
    r = await ajard_query(
        f"INSERT INTO rh.colaboradores ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))}) RETURNING id",
        tuple(vals), fetch="one")
    return {"ok": True, "id": str(r["id"])}


@router.get("/rh/api/colaboradores/{cid}")
async def colaborador_ficha(cid: str, payload=Depends(verificar_rh_ver)):
    await garantir_rh()
    from routers.manutencao import _garantir_funcoes
    await _garantir_funcoes()
    c = await _colab(cid)
    pode = await _eh_rh(payload)
    f = None
    if c["funcao_codigo"]:
        f = await ajard_query("SELECT codigo, nome, setor, custo_hh FROM manutencao.funcoes WHERE codigo=%s",
                              (c["funcao_codigo"],), fetch="one")
    u = None
    if c["usuario_id"]:
        u = await ajard_query("SELECT id, login, nome, perfil, ativo FROM public.usuarios_garra WHERE id=%s",
                              (c["usuario_id"],), fetch="one")
    cg = None
    if c["cargo_codigo"]:
        cg = await ajard_query("SELECT nome FROM rh.cargos WHERE codigo=%s", (c["cargo_codigo"],), fetch="one")
    out = {"id": str(c["id"]), "nome": c["nome"], "apelido": c["apelido"], "situacao": c["situacao"], "vinculo": c["vinculo"],
           "cargo_codigo": c["cargo_codigo"], "cargo_nome": cg["nome"] if cg else None,
           "funcao_codigo": c["funcao_codigo"], "funcao_nome": f["nome"] if f else None, "setor": f["setor"] if f else None,
           "tem_login": bool(u), "pode_rh": pode}
    if pode:
        iso = lambda x: x.isoformat() if x else None
        out.update({
            "cpf": c["cpf"], "matricula_mais": c["matricula_mais"], "telefone": c["telefone"],
            "data_nascimento": iso(c["data_nascimento"]), "data_admissao": iso(c["data_admissao"]),
            "data_desligamento": iso(c["data_desligamento"]), "observacao": c["observacao"],
            "mais_sync_em": c["mais_sync_em"].isoformat() if c["mais_sync_em"] else None,
            "usuario": ({"id": str(u["id"]), "login": u["login"], "nome": u["nome"], "perfil": u["perfil"], "ativo": u["ativo"]} if u else None),
            "custo_hh_proprio": _f(c["custo_hh"]), "custo_hh_funcao": _f(f["custo_hh"]) if f else None,
            "custo_hh": _f(c["custo_hh"]) if c["custo_hh"] is not None else (_f(f["custo_hh"]) if f else None),
        })
    return out


@router.patch("/rh/api/colaboradores/{cid}")
async def colaborador_editar(cid: str, request: Request, payload=Depends(verificar_rh)):
    await garantir_rh()
    await _colab(cid)
    v = await _validar_dados(await request.json(), cid)
    if not v:
        return {"ok": True}
    if v.get("situacao") == "desligado" and "data_desligamento" not in v:
        v["data_desligamento"] = _hoje()
    sets = [f"{k}=%s" for k in v] + ["atualizado_em=now()"]
    await ajard_query(f"UPDATE rh.colaboradores SET {', '.join(sets)} WHERE id=%s", tuple(v.values()) + (cid,), fetch="none")
    return {"ok": True}


@router.delete("/rh/api/colaboradores/{cid}")
async def colaborador_excluir(cid: str, payload=Depends(verificar_rh)):
    """Ficha criada por engano: sai da lista (soft). Quem saiu da empresa = situação Desligado."""
    await garantir_rh()
    await _colab(cid)
    n = await ajard_query("SELECT COUNT(*) AS n FROM rh.treinamentos WHERE colaborador_id=%s AND ativo", (cid,), fetch="one")
    if n and int(n["n"]) > 0:
        raise HTTPException(status_code=409, detail="Tem treinamentos registrados — marque a situação como Desligado em vez de excluir")
    await ajard_query("UPDATE rh.colaboradores SET ativo=false, atualizado_em=now() WHERE id=%s", (cid,), fetch="none")
    return {"ok": True}


@router.get("/rh/api/usuarios-sem-ficha")
async def usuarios_sem_ficha(payload=Depends(verificar_rh)):
    """Prévia da importação: logins do sistema que ainda não têm ficha (você escolhe quem entra)."""
    await garantir_rh()
    rows = await ajard_query(
        """SELECT u.id, u.nome, u.login, u.perfil, u.ativo FROM public.usuarios_garra u
            WHERE NOT EXISTS (SELECT 1 FROM rh.colaboradores c WHERE c.usuario_id = u.id AND c.ativo)
            ORDER BY u.ativo DESC, u.nome""") or []
    return [{"id": str(r["id"]), "nome": r["nome"], "login": r["login"], "perfil": r["perfil"], "ativo": r["ativo"]} for r in rows]


@router.post("/rh/api/importar-usuarios")
async def importar_usuarios(request: Request, payload=Depends(verificar_rh)):
    await garantir_rh()
    ids = [str(x) for x in ((await request.json()).get("ids") or [])][:500]
    n = 0
    for uid in ids:
        r = await ajard_query(
            """INSERT INTO rh.colaboradores (nome, usuario_id, situacao, criado_por)
               SELECT u.nome, u.id, CASE WHEN u.ativo THEN 'ativo' ELSE 'desligado' END, %s
                 FROM public.usuarios_garra u
                WHERE u.id=%s AND NOT EXISTS (SELECT 1 FROM rh.colaboradores c WHERE c.usuario_id=u.id AND c.ativo)
               RETURNING id""", (payload.get("sub"), uid), fetch="one")
        n += 1 if r else 0
    return {"ok": True, "criados": n}


@router.get("/rh/api/opcoes")
async def opcoes(payload=Depends(verificar_rh_ver)):
    """Listas dos seletores: funções (organograma), vínculos, logins livres, cursos e plataformas."""
    await garantir_rh()
    from routers.manutencao import _garantir_funcoes
    await _garantir_funcoes()
    fun = await ajard_query("SELECT codigo, nome, setor, custo_hh, ativo FROM manutencao.funcoes WHERE externa IS NOT TRUE ORDER BY codigo") or []
    vin = await ajard_query("SELECT codigo, nome FROM rh.vinculos WHERE ativo ORDER BY ordem, nome") or []
    pode = await _eh_rh(payload)
    cgs = await ajard_query("SELECT codigo, nome, ativo FROM rh.cargos ORDER BY ativo DESC, nome") or []
    out = {"funcoes": [{**dict(f), "custo_hh": _f(f["custo_hh"]) if pode else None} for f in fun], "cargos": cgs,
           "vinculos": vin, "cursos": await _cursos(), "plataformas": await _plataformas()}
    if pode:
        us = await ajard_query("SELECT id, nome, login, ativo FROM public.usuarios_garra ORDER BY ativo DESC, nome") or []
        out["usuarios"] = [{"id": str(u["id"]), "nome": u["nome"], "login": u["login"], "ativo": u["ativo"]} for u in us]
    return out


# ══════════════════════════════════════════════════════════════
# CATÁLOGO DE NRs / TREINAMENTOS · EXIGÊNCIA POR FUNÇÃO · PLATAFORMAS
# ══════════════════════════════════════════════════════════════
async def _cursos(so_ativos=False):
    rows = await ajard_query(
        "SELECT id, codigo, nome, tipo, validade_meses, carga_horaria, ordem, ativo FROM rh.cursos"
        + (" WHERE ativo" if so_ativos else "") + " ORDER BY ativo DESC, (tipo='NR') DESC, ordem, codigo") or []
    return [{**dict(r), "id": str(r["id"]), "carga_horaria": _f(r["carga_horaria"])} for r in rows]


async def _plataformas():
    rows = await ajard_query("SELECT id, nome, site, observacao, ativo FROM rh.plataformas ORDER BY ativo DESC, nome") or []
    return [{**dict(r), "id": str(r["id"])} for r in rows]


@router.get("/rh/api/cursos")
async def cursos_listar(payload=Depends(verificar_rh_ver)):
    await garantir_rh()
    return await _cursos()


@router.post("/rh/api/cursos")
async def cursos_salvar(request: Request, payload=Depends(verificar_rh)):
    await garantir_rh()
    d = await request.json()
    cod, nome = (d.get("codigo") or "").strip().upper(), (d.get("nome") or "").strip()
    if not cod or not nome:
        raise HTTPException(status_code=422, detail="Informe código e nome")
    val = d.get("validade_meses")
    val = int(_num(val)) if val not in (None, "") else None
    ch = _num(d.get("carga_horaria"))
    tipo = "NR" if (d.get("tipo") or "NR").upper() == "NR" else "OUTRO"
    if d.get("id"):
        await ajard_query("""UPDATE rh.cursos SET codigo=%s, nome=%s, tipo=%s, validade_meses=%s, carga_horaria=%s,
                                    ativo=COALESCE(%s, ativo) WHERE id=%s""",
                          (cod, nome, tipo, val, ch, d.get("ativo"), d["id"]), fetch="none")
        return {"ok": True, "id": d["id"]}
    dup = await ajard_query("SELECT id FROM rh.cursos WHERE upper(codigo)=%s", (cod,), fetch="one")
    if dup:
        raise HTTPException(status_code=409, detail="Já existe um treinamento com esse código")
    r = await ajard_query("""INSERT INTO rh.cursos (codigo, nome, tipo, validade_meses, carga_horaria, ordem)
                             VALUES (%s,%s,%s,%s,%s,999) RETURNING id""", (cod, nome, tipo, val, ch), fetch="one")
    return {"ok": True, "id": str(r["id"])}


@router.get("/rh/api/exigencias")
async def exigencias(payload=Depends(verificar_rh_ver)):
    """{cargo_codigo: [curso_id, …]} — NRs que cada cargo exige."""
    await garantir_rh()
    rows = await ajard_query("SELECT cargo_codigo, curso_id FROM rh.cargo_cursos") or []
    out = {}
    for r in rows:
        out.setdefault(r["cargo_codigo"], []).append(str(r["curso_id"]))
    return out


@router.put("/rh/api/exigencias/{cargo_codigo}")
async def exigencias_salvar(cargo_codigo: str, request: Request, payload=Depends(verificar_rh)):
    await garantir_rh()
    ids = [str(x) for x in ((await request.json()).get("cursos") or [])]
    await ajard_query("DELETE FROM rh.cargo_cursos WHERE cargo_codigo=%s", (cargo_codigo,), fetch="none")
    for k in ids:
        await ajard_query("INSERT INTO rh.cargo_cursos (cargo_codigo, curso_id) VALUES (%s,%s) ON CONFLICT DO NOTHING",
                          (cargo_codigo, k), fetch="none")
    return {"ok": True}


@router.post("/rh/api/plataformas")
async def plataformas_salvar(request: Request, payload=Depends(verificar_rh)):
    await garantir_rh()
    d = await request.json()
    nome = (d.get("nome") or "").strip()
    if not nome:
        raise HTTPException(status_code=422, detail="Informe o nome da plataforma")
    site = (d.get("site") or "").strip() or None
    if d.get("id"):
        await ajard_query("UPDATE rh.plataformas SET nome=%s, site=%s, observacao=%s, ativo=COALESCE(%s, ativo) WHERE id=%s",
                          (nome, site, (d.get("observacao") or "").strip() or None, d.get("ativo"), d["id"]), fetch="none")
        return {"ok": True, "id": d["id"]}
    dup = await ajard_query("SELECT id FROM rh.plataformas WHERE upper(nome)=upper(%s)", (nome,), fetch="one")
    if dup:
        raise HTTPException(status_code=409, detail="Plataforma já cadastrada")
    r = await ajard_query("INSERT INTO rh.plataformas (nome, site) VALUES (%s,%s) RETURNING id", (nome, site), fetch="one")
    return {"ok": True, "id": str(r["id"])}


# ══════════════════════════════════════════════════════════════
# TREINAMENTOS (NRs) DO COLABORADOR
# ══════════════════════════════════════════════════════════════
@router.get("/rh/api/colaboradores/{cid}/treinamentos")
async def treinamentos_listar(cid: str, payload=Depends(verificar_rh_ver)):
    await garantir_rh()
    c = await _colab(cid)
    pode = await _eh_rh(payload)
    rows = await ajard_query(
        """SELECT t.*, k.codigo AS curso_codigo, k.nome AS curso_nome, k.validade_meses, p.nome AS plataforma_nome
             FROM rh.treinamentos t JOIN rh.cursos k ON k.id = t.curso_id
             LEFT JOIN rh.plataformas p ON p.id = t.plataforma_id
            WHERE t.colaborador_id=%s AND t.ativo
            ORDER BY k.ordem, k.codigo, t.data_conclusao DESC NULLS FIRST""", (cid,)) or []
    hoje = _hoje()
    iso = lambda x: x.isoformat() if x else None
    hist = []
    for t in rows:
        h = {"id": str(t["id"]), "curso_id": str(t["curso_id"]), "curso_codigo": t["curso_codigo"], "curso_nome": t["curso_nome"],
             "situacao": t["situacao"], "status": _status_trein(t, hoje), "modalidade": t["modalidade"],
             "plataforma_id": str(t["plataforma_id"]) if t["plataforma_id"] else None, "plataforma_nome": t["plataforma_nome"],
             "local": t["local"], "instrutor": t["instrutor"], "data_inicio": iso(t["data_inicio"]),
             "data_conclusao": iso(t["data_conclusao"]), "validade_ate": iso(t["validade_ate"]),
             "carga_horaria": _f(t["carga_horaria"]), "tem_certificado": bool(t["certificado_path"]),
             "certificado_nome": t["certificado_nome"], "observacao": t["observacao"]}
        if pode:
            h["custo"] = _f(t["custo"])
        hist.append(h)
    sit = (await _situacao_nrs([cid])).get(str(c["id"]), {})
    cursos = {k["id"]: k for k in await _cursos()}
    quadro = []
    for kid, cel in sit.items():
        k = cursos.get(kid)
        if not k:
            continue
        ult = next((h for h in hist if h["id"] == cel.get("tid")), None)
        quadro.append({"curso_id": kid, "codigo": k["codigo"], "nome": k["nome"], "ordem": k["ordem"], "exigida": cel.get("exigida", False),
                       "status": cel["st"], "validade_ate": cel["validade_ate"], "conclusao": cel["conclusao"],
                       "onde": (("EAD · " + (ult["plataforma_nome"] or "")) if ult and ult["modalidade"] == "ead"
                                else (("Presencial" + (" · " + ult["local"] if ult.get("local") else "")) if ult and ult["modalidade"] == "presencial"
                                      else ("Semipresencial" if ult and ult["modalidade"] == "semipresencial" else None))),
                       "tid": cel.get("tid"), "tem_certificado": bool(ult and ult["tem_certificado"])})
    ordem_st = {"vencida": 0, "falta": 1, "vence": 2, "andamento": 3, "inscrito": 3, "valida": 4}
    quadro.sort(key=lambda q: (not q["exigida"], ordem_st.get(q["status"], 9), q["ordem"] or 0, q["codigo"]))
    return {"quadro": quadro, "historico": hist, "resumo": _resumo(sit)}


async def _trein_payload(d, parcial=False):
    v = {}
    if not parcial or "curso_id" in d:
        if not d.get("curso_id"):
            raise HTTPException(status_code=422, detail="Escolha a NR / treinamento")
        v["curso_id"] = d["curso_id"]
    if not parcial or "situacao" in d:
        s = d.get("situacao") or "concluido"
        if s not in _SIT_TREIN:
            raise HTTPException(status_code=422, detail="Situação inválida")
        v["situacao"] = s
    if "modalidade" in d or not parcial:
        m = (d.get("modalidade") or "").strip() or None
        if m and m not in _MODALIDADES:
            raise HTTPException(status_code=422, detail="Modalidade inválida")
        v["modalidade"] = m
    for k in ("plataforma_id", "local", "instrutor", "observacao"):
        if k in d or not parcial:
            v[k] = (str(d.get(k) or "")).strip() or None
    for k in ("data_inicio", "data_conclusao", "validade_ate"):
        if k in d or not parcial:
            v[k] = _data(d.get(k))
    for k in ("carga_horaria", "custo"):
        if k in d or not parcial:
            v[k] = _num(d.get(k))
    if v.get("modalidade") != "ead" and "modalidade" in v:
        v["plataforma_id"] = None   # Classe 2: plataforma pertence ao EAD
    if v.get("situacao") == "concluido" and "data_conclusao" in v and not v.get("data_conclusao"):
        raise HTTPException(status_code=422, detail="Informe a data de conclusão")
    if v.get("data_conclusao") and v["data_conclusao"] > _hoje():
        raise HTTPException(status_code=422, detail="Data de conclusão no futuro")
    return v


async def _vencimento(v, curso_id):
    """Vencimento = conclusão + validade da NR (se não foi digitado à mão)."""
    if v.get("situacao") == "concluido" and v.get("data_conclusao") and not v.get("validade_ate"):
        k = await ajard_query("SELECT validade_meses FROM rh.cursos WHERE id=%s", (curso_id,), fetch="one")
        if k and k["validade_meses"]:
            v["validade_ate"] = _mais_meses(v["data_conclusao"], k["validade_meses"])
    if v.get("situacao") in ("inscrito", "andamento"):
        v["validade_ate"] = None


@router.post("/rh/api/colaboradores/{cid}/treinamentos")
async def treinamento_criar(cid: str, request: Request, payload=Depends(verificar_rh)):
    await garantir_rh()
    await _colab(cid)
    v = await _trein_payload(await request.json())
    await _vencimento(v, v["curso_id"])
    cols = ["colaborador_id"] + list(v.keys()) + ["criado_por"]
    vals = [cid] + list(v.values()) + [payload.get("sub")]
    r = await ajard_query(f"INSERT INTO rh.treinamentos ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))}) RETURNING id",
                          tuple(vals), fetch="one")
    return {"ok": True, "id": str(r["id"])}


@router.patch("/rh/api/treinamentos/{tid}")
async def treinamento_editar(tid: str, request: Request, payload=Depends(verificar_rh)):
    await garantir_rh()
    t = await ajard_query("SELECT * FROM rh.treinamentos WHERE id=%s AND ativo", (tid,), fetch="one")
    if not t:
        raise HTTPException(status_code=404, detail="Treinamento não encontrado")
    d = await request.json()
    v = await _trein_payload(d, parcial=True)
    junto = {**{k: t[k] for k in ("situacao", "data_conclusao")}, **v}
    if "validade_ate" not in d and ("data_conclusao" in v or "situacao" in v):
        junto["validade_ate"] = None
        await _vencimento(junto, v.get("curso_id") or t["curso_id"])
        v["validade_ate"] = junto.get("validade_ate")
    if not v:
        return {"ok": True}
    sets = [f"{k}=%s" for k in v] + ["atualizado_em=now()"]
    await ajard_query(f"UPDATE rh.treinamentos SET {', '.join(sets)} WHERE id=%s", tuple(v.values()) + (tid,), fetch="none")
    return {"ok": True}


@router.delete("/rh/api/treinamentos/{tid}")
async def treinamento_excluir(tid: str, payload=Depends(verificar_rh)):
    await garantir_rh()
    await ajard_query("UPDATE rh.treinamentos SET ativo=false, atualizado_em=now() WHERE id=%s", (tid,), fetch="none")
    return {"ok": True}


_TIPOS_CERT = {"application/pdf": "pdf", "image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}


@router.post("/rh/api/treinamentos/{tid}/certificado")
async def certificado_enviar(tid: str, ficheiro: UploadFile = File(...), payload=Depends(verificar_rh)):
    await garantir_rh()
    t = await ajard_query("SELECT colaborador_id FROM rh.treinamentos WHERE id=%s AND ativo", (tid,), fetch="one")
    if not t:
        raise HTTPException(status_code=404, detail="Treinamento não encontrado")
    ctype = (ficheiro.content_type or "").lower()
    if ctype not in _TIPOS_CERT:
        raise HTTPException(status_code=415, detail="Envie PDF ou foto (JPG/PNG)")
    dados = await ficheiro.read()
    if len(dados) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Arquivo acima de 10 MB")
    path = storage_upload(dados, f"rh/colaboradores/{t['colaborador_id']}/certificados/{tid}_{_uuid.uuid4().hex[:8]}.{_TIPOS_CERT[ctype]}",
                          content_type=ctype)
    nome = re.sub(r"[^\w.\- ]", "", ficheiro.filename or "certificado")[:80]
    await ajard_query("UPDATE rh.treinamentos SET certificado_path=%s, certificado_nome=%s, atualizado_em=now() WHERE id=%s",
                      (path, nome, tid), fetch="none")
    return {"ok": True}


@router.get("/rh/api/treinamentos/{tid}/certificado")
async def certificado_link(tid: str, payload=Depends(verificar_rh_ver)):
    await garantir_rh()
    t = await ajard_query("SELECT certificado_path FROM rh.treinamentos WHERE id=%s AND ativo", (tid,), fetch="one")
    if not t or not t["certificado_path"]:
        raise HTTPException(status_code=404, detail="Sem certificado")
    url = storage_url(t["certificado_path"])
    if not url:
        raise HTTPException(status_code=502, detail="Não foi possível abrir o certificado agora (armazenamento indisponível)")
    return {"url": url}


# ══════════════════════════════════════════════════════════════
# LOGINS DAS PLATAFORMAS EAD (senha criptografada; ver = registrado)
# ══════════════════════════════════════════════════════════════
@router.get("/rh/api/colaboradores/{cid}/acessos")
async def acessos_listar(cid: str, payload=Depends(verificar_rh)):
    await garantir_rh()
    await _colab(cid)
    rows = await ajard_query(
        """SELECT p.id AS plataforma_id, p.nome, p.site, a.id, a.login, (a.senha_cripto IS NOT NULL) AS tem_senha,
                  a.observacao, a.atualizado_em, a.atualizado_por
             FROM rh.plataformas p
             LEFT JOIN rh.acessos_plataforma a ON a.plataforma_id = p.id AND a.colaborador_id=%s
            WHERE p.ativo OR a.id IS NOT NULL ORDER BY p.nome""", (cid,)) or []
    return [{"plataforma_id": str(r["plataforma_id"]), "plataforma": r["nome"], "site": r["site"],
             "id": str(r["id"]) if r["id"] else None, "login": r["login"], "tem_senha": bool(r["tem_senha"]),
             "observacao": r["observacao"], "atualizado_por": r["atualizado_por"],
             "atualizado_em": r["atualizado_em"].isoformat() if r["atualizado_em"] else None} for r in rows]


@router.put("/rh/api/colaboradores/{cid}/acessos/{pid}")
async def acesso_salvar(cid: str, pid: str, request: Request, payload=Depends(verificar_rh)):
    """senha: texto novo grava (criptografada) · ausente mantém · "" apaga."""
    await garantir_rh()
    await _colab(cid)
    d = await request.json()
    login = (d.get("login") or "").strip() or None
    obs = (d.get("observacao") or "").strip() or None
    atual = await ajard_query("SELECT id, senha_cripto FROM rh.acessos_plataforma WHERE colaborador_id=%s AND plataforma_id=%s",
                              (cid, pid), fetch="one")
    if "senha" in d:
        cripto = _cifrar(d.get("senha") or "")
    else:
        cripto = atual["senha_cripto"] if atual else None
    await ajard_query(
        """INSERT INTO rh.acessos_plataforma (colaborador_id, plataforma_id, login, senha_cripto, observacao, atualizado_por, atualizado_em)
           VALUES (%s,%s,%s,%s,%s,%s,now())
           ON CONFLICT (colaborador_id, plataforma_id) DO UPDATE
              SET login=EXCLUDED.login, senha_cripto=EXCLUDED.senha_cripto, observacao=EXCLUDED.observacao,
                  atualizado_por=EXCLUDED.atualizado_por, atualizado_em=now()""",
        (cid, pid, login, cripto, obs, payload.get("sub")), fetch="none")
    await ajard_query("INSERT INTO rh.acessos_log (usuario_login, acao, colaborador_id, plataforma_id) VALUES (%s,%s,%s,%s)",
                      (payload.get("sub"), "alterou" if "senha" in d else "alterou_login", cid, pid), fetch="none")
    return {"ok": True}


@router.post("/rh/api/colaboradores/{cid}/acessos/{pid}/revelar")
async def acesso_revelar(cid: str, pid: str, payload=Depends(verificar_rh)):
    await garantir_rh()
    a = await ajard_query("SELECT senha_cripto FROM rh.acessos_plataforma WHERE colaborador_id=%s AND plataforma_id=%s",
                          (cid, pid), fetch="one")
    if not a or not a["senha_cripto"]:
        raise HTTPException(status_code=404, detail="Sem senha guardada")
    senha = _decifrar(a["senha_cripto"])
    await ajard_query("INSERT INTO rh.acessos_log (usuario_login, acao, colaborador_id, plataforma_id) VALUES (%s,'viu_senha',%s,%s)",
                      (payload.get("sub"), cid, pid), fetch="none")
    return {"senha": senha}


@router.get("/rh/api/colaboradores/{cid}/acessos-log")
async def acessos_log(cid: str, payload=Depends(verificar_rh)):
    await garantir_rh()
    rows = await ajard_query(
        """SELECT l.em, l.acao, l.usuario_login, u.nome AS usuario_nome, p.nome AS plataforma
             FROM rh.acessos_log l LEFT JOIN rh.plataformas p ON p.id = l.plataforma_id
             LEFT JOIN public.usuarios_garra u ON u.login = l.usuario_login
            WHERE l.colaborador_id=%s ORDER BY l.em DESC LIMIT 50""", (cid,)) or []
    return [{"em": r["em"].isoformat(), "acao": r["acao"], "quem": r["usuario_nome"] or r["usuario_login"],
             "plataforma": r["plataforma"]} for r in rows]


# ══════════════════════════════════════════════════════════════
# PAINEL GERAL DE NRs (colaboradores × NRs) + EXCEL
# ══════════════════════════════════════════════════════════════
async def _painel(cargo="", situacao="", so_exigidas=False):
    await garantir_rh()
    from routers.manutencao import _garantir_funcoes
    await _garantir_funcoes()
    cond, params = ["c.ativo"], []
    if situacao in _SITUACOES:
        cond.append("c.situacao=%s"); params.append(situacao)
    else:
        cond.append("c.situacao <> 'desligado'")
    if cargo:
        cond.append("c.cargo_codigo=%s"); params.append(cargo)
    col = await ajard_query(
        f"""SELECT c.id, c.nome, c.situacao, c.cargo_codigo, f.nome AS funcao_nome
              FROM rh.colaboradores c LEFT JOIN rh.cargos f ON f.codigo=c.cargo_codigo
             WHERE {' AND '.join(cond)} ORDER BY c.nome""", tuple(params)) or []
    sit = await _situacao_nrs([c["id"] for c in col])
    cursos = await _cursos(so_ativos=True)
    usados = set()
    for cel in sit.values():
        for k, x in cel.items():
            if x.get("exigida") or not so_exigidas:
                usados.add(k)
    cursos = [k for k in cursos if k["id"] in usados or (k["tipo"] == "NR" and not so_exigidas and not cargo)]
    linhas, tot = [], {"falta": 0, "vencida": 0, "vence": 0, "andamento": 0, "valida": 0}
    for c in col:
        cels = sit.get(str(c["id"]), {})
        r = _resumo(cels)
        for k in tot:
            tot[k] += r.get(k, 0)
        linhas.append({"id": str(c["id"]), "nome": c["nome"], "funcao": c["funcao_nome"], "situacao": c["situacao"],
                       "resumo": r, "celulas": {k: v for k, v in cels.items() if k in {x["id"] for x in cursos}}})
    return {"cursos": [{"id": k["id"], "codigo": k["codigo"], "nome": k["nome"], "validade_meses": k["validade_meses"]} for k in cursos],
            "linhas": linhas, "totais": tot, "hoje": _hoje().isoformat()}


@router.get("/rh/api/painel-nr")
async def painel_nr(cargo: str = "", situacao: str = "", so_exigidas: int = 0, payload=Depends(verificar_rh_ver)):
    return await _painel(cargo, situacao, bool(so_exigidas))


@router.get("/rh/api/painel-nr.xlsx")
async def painel_nr_excel(cargo: str = "", situacao: str = "", so_exigidas: int = 0, payload=Depends(verificar_rh_ver)):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill, Border, Side
    p = await _painel(cargo, situacao, bool(so_exigidas))
    wb = Workbook(); ws = wb.active; ws.title = "NRs"
    cor = {"valida": "DCFCE7", "vence": "FEF3C7", "vencida": "FEE2E2", "falta": "FEE2E2", "andamento": "DBEAFE", "inscrito": "DBEAFE"}
    txt = {"valida": "Válida", "vence": "Vence", "vencida": "Vencida", "falta": "Falta", "andamento": "Em andamento", "inscrito": "Inscrito"}
    fino = Side(style="thin", color="CBD5E1")
    ws.append(["GARRA TERRAPLENAGEM — Situação das NRs", "", "", f"Gerado em {_hoje().strftime('%d/%m/%Y')}"])
    ws["A1"].font = Font(bold=True, size=13, color="001F4D")
    ws.append([])
    ws.append(["Colaborador", "Cargo"] + [k["codigo"] for k in p["cursos"]] + ["Faltam", "Vencidas", "Vencem ≤30d"])
    for c in ws[3]:
        c.font = Font(bold=True, color="FFFFFF"); c.fill = PatternFill("solid", fgColor="001F4D")
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for ln in p["linhas"]:
        row = [ln["nome"], ln["funcao"] or ""]
        for k in p["cursos"]:
            cel = ln["celulas"].get(k["id"])
            if not cel:
                row.append("")
            else:
                s = txt.get(cel["st"], cel["st"])
                if cel.get("validade_ate") and cel["st"] in ("valida", "vence", "vencida"):
                    y, m, d = cel["validade_ate"].split("-"); s += f" até {d}/{m}/{y}"
                row.append(s)
        row += [ln["resumo"]["falta"], ln["resumo"]["vencida"], ln["resumo"]["vence"]]
        ws.append(row)
        r = ws.max_row
        for i, k in enumerate(p["cursos"]):
            cel = ln["celulas"].get(k["id"])
            if cel:
                ws.cell(r, 3 + i).fill = PatternFill("solid", fgColor=cor.get(cel["st"], "FFFFFF"))
        for c in ws[r]:
            c.border = Border(top=fino, bottom=fino, left=fino, right=fino)
    ws.column_dimensions["A"].width = 32; ws.column_dimensions["B"].width = 24
    from openpyxl.utils import get_column_letter
    for i in range(len(p["cursos"]) + 3):
        ws.column_dimensions[get_column_letter(3 + i)].width = 17
    ws.freeze_panes = "C4"
    buf = io.BytesIO(); wb.save(buf); buf.seek(0)
    return StreamingResponse(buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                             headers={"Content-Disposition": f'attachment; filename="garra-nrs-{_hoje().isoformat()}.xlsx"'})


# ══════════════════════════════════════════════════════════════
# IMPORTAÇÃO DO MAIS (planilha "Colaboradores" exportada do MAIS) — 15/11/2026
# Prévia → você confere → aplica. Casa por CPF (depois pela matrícula do MAIS). Nunca apaga.
# Só traz o necessário: matrícula, nome, apelido, CPF, nascimento, celular, cargo, admissão, demissão.
# O arquivo não é guardado (é lido em memória).
# ══════════════════════════════════════════════════════════════
_COLS_MAIS = {
    "matricula": ("codigo",), "nome": ("nome",), "apelido": ("apelido",), "cpf": ("cpf/cnpj", "cpf"),
    "nascimento": ("data nasc.", "data nasc", "nascimento"), "celular": ("celular",), "telefone": ("telefone",),
    "cargo_codigo": ("c.cargo",), "cargo_nome": ("cargo",), "admissao": ("admissao", "admissão"),
    "demissao": ("demissao", "demissão"), "inativo": ("inativo",),
}


def _norm(s):
    import unicodedata
    s = unicodedata.normalize("NFD", str(s or "")).encode("ascii", "ignore").decode().upper()
    return re.sub(r"[^A-Z0-9 ]", " ", s).split()


def _cel(v):
    if v in (None, ""):
        return None
    if hasattr(v, "date"):
        return v.date()
    if isinstance(v, _date):
        return v
    t = str(v).strip()
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", t) or re.match(r"^(\d{2})/(\d{2})/(\d{4})", t)
    if not m:
        return None
    try:
        return _date(int(m.group(1)), int(m.group(2)), int(m.group(3))) if len(m.group(1)) == 4 \
            else _date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


def _tel(v):
    d = re.sub(r"\D", "", str(v or ""))
    if len(d) == 11:
        return f"({d[:2]}) {d[2:7]}-{d[7:]}"
    if len(d) == 10:
        return f"({d[:2]}) {d[2:6]}-{d[6:]}"
    return d or None


def _ler_planilha_mais(dados: bytes):
    from openpyxl import load_workbook
    try:
        wb = load_workbook(io.BytesIO(dados), read_only=True, data_only=True)
    except Exception:
        raise HTTPException(status_code=422, detail="Não consegui abrir a planilha. Envie o .xlsx exportado do MAIS.")
    ws = wb.worksheets[0]
    linhas = list(ws.iter_rows(values_only=True))
    if not linhas:
        raise HTTPException(status_code=422, detail="Planilha vazia")
    cab = [str(c or "").strip().lower() for c in linhas[0]]
    idx = {}
    for campo, nomes in _COLS_MAIS.items():
        for n in nomes:
            if n in cab:
                idx[campo] = cab.index(n)
                break
    falta = [c for c in ("matricula", "nome", "cpf") if c not in idx]
    if falta:
        raise HTTPException(status_code=422, detail="A planilha não tem as colunas " + ", ".join(falta) + " — é o relatório de colaboradores do MAIS?")
    out = []
    for r in linhas[1:]:
        if not r or not any(r):
            continue
        g = lambda c: r[idx[c]] if c in idx and idx[c] < len(r) else None
        out.append({k: g(k) for k in _COLS_MAIS})
    return out


@router.post("/rh/api/importar-mais/previa")
async def importar_mais_previa(ficheiro: UploadFile = File(...), payload=Depends(verificar_rh)):
    await garantir_rh()
    dados = await ficheiro.read()
    if len(dados) > 5 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Arquivo acima de 5 MB")
    brutas = _ler_planilha_mais(dados)
    fichas = await ajard_query("SELECT id, nome, apelido, cpf, matricula_mais, usuario_id FROM rh.colaboradores WHERE ativo") or []
    usuarios = await ajard_query("SELECT id, nome, login, ativo FROM public.usuarios_garra ORDER BY ativo DESC, nome") or []
    usados = {str(f["usuario_id"]) for f in fichas if f["usuario_id"]}
    cargos = {str(c["codigo"]): c["nome"] for c in (await ajard_query("SELECT codigo, nome FROM rh.cargos") or [])}
    hoje = _hoje()
    previa = []
    for b in brutas:
        avisos = []
        mat = str(b["matricula"]).strip() if b["matricula"] not in (None, "") else ""
        if isinstance(b["matricula"], float) and b["matricula"].is_integer():
            mat = str(int(b["matricula"]))
        nome = " ".join(str(b["nome"] or "").split())
        try:
            cpf = _cpf_limpo(str(b["cpf"] or ""))
        except HTTPException:
            cpf = None
            avisos.append("CPF inválido — entra sem CPF")
        adm, dem, nasc = _cel(b["admissao"]), _cel(b["demissao"]), _cel(b["nascimento"])
        inativo = str(b["inativo"] or "").strip().upper() in ("S", "SIM", "1", "TRUE")
        if dem and adm and dem < adm:
            avisos.append(f"demissão ({dem.strftime('%d/%m/%Y')}) antes da admissão — tratado como readmitido (ativo)")
            dem_vale = None
        else:
            dem_vale = dem
        situacao = "desligado" if (inativo or (dem_vale and dem_vale <= hoje)) else "ativo"
        cg_cod = str(b["cargo_codigo"]).strip() if b["cargo_codigo"] not in (None, "") else None
        if cg_cod and cg_cod.endswith(".0"):
            cg_cod = cg_cod[:-2]
        generico = not cpf and _norm(nome)[:1] in (["TERCERIZADO"], ["TERCEIRIZADO"], ["DIVERSOS"], ["CONSUMIDOR"])
        # casa com ficha existente: CPF → matrícula do MAIS
        ficha = next((f for f in fichas if cpf and f["cpf"] == cpf), None) or \
                next((f for f in fichas if mat and (f["matricula_mais"] or "") == mat), None)
        # sugere o login pelo nome (apelido/1º nome) — você confirma na tela. Se o login já tem ficha
        # sem CPF nem matrícula (criada por "Importar logins" ou pela migração do HH), propõe ATUALIZAR essa ficha.
        sug = None
        if ficha and ficha["usuario_id"]:
            sug = str(ficha["usuario_id"])
        else:
            alvo = [_norm(b["apelido"] or ""), _norm(nome)]
            cands = []
            for u in usuarios:
                if str(u["id"]) in usados:
                    dono = next((f for f in fichas if str(f["usuario_id"] or "") == str(u["id"])), None)
                    if ficha or not dono or dono["cpf"] or dono["matricula_mais"]:
                        continue
                un = _norm(u["nome"])
                if not un:
                    continue
                if any(a and (un == a[:len(un)] or un[0] == a[0]) for a in alvo):
                    peso = max((len(set(un) & set(a)) for a in alvo if a), default=0)
                    cands.append((peso, u))
            cands.sort(key=lambda x: -x[0])
            if cands and (len(cands) == 1 or cands[0][0] > cands[1][0]):
                sug = str(cands[0][1]["id"])
                if not ficha and sug in usados:
                    ficha = next(f for f in fichas if str(f["usuario_id"] or "") == sug)
        previa.append({
            "matricula": mat, "nome": nome, "apelido": " ".join(str(b["apelido"] or "").split()) or None, "cpf": cpf,
            "nascimento": nasc.isoformat() if nasc else None,
            "telefone": _tel(b["celular"]) or _tel(b["telefone"]),
            "cargo_codigo": cg_cod, "cargo_nome": " ".join(str(b["cargo_nome"] or "").split()) or None,
            "cargo_novo": bool(cg_cod and cg_cod not in cargos),
            "admissao": adm.isoformat() if adm else None, "demissao": dem_vale.isoformat() if dem_vale else None,
            "situacao": situacao, "acao": "ignorar" if generico else ("atualizar" if ficha else "criar"),
            "ficha_id": str(ficha["id"]) if ficha else None, "ficha_nome": ficha["nome"] if ficha else None,
            "usuario_id": sug, "generico": generico, "avisos": avisos,
        })
    return {"linhas": previa,
            "usuarios": [{"id": str(u["id"]), "nome": u["nome"], "login": u["login"], "ativo": u["ativo"],
                          "em_uso": str(u["id"]) in usados} for u in usuarios]}


@router.post("/rh/api/importar-mais/aplicar")
async def importar_mais_aplicar(request: Request, payload=Depends(verificar_rh)):
    """Recebe as linhas da prévia (já conferidas) e grava. Campos do MAIS sobrescrevem; os do Garra
    (função, HH, vínculo, observação, NRs, logins EAD) nunca são tocados."""
    await garantir_rh()
    linhas = ((await request.json()).get("linhas") or [])[:1000]
    cri = atu = ign = 0
    erros = []
    for ln in linhas:
        if ln.get("acao") not in ("criar", "atualizar"):
            ign += 1
            continue
        nome = " ".join(str(ln.get("nome") or "").split())
        if not nome:
            continue
        try:
            cpf = _cpf_limpo(ln.get("cpf") or "")
        except HTTPException:
            cpf = None
        if ln.get("cargo_codigo"):
            await ajard_query(
                """INSERT INTO rh.cargos (codigo, nome) VALUES (%s,%s)
                   ON CONFLICT (codigo) DO UPDATE SET nome=EXCLUDED.nome""",
                (str(ln["cargo_codigo"]), ln.get("cargo_nome") or str(ln["cargo_codigo"])), fetch="none")
        uid = (ln.get("usuario_id") or "").strip() or None
        if uid:
            dono = await ajard_query("SELECT nome FROM rh.colaboradores WHERE usuario_id=%s AND ativo AND id IS DISTINCT FROM %s",
                                     (uid, ln.get("ficha_id")), fetch="one")
            if dono:
                erros.append(f"{nome}: o login escolhido já está na ficha de {dono['nome']} — gravado sem login")
                uid = None
        sit = ln.get("situacao") if ln.get("situacao") in _SITUACOES else "ativo"
        vals = (nome, ln.get("apelido"), cpf, str(ln.get("matricula") or "") or None, _data(ln.get("nascimento")),
                ln.get("telefone"), ln.get("cargo_codigo"), _data(ln.get("admissao")), _data(ln.get("demissao")), sit)
        try:
            if ln.get("acao") == "atualizar" and ln.get("ficha_id"):
                await ajard_query(
                    """UPDATE rh.colaboradores SET nome=%s, apelido=%s, cpf=COALESCE(%s, cpf), matricula_mais=%s,
                              data_nascimento=%s, telefone=COALESCE(%s, telefone), cargo_codigo=%s, data_admissao=%s,
                              data_desligamento=%s, situacao=%s, usuario_id=COALESCE(%s, usuario_id),
                              mais_sync_em=now(), atualizado_em=now()
                        WHERE id=%s AND ativo""", vals + (uid, ln["ficha_id"]), fetch="none")
                atu += 1
            else:
                await ajard_query(
                    """INSERT INTO rh.colaboradores (nome, apelido, cpf, matricula_mais, data_nascimento, telefone, cargo_codigo,
                              data_admissao, data_desligamento, situacao, usuario_id, mais_sync_em, criado_por)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now(),%s)""", vals + (uid, payload.get("sub")), fetch="none")
                cri += 1
        except Exception as e:
            erros.append(f"{nome}: {str(e)[:120]}")
    return {"ok": True, "criados": cri, "atualizados": atu, "ignorados": ign, "erros": erros}
