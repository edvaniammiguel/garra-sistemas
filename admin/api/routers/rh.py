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
    # (15/11/2026) excluir do catálogo = some de todas as telas (o registro fica; recriar o mesmo código reaproveita)
    await ajard_query("ALTER TABLE rh.cursos ADD COLUMN IF NOT EXISTS excluido_em TIMESTAMPTZ", fetch="none")
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
    await _garantir_documentos()
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
    return {"nome": payload.get("nome"), "pode_rh": await _eh_rh(payload), "admin": (payload.get("perfil") or "") == "admin"}


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
        "SELECT id, codigo, nome, tipo, validade_meses, carga_horaria, ordem, ativo FROM rh.cursos WHERE excluido_em IS NULL"
        + (" AND ativo" if so_ativos else "") + " ORDER BY ativo DESC, (tipo='NR') DESC, ordem, codigo") or []
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
    dup = await ajard_query("SELECT id, excluido_em FROM rh.cursos WHERE upper(codigo)=%s AND id IS DISTINCT FROM %s",
                            (cod, d.get("id")), fetch="one")
    if dup and not dup["excluido_em"]:
        raise HTTPException(status_code=409, detail=f"Já existe um treinamento com o código {cod}")
    if dup and not d.get("id"):
        # código de um item excluído antes: reaproveita o registro
        await ajard_query("""UPDATE rh.cursos SET nome=%s, tipo=%s, validade_meses=%s, carga_horaria=%s, ativo=true,
                                    excluido_em=NULL WHERE id=%s""", (nome, tipo, val, ch, dup["id"]), fetch="none")
        return {"ok": True, "id": str(dup["id"])}
    if dup:
        raise HTTPException(status_code=409, detail=f"O código {cod} já foi usado por um treinamento excluído — escolha outro")
    if d.get("id"):
        await ajard_query("""UPDATE rh.cursos SET codigo=%s, nome=%s, tipo=%s, validade_meses=%s, carga_horaria=%s,
                                    ativo=COALESCE(%s, ativo) WHERE id=%s""",
                          (cod, nome, tipo, val, ch, d.get("ativo"), d["id"]), fetch="none")
        return {"ok": True, "id": d["id"]}
    r = await ajard_query("""INSERT INTO rh.cursos (codigo, nome, tipo, validade_meses, carga_horaria, ordem)
                             VALUES (%s,%s,%s,%s,%s,999) RETURNING id""", (cod, nome, tipo, val, ch), fetch="one")
    return {"ok": True, "id": str(r["id"])}


@router.delete("/rh/api/cursos/{kid}")
async def cursos_excluir(kid: str, payload=Depends(verificar_rh)):
    """(15/11/2026) Exclui do catálogo quem nunca foi usado. Com treinamento registrado não exclui —
    desativar (sai das listas e do painel; o histórico das pessoas fica)."""
    await garantir_rh()
    k = await ajard_query("SELECT codigo FROM rh.cursos WHERE id=%s AND excluido_em IS NULL", (kid,), fetch="one")
    if not k:
        raise HTTPException(status_code=404, detail="Treinamento não encontrado")
    n = await ajard_query("SELECT COUNT(*) AS n FROM rh.treinamentos WHERE curso_id=%s AND ativo", (kid,), fetch="one")
    if n and int(n["n"]):
        raise HTTPException(status_code=409, detail=f"{k['codigo']} tem {n['n']} treinamento(s) registrado(s) — não dá para excluir. "
                                                     "Em Editar, marque Ativo = Não: sai das listas e do painel e o histórico fica.")
    await ajard_query("DELETE FROM rh.cargo_cursos WHERE curso_id=%s", (kid,), fetch="none")
    await ajard_query("UPDATE rh.cursos SET excluido_em=now(), ativo=false WHERE id=%s", (kid,), fetch="none")
    return {"ok": True}


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
    if ctype.startswith("image/"):
        dados, ctype = _comprimir(dados, ctype)
    path = _rh_upload(dados, f"colaboradores/{t['colaborador_id']}/certificados/{tid}_{_uuid.uuid4().hex[:8]}.{_TIPOS_CERT[ctype]}", ctype)
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
    url = _rh_link(t["certificado_path"])
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
    await _exigir_identidade(payload)
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
        """SELECT l.em, l.acao, l.usuario_login, u.nome AS usuario_nome, COALESCE(p.nome, d.titulo) AS plataforma
             FROM rh.acessos_log l LEFT JOIN rh.plataformas p ON p.id = l.plataforma_id
             LEFT JOIN rh.documentos d ON d.id = l.documento_id
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


_PARTICULAS = {"DE", "DA", "DO", "DAS", "DOS", "E", "D"}


def _fonetico(t):
    """Grafias equivalentes do português: LUIZ=LUIS, PHELIPE=FELIPE, THIAGO=TIAGO, YAGO=IAGO, letras dobradas."""
    t = t.replace("PH", "F").replace("TH", "T").replace("Y", "I").replace("W", "V").replace("Z", "S").replace("K", "C")
    t = re.sub(r"(.)\1+", r"\1", t)
    return t


def _token_parecido(a, b):
    """(15/11/2026) FILIPI≈FELIPE, LUIZ≈LUIS, ITALO≈ÍTALO: igual, igual na fonética, mesmas consoantes
    (com a mesma inicial) ou muito parecido. Só sugere — quem confirma é a pessoa na prévia."""
    if a == b:
        return True
    fa, fb = _fonetico(a), _fonetico(b)
    if fa == fb:
        return True
    if len(fa) >= 4 and len(fb) >= 4 and fa[0] == fb[0]:
        sa, sb = re.sub(r"[AEIOU]", "", fa), re.sub(r"[AEIOU]", "", fb)
        if len(sa) >= 2 and sa == sb:
            return True
        from difflib import SequenceMatcher
        if SequenceMatcher(None, fa, fb).ratio() >= 0.8:
            return True
    return False


def _pontos_nome(usuario_nome, alvos):
    """Pontuação de "este login é esta pessoa do MAIS" (0 = não é ele). A 1ª palavra do usuário tem de bater
    com alguma do MAIS; +1 por palavra que bate; +1 se bate com o PRIMEIRO nome do MAIS (Jair → JAIR LUIZ…,
    e não Luiz); −0,5 por palavra do usuário que não aparece no MAIS."""
    un = [t for t in _norm(usuario_nome) if t not in _PARTICULAS]
    if not un:
        return 0
    melhor = 0
    for a in alvos:
        a = [t for t in a if t not in _PARTICULAS]
        if not a or not any(_token_parecido(un[0], x) for x in a):
            continue
        bate = sum(1 for t in un if any(_token_parecido(t, x) for x in a))
        pts = bate + (1 if _token_parecido(un[0], a[0]) else 0) - 0.5 * (len(un) - bate)
        melhor = max(melhor, pts)
    return max(melhor, 0)


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
        sug, sug_pts = None, 0
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
                pts = _pontos_nome(u["nome"], alvo)
                if pts:
                    cands.append((pts + (0.5 if u["ativo"] else 0), u))
            cands.sort(key=lambda x: -x[0])
            if cands and (len(cands) == 1 or cands[0][0] > cands[1][0]):
                sug = str(cands[0][1]["id"])
                sug_pts = cands[0][0]
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
            "_pts": (99 if (ficha and ficha["usuario_id"] and sug == str(ficha["usuario_id"])) else sug_pts) if sug else 0,
        })
    # o mesmo login não é sugerido para duas pessoas: fica com quem bate melhor; empate = ninguém
    por_user = {}
    for i, p in enumerate(previa):
        if p["usuario_id"]:
            por_user.setdefault(p["usuario_id"], []).append(i)
    for uid, idxs in por_user.items():
        if len(idxs) > 1:
            idxs.sort(key=lambda i: -previa[i]["_pts"])
            fica = idxs[0] if previa[idxs[0]]["_pts"] > previa[idxs[1]]["_pts"] else None
            for i in idxs:
                if i != fica:
                    previa[i]["usuario_id"] = None
                    previa[i]["avisos"].append("o nome lembra mais de um login — escolha na lista")
    for p in previa:
        p.pop("_pts", None)
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


# ══════════════════════════════════════════════════════════════
# DOCUMENTOS DO COLABORADOR (15/11/2026)
# A pasta "Documentos-NOME" do servidor vira a aba Documentos da ficha; cada subpasta, uma categoria.
# Segurança:
#   1. arquivos do RH num bucket próprio e PRIVADO (garra-rh), criado pelo sistema;
#   2. link de abertura de 5 minutos, gerado na hora, sem reaproveitar;
#   3. categoria sigilosa (atestado, documento pessoal) = só RH completo + registro de quem abriu;
#   4. sigilosos e senhas EAD pedem confirmação de identidade por biometria (Windows Hello/digital)
#      — vale 15 minutos; pode ser desligada em Parametrizar (só administrador).
# ══════════════════════════════════════════════════════════════
import json as _json
import time as _time
import zipfile as _zip
import requests as _req
from core.config import SUPABASE_URL as _SB_URL, SUPABASE_SERVICE_KEY as _SB_KEY

_BUCKET_RH = os.environ.get("RH_BUCKET", "garra-rh")
_BUCKET_OK = False
_LINK_SEG = 300            # link de abertura: 5 minutos
_ELEVACAO_MIN = 15         # confirmação de identidade vale 15 minutos
_MAX_ARQ = 10 * 1024 * 1024
_MAX_ZIP = 60 * 1024 * 1024
_TIPOS_DOC = {"application/pdf": "pdf", "image/jpeg": "jpg", "image/png": "png", "image/webp": "webp",
              "application/msword": "doc",
              "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
              "application/vnd.ms-excel": "xls",
              "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx"}
_EXT_TIPO = {v: k for k, v in _TIPOS_DOC.items()}
_EXT_TIPO.update({"jpeg": "image/jpeg"})


def _sb_headers(ct=None):
    h = {"Authorization": f"Bearer {_SB_KEY}", "apikey": _SB_KEY or ""}
    if ct:
        h["Content-Type"] = ct
    return h


def _garantir_bucket():
    """Cria o bucket privado do RH na primeira vez (idempotente)."""
    global _BUCKET_OK
    if _BUCKET_OK:
        return
    if not _SB_URL or not _SB_KEY:
        raise HTTPException(status_code=500, detail="Armazenamento não configurado")
    r = _req.post(f"{_SB_URL}/storage/v1/bucket", headers=_sb_headers("application/json"),
                  json={"id": _BUCKET_RH, "name": _BUCKET_RH, "public": False}, timeout=15)
    if r.status_code not in (200, 201) and "exist" not in (r.text or "").lower() and r.status_code != 409:
        raise HTTPException(status_code=502, detail=f"Não foi possível preparar a área de arquivos do RH [{r.status_code}]")
    _BUCKET_OK = True


def _rh_upload(dados: bytes, path: str, ctype: str) -> str:
    _garantir_bucket()
    r = _req.post(f"{_SB_URL}/storage/v1/object/{_BUCKET_RH}/{path}", headers={**_sb_headers(ctype), "x-upsert": "true"},
                  data=dados, timeout=60)
    if r.status_code not in (200, 201):
        if r.status_code in (402, 413) or "quota" in (r.text or "").lower() or "exceed" in (r.text or "").lower():
            raise HTTPException(status_code=507, detail="Espaço de armazenamento esgotado (plano gratuito: 1 GB). Arquivo não enviado.")
        raise HTTPException(status_code=502, detail=f"Falha ao guardar o arquivo [{r.status_code}]")
    return f"{_BUCKET_RH}:{path}"


def _rh_link(path: str, segundos: int = _LINK_SEG) -> str:
    """Link assinado curto, sempre novo (sem cache) — também abre os arquivos antigos do garra-fotos."""
    if not path or not _SB_URL:
        return ""
    bucket, real = path.split(":", 1) if ":" in path else ("garra-fotos", path)
    try:
        r = _req.post(f"{_SB_URL}/storage/v1/object/sign/{bucket}/{real}", headers=_sb_headers("application/json"),
                      json={"expiresIn": segundos}, timeout=15)
        if r.status_code == 200:
            return f"{_SB_URL}/storage/v1{r.json().get('signedURL', '')}"
    except Exception as e:
        print("RH: falha ao assinar link:", e)
    return ""


def _comprimir(dados: bytes, ctype: str):
    """Foto/digitalização → JPEG até 2000 px (legível e bem menor). Se falhar, guarda o original."""
    try:
        from core.helpers import comprimir_imagem
        novo = comprimir_imagem(dados, max_px=2000, qualidade=80)
        if len(novo) < len(dados):
            return novo, "image/jpeg"
    except Exception as e:
        print("RH: compressão falhou, mantendo original:", e)
    return dados, ctype


def _tipo_por_nome(nome: str):
    ext = (nome.rsplit(".", 1)[-1] if "." in nome else "").lower()
    return _EXT_TIPO.get(ext)


async def _garantir_documentos():
    await ajard_query("""
        CREATE TABLE IF NOT EXISTS rh.doc_categorias (
            codigo TEXT PRIMARY KEY, nome TEXT NOT NULL,
            sigilosa BOOLEAN NOT NULL DEFAULT false, tem_vencimento BOOLEAN NOT NULL DEFAULT false,
            ver_nr BOOLEAN NOT NULL DEFAULT false, pastas TEXT, ordem INT DEFAULT 0,
            ativo BOOLEAN NOT NULL DEFAULT true, excluido_em TIMESTAMPTZ)""", fetch="none")
    # pastas = começos de nome de subpasta do servidor que caem nesta categoria (importação em lote)
    await ajard_query("""
        INSERT INTO rh.doc_categorias (codigo, nome, sigilosa, tem_vencimento, ver_nr, pastas, ordem) VALUES
          ('ASO','ASO',false,true,true,'ASO',1),
          ('ATESTADO','Atestados',true,false,false,'ATESTADO',2),
          ('CERTIFICADO','Certificados de NR e cursos',false,true,true,'CERTIFICADO,CERTIFICADOS',3),
          ('PESSOAL','Documentos pessoais',true,true,false,'DOC-PESSOAL,DOC PESSOAL,DOCUMENTOS PESSOAIS,DOC_PESSOAL',4),
          ('CONTRATO','Contratos',false,false,false,'CONTRATO',5),
          ('OS_SST','Ordem de serviço de segurança',false,false,true,'OS ,ORDEM DE SERVICO,ORDEM-DE-SERVICO',6),
          ('EPI','Fichas de EPI',false,false,true,'EPI,FICHA DE EPI,FICHA-EPI',7),
          ('RECIBO','Recibos',false,false,false,'RECIBO,RECIBOS',8),
          ('ANTIGO','Arquivo antigo',false,false,false,'DOCS-ANTIGO,ANTIGO',9),
          ('OUTROS','Outros',false,false,false,'',99)
        ON CONFLICT (codigo) DO NOTHING""", fetch="none")
    await ajard_query("""
        CREATE TABLE IF NOT EXISTS rh.documentos (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            colaborador_id UUID NOT NULL, categoria TEXT NOT NULL,
            titulo TEXT NOT NULL, data_doc DATE, vencimento DATE, observacao TEXT,
            origem TEXT DEFAULT 'manual', ativo BOOLEAN NOT NULL DEFAULT true,
            criado_por TEXT, criado_em TIMESTAMPTZ DEFAULT now(), atualizado_em TIMESTAMPTZ DEFAULT now())""", fetch="none")
    await ajard_query("CREATE INDEX IF NOT EXISTS ix_rh_docs_colab ON rh.documentos (colaborador_id) WHERE ativo", fetch="none")
    await ajard_query("""
        CREATE TABLE IF NOT EXISTS rh.documento_arquivos (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            documento_id UUID NOT NULL, path TEXT NOT NULL, nome TEXT, tipo TEXT,
            tamanho BIGINT, sha256 TEXT, ativo BOOLEAN NOT NULL DEFAULT true,
            criado_por TEXT, criado_em TIMESTAMPTZ DEFAULT now())""", fetch="none")
    await ajard_query("CREATE INDEX IF NOT EXISTS ix_rh_docarq_doc ON rh.documento_arquivos (documento_id) WHERE ativo", fetch="none")
    await ajard_query("CREATE INDEX IF NOT EXISTS ix_rh_docarq_sha ON rh.documento_arquivos (sha256) WHERE ativo", fetch="none")
    await ajard_query("ALTER TABLE rh.acessos_log ADD COLUMN IF NOT EXISTS documento_id UUID", fetch="none")
    await ajard_query("""
        CREATE TABLE IF NOT EXISTS rh.config (chave TEXT PRIMARY KEY, valor TEXT,
            atualizado_por TEXT, atualizado_em TIMESTAMPTZ DEFAULT now())""", fetch="none")
    await ajard_query("INSERT INTO rh.config (chave, valor) VALUES ('exigir_biometria','1') ON CONFLICT DO NOTHING", fetch="none")
    await ajard_query("""
        CREATE TABLE IF NOT EXISTS rh.elevacoes (usuario_login TEXT PRIMARY KEY, expira_em TIMESTAMPTZ NOT NULL)""", fetch="none")


async def _config(chave, padrao=None):
    r = await ajard_query("SELECT valor FROM rh.config WHERE chave=%s", (chave,), fetch="one")
    return r["valor"] if r else padrao


# ── confirmação de identidade (biometria) ──
async def _identidade_ok(payload) -> bool:
    if (await _config("exigir_biometria", "1")) != "1":
        return True
    r = await ajard_query("SELECT 1 FROM rh.elevacoes WHERE usuario_login=%s AND expira_em > now()",
                          (payload.get("login") or payload.get("sub"),), fetch="one")
    return bool(r)


async def _exigir_identidade(payload):
    if not await _identidade_ok(payload):
        raise HTTPException(status_code=428, detail="CONFIRMAR_IDENTIDADE")


@router.get("/rh/api/identidade")
async def identidade_status(payload=Depends(verificar_rh)):
    await garantir_rh()
    login = payload.get("login") or payload.get("sub")
    n = await ajard_query("""SELECT COUNT(*) AS n FROM public.credenciais_webauthn c JOIN public.usuarios_garra u ON u.id=c.usuario_id
                              WHERE u.login=%s AND c.ativo""", (login,), fetch="one")
    r = await ajard_query("SELECT expira_em FROM rh.elevacoes WHERE usuario_login=%s AND expira_em > now()", (login,), fetch="one")
    return {"exigir": (await _config("exigir_biometria", "1")) == "1", "biometrias": int(n["n"]) if n else 0,
            "confirmada_ate": r["expira_em"].isoformat() if r else None}


@router.post("/rh/api/identidade/desafio")
async def identidade_desafio(payload=Depends(verificar_rh)):
    from core.webauthn import gerar_opcoes_login
    await garantir_rh()
    login = payload.get("login") or payload.get("sub")
    creds = await ajard_query("""SELECT c.credential_id FROM public.credenciais_webauthn c JOIN public.usuarios_garra u ON u.id=c.usuario_id
                                  WHERE u.login=%s AND c.ativo""", (login,)) or []
    if not creds:
        raise HTTPException(status_code=404, detail="SEM_BIOMETRIA")
    opts, desafio = gerar_opcoes_login([dict(c) for c in creds])
    await ajard_query("DELETE FROM public.webauthn_desafios WHERE login=%s AND tipo='rh'", (login,), fetch="none")
    await ajard_query("INSERT INTO public.webauthn_desafios (login, desafio, tipo) VALUES (%s,%s,'rh')", (login, desafio), fetch="none")
    return _json.loads(opts)


@router.post("/rh/api/identidade/verificar")
async def identidade_verificar(request: Request, payload=Depends(verificar_rh)):
    from core.webauthn import verificar_login
    await garantir_rh()
    login = payload.get("login") or payload.get("sub")
    cred = (await request.json()).get("credencial") or {}
    d = await ajard_query("""SELECT desafio FROM public.webauthn_desafios WHERE login=%s AND tipo='rh'
                              AND criado_em > now() - interval '5 minutes' ORDER BY criado_em DESC LIMIT 1""", (login,), fetch="one")
    if not d:
        raise HTTPException(status_code=400, detail="Tempo esgotado — tente de novo")
    c = await ajard_query("""SELECT c.credential_id, c.public_key, c.sign_count FROM public.credenciais_webauthn c
                              JOIN public.usuarios_garra u ON u.id=c.usuario_id
                             WHERE u.login=%s AND c.credential_id=%s AND c.ativo""", (login, cred.get("id") or ""), fetch="one")
    if not c:
        raise HTTPException(status_code=401, detail="Biometria não reconhecida")
    try:
        novo = verificar_login(cred, d["desafio"], c["public_key"], c["sign_count"] or 0)
    except Exception:
        raise HTTPException(status_code=401, detail="Biometria não reconhecida")
    await ajard_query("UPDATE public.credenciais_webauthn SET sign_count=%s, ultimo_uso=now() WHERE credential_id=%s",
                      (novo, c["credential_id"]), fetch="none")
    await ajard_query("DELETE FROM public.webauthn_desafios WHERE login=%s AND tipo='rh'", (login,), fetch="none")
    await ajard_query(f"""INSERT INTO rh.elevacoes (usuario_login, expira_em) VALUES (%s, now() + interval '{_ELEVACAO_MIN} minutes')
                          ON CONFLICT (usuario_login) DO UPDATE SET expira_em=EXCLUDED.expira_em""", (login,), fetch="none")
    await ajard_query("INSERT INTO rh.acessos_log (usuario_login, acao) VALUES (%s,'confirmou_identidade')", (login,), fetch="none")
    return {"ok": True, "minutos": _ELEVACAO_MIN}


@router.put("/rh/api/config")
async def rh_config_salvar(request: Request, payload=Depends(verificar_rh)):
    """Só o administrador muda a exigência de biometria."""
    await garantir_rh()
    if (payload.get("perfil") or "") != "admin":
        raise HTTPException(status_code=403, detail="Só o administrador altera esta configuração")
    d = await request.json()
    if "exigir_biometria" in d:
        v = "1" if d["exigir_biometria"] else "0"
        await ajard_query("""INSERT INTO rh.config (chave, valor, atualizado_por, atualizado_em) VALUES ('exigir_biometria',%s,%s,now())
                             ON CONFLICT (chave) DO UPDATE SET valor=EXCLUDED.valor, atualizado_por=EXCLUDED.atualizado_por, atualizado_em=now()""",
                          (v, payload.get("sub")), fetch="none")
        await ajard_query("INSERT INTO rh.acessos_log (usuario_login, acao) VALUES (%s,%s)",
                          (payload.get("sub"), "biometria_" + ("ligada" if v == "1" else "desligada")), fetch="none")
    return {"ok": True}


# ── categorias ──
async def _categorias(todas=False):
    rows = await ajard_query("SELECT * FROM rh.doc_categorias WHERE excluido_em IS NULL" + ("" if todas else " AND ativo")
                             + " ORDER BY ordem, nome") or []
    return [dict(r) | {"excluido_em": None} for r in rows]


@router.get("/rh/api/doc-categorias")
async def doc_categorias(payload=Depends(verificar_rh_ver)):
    await garantir_rh()
    cats = await _categorias(todas=True)
    if not await _eh_rh(payload):
        cats = [c for c in cats if c["ver_nr"] and not c["sigilosa"]]
    return cats


@router.post("/rh/api/doc-categorias")
async def doc_categoria_salvar(request: Request, payload=Depends(verificar_rh)):
    await garantir_rh()
    d = await request.json()
    nome = (d.get("nome") or "").strip()
    if not nome:
        raise HTTPException(status_code=422, detail="Informe o nome da categoria")
    cod = (d.get("codigo") or "").strip().upper() or re.sub(r"[^A-Z0-9]+", "_", " ".join(_norm(nome)))[:20]
    pastas = ",".join(p.strip().upper() for p in str(d.get("pastas") or "").split(",") if p.strip()) or None
    vals = (nome, bool(d.get("sigilosa")), bool(d.get("tem_vencimento")), bool(d.get("ver_nr")) and not bool(d.get("sigilosa")),
            pastas, d.get("ativo") if d.get("ativo") is not None else True)
    existe = await ajard_query("SELECT codigo, excluido_em FROM rh.doc_categorias WHERE codigo=%s", (cod,), fetch="one")
    if existe and not d.get("codigo") and not existe["excluido_em"]:
        raise HTTPException(status_code=409, detail="Já existe uma categoria com esse nome")
    if existe:
        await ajard_query("""UPDATE rh.doc_categorias SET nome=%s, sigilosa=%s, tem_vencimento=%s, ver_nr=%s, pastas=%s, ativo=%s,
                                    excluido_em=NULL WHERE codigo=%s""", vals + (cod,), fetch="none")
    else:
        await ajard_query("""INSERT INTO rh.doc_categorias (nome, sigilosa, tem_vencimento, ver_nr, pastas, ativo, codigo, ordem)
                             VALUES (%s,%s,%s,%s,%s,%s,%s,50)""", vals + (cod,), fetch="none")
    return {"ok": True, "codigo": cod}


@router.delete("/rh/api/doc-categorias/{cod}")
async def doc_categoria_excluir(cod: str, payload=Depends(verificar_rh)):
    await garantir_rh()
    n = await ajard_query("SELECT COUNT(*) AS n FROM rh.documentos WHERE categoria=%s AND ativo", (cod,), fetch="one")
    if n and int(n["n"]):
        raise HTTPException(status_code=409, detail=f"Há {n['n']} documento(s) nesta categoria — mude-os de categoria ou desative-a (Ativa = Não)")
    if cod == "OUTROS":
        raise HTTPException(status_code=409, detail="A categoria Outros é usada pela importação e não pode ser excluída")
    await ajard_query("UPDATE rh.doc_categorias SET excluido_em=now(), ativo=false WHERE codigo=%s", (cod,), fetch="none")
    return {"ok": True}


# ── documentos ──
async def _doc(did):
    d = await ajard_query("""SELECT d.*, c.sigilosa, c.nome AS categoria_nome, c.ver_nr FROM rh.documentos d
                               LEFT JOIN rh.doc_categorias c ON c.codigo=d.categoria WHERE d.id=%s AND d.ativo""", (did,), fetch="one")
    if not d:
        raise HTTPException(status_code=404, detail="Documento não encontrado")
    return d


def _st_venc(v, hoje):
    if not v:
        return None
    if v < hoje:
        return "vencida"
    return "vence" if (v - hoje).days <= 30 else "valida"


@router.get("/rh/api/colaboradores/{cid}/documentos")
async def documentos_listar(cid: str, payload=Depends(verificar_rh_ver)):
    await garantir_rh()
    await _colab(cid)
    pode = await _eh_rh(payload)
    cats = {c["codigo"]: c for c in await _categorias(todas=True)}
    rows = await ajard_query("""SELECT d.*, (SELECT json_agg(json_build_object('id',a.id,'nome',a.nome,'tipo',a.tipo,'tamanho',a.tamanho)
                                                      ORDER BY a.criado_em) FROM rh.documento_arquivos a WHERE a.documento_id=d.id AND a.ativo) AS arquivos
                                  FROM rh.documentos d WHERE d.colaborador_id=%s AND d.ativo
                                 ORDER BY d.data_doc DESC NULLS LAST, d.criado_em DESC""", (cid,)) or []
    hoje = _hoje()
    iso = lambda x: x.isoformat() if x else None
    out = []
    for r in rows:
        c = cats.get(r["categoria"]) or {"nome": r["categoria"], "sigilosa": False, "ver_nr": False}
        if not pode and (c["sigilosa"] or not c["ver_nr"]):
            continue
        arqs = r["arquivos"] if isinstance(r["arquivos"], list) else (_json.loads(r["arquivos"]) if r["arquivos"] else [])
        out.append({"id": str(r["id"]), "categoria": r["categoria"], "categoria_nome": c["nome"], "sigilosa": bool(c["sigilosa"]),
                    "titulo": r["titulo"], "data_doc": iso(r["data_doc"]), "vencimento": iso(r["vencimento"]),
                    "status": _st_venc(r["vencimento"], hoje), "observacao": r["observacao"], "origem": r["origem"],
                    "arquivos": arqs})
    # certificados anexados na aba NRs aparecem também aqui (somente leitura)
    certs = await ajard_query("""SELECT t.id, t.certificado_nome, t.data_conclusao, t.validade_ate, k.codigo
                                   FROM rh.treinamentos t JOIN rh.cursos k ON k.id=t.curso_id
                                  WHERE t.colaborador_id=%s AND t.ativo AND t.certificado_path IS NOT NULL""", (cid,)) or []
    for t in certs:
        out.append({"id": "nr:" + str(t["id"]), "categoria": "CERTIFICADO", "categoria_nome": (cats.get("CERTIFICADO") or {}).get("nome", "Certificados"),
                    "sigilosa": False, "titulo": f"Certificado {t['codigo']}", "data_doc": iso(t["data_conclusao"]),
                    "vencimento": iso(t["validade_ate"]), "status": _st_venc(t["validade_ate"], hoje), "observacao": None,
                    "origem": "nr", "arquivos": [{"id": "nr:" + str(t["id"]), "nome": t["certificado_nome"] or "certificado", "tipo": None, "tamanho": None}]})
    return out


async def _guardar_arquivo(cid, did, nome, dados, ctype, quem, origem="manual"):
    if len(dados) > _MAX_ARQ:
        raise HTTPException(status_code=413, detail=f"{nome}: acima de 10 MB")
    if ctype not in _TIPOS_DOC:
        raise HTTPException(status_code=415, detail=f"{nome}: tipo não aceito (PDF, foto, Word ou Excel)")
    if ctype.startswith("image/"):
        dados, ctype = _comprimir(dados, ctype)
    sha = _hl.sha256(dados).hexdigest()
    ja = await ajard_query("""SELECT a.id FROM rh.documento_arquivos a JOIN rh.documentos d ON d.id=a.documento_id
                               WHERE a.sha256=%s AND a.ativo AND d.ativo AND d.colaborador_id=%s LIMIT 1""", (sha, cid), fetch="one")
    if ja:
        return None   # já existe esse mesmo arquivo na ficha
    path = _rh_upload(dados, f"colaboradores/{cid}/docs/{did}/{_uuid.uuid4().hex[:10]}.{_TIPOS_DOC[ctype]}", ctype)
    r = await ajard_query("""INSERT INTO rh.documento_arquivos (documento_id, path, nome, tipo, tamanho, sha256, criado_por)
                             VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                          (did, path, re.sub(r"[^\w.\- ()À-ÿ]", "", nome)[:120], ctype, len(dados), sha, quem), fetch="one")
    return str(r["id"])


@router.post("/rh/api/colaboradores/{cid}/documentos")
async def documento_criar(cid: str, request: Request, payload=Depends(verificar_rh)):
    await garantir_rh()
    await _colab(cid)
    form = await request.form()
    cat = (form.get("categoria") or "").strip()
    if not await ajard_query("SELECT 1 FROM rh.doc_categorias WHERE codigo=%s AND excluido_em IS NULL", (cat,), fetch="one"):
        raise HTTPException(status_code=422, detail="Escolha a categoria")
    titulo = (form.get("titulo") or "").strip()
    arquivos = [f for f in form.getlist("arquivos") if hasattr(f, "read")]
    if not titulo:
        titulo = (arquivos[0].filename.rsplit(".", 1)[0] if arquivos else "")
    if not titulo:
        raise HTTPException(status_code=422, detail="Informe o nome do documento")
    if not arquivos:
        raise HTTPException(status_code=422, detail="Anexe ao menos um arquivo")
    r = await ajard_query("""INSERT INTO rh.documentos (colaborador_id, categoria, titulo, data_doc, vencimento, observacao, criado_por)
                             VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                          (cid, cat, titulo[:150], _data(form.get("data_doc")), _data(form.get("vencimento")),
                           (form.get("observacao") or "").strip() or None, payload.get("sub")), fetch="one")
    did = str(r["id"])
    guardados, repetidos = 0, 0
    try:
        for f in arquivos:
            dados = await f.read()
            ct = (f.content_type or "").lower()
            if ct not in _TIPOS_DOC:
                ct = _tipo_por_nome(f.filename or "") or ct
            if await _guardar_arquivo(cid, did, f.filename or "arquivo", dados, ct, payload.get("sub")):
                guardados += 1
            else:
                repetidos += 1
    except HTTPException:
        if not guardados:
            await ajard_query("UPDATE rh.documentos SET ativo=false WHERE id=%s", (did,), fetch="none")
        raise
    if not guardados:
        await ajard_query("UPDATE rh.documentos SET ativo=false WHERE id=%s", (did,), fetch="none")
        raise HTTPException(status_code=409, detail="Esse arquivo já está na ficha (nada foi duplicado)")
    return {"ok": True, "id": did, "arquivos": guardados, "repetidos": repetidos}


@router.patch("/rh/api/documentos/{did}")
async def documento_editar(did: str, request: Request, payload=Depends(verificar_rh)):
    await garantir_rh()
    await _doc(did)
    d = await request.json()
    v = {}
    if "categoria" in d:
        if not await ajard_query("SELECT 1 FROM rh.doc_categorias WHERE codigo=%s AND excluido_em IS NULL", (d["categoria"],), fetch="one"):
            raise HTTPException(status_code=422, detail="Categoria inválida")
        v["categoria"] = d["categoria"]
    if "titulo" in d:
        if not (d["titulo"] or "").strip():
            raise HTTPException(status_code=422, detail="Informe o nome do documento")
        v["titulo"] = d["titulo"].strip()[:150]
    for k in ("data_doc", "vencimento"):
        if k in d:
            v[k] = _data(d[k])
    if "observacao" in d:
        v["observacao"] = (d["observacao"] or "").strip() or None
    if v:
        await ajard_query(f"UPDATE rh.documentos SET {', '.join(k + '=%s' for k in v)}, atualizado_em=now() WHERE id=%s",
                          tuple(v.values()) + (did,), fetch="none")
    return {"ok": True}


@router.delete("/rh/api/documentos/{did}")
async def documento_excluir(did: str, payload=Depends(verificar_rh)):
    await garantir_rh()
    await _doc(did)
    await ajard_query("UPDATE rh.documentos SET ativo=false, atualizado_em=now() WHERE id=%s", (did,), fetch="none")
    await ajard_query("UPDATE rh.documento_arquivos SET ativo=false WHERE documento_id=%s", (did,), fetch="none")
    return {"ok": True}


@router.post("/rh/api/documentos/{did}/arquivos")
async def documento_mais_arquivos(did: str, request: Request, payload=Depends(verificar_rh)):
    await garantir_rh()
    d = await _doc(did)
    form = await request.form()
    n = 0
    for f in [f for f in form.getlist("arquivos") if hasattr(f, "read")]:
        ct = (f.content_type or "").lower()
        if ct not in _TIPOS_DOC:
            ct = _tipo_por_nome(f.filename or "") or ct
        if await _guardar_arquivo(str(d["colaborador_id"]), did, f.filename or "arquivo", await f.read(), ct, payload.get("sub")):
            n += 1
    return {"ok": True, "arquivos": n}


@router.delete("/rh/api/doc-arquivos/{aid}")
async def documento_arquivo_excluir(aid: str, payload=Depends(verificar_rh)):
    await garantir_rh()
    await ajard_query("UPDATE rh.documento_arquivos SET ativo=false WHERE id=%s", (aid,), fetch="none")
    return {"ok": True}


@router.get("/rh/api/doc-arquivos/{aid}/abrir")
async def documento_arquivo_abrir(aid: str, payload=Depends(verificar_rh_ver)):
    """Link de 5 minutos. Sigiloso: só RH completo, com identidade confirmada e registro de quem abriu."""
    await garantir_rh()
    if aid.startswith("nr:"):
        t = await ajard_query("SELECT certificado_path FROM rh.treinamentos WHERE id=%s AND ativo", (aid[3:],), fetch="one")
        if not t or not t["certificado_path"]:
            raise HTTPException(status_code=404, detail="Sem certificado")
        url = _rh_link(t["certificado_path"])
    else:
        a = await ajard_query("""SELECT a.path, a.documento_id, d.colaborador_id, c.sigilosa, c.ver_nr
                                   FROM rh.documento_arquivos a JOIN rh.documentos d ON d.id=a.documento_id AND d.ativo
                                   LEFT JOIN rh.doc_categorias c ON c.codigo=d.categoria
                                  WHERE a.id=%s AND a.ativo""", (aid,), fetch="one")
        if not a:
            raise HTTPException(status_code=404, detail="Arquivo não encontrado")
        pode = await _eh_rh(payload)
        if not pode and (a["sigilosa"] or not a["ver_nr"]):
            raise HTTPException(status_code=403, detail="Documento restrito ao RH")
        if a["sigilosa"]:
            await _exigir_identidade(payload)
            await ajard_query("""INSERT INTO rh.acessos_log (usuario_login, acao, colaborador_id, documento_id)
                                 VALUES (%s,'abriu_documento',%s,%s)""", (payload.get("sub"), a["colaborador_id"], a["documento_id"]), fetch="none")
        url = _rh_link(a["path"])
    if not url:
        raise HTTPException(status_code=502, detail="Não foi possível abrir o arquivo agora (armazenamento indisponível)")
    return {"url": url, "expira_em_segundos": _LINK_SEG}


@router.get("/rh/api/documentos/uso")
async def documentos_uso(payload=Depends(verificar_rh)):
    await garantir_rh()
    r = await ajard_query("SELECT COALESCE(SUM(tamanho),0) AS b, COUNT(*) AS n FROM rh.documento_arquivos WHERE ativo", fetch="one")
    return {"bytes": int(r["b"]), "arquivos": int(r["n"]), "limite_plano_bytes": 1024 ** 3}


@router.get("/rh/api/documentos/vencimentos")
async def documentos_vencimentos(dias: int = 60, payload=Depends(verificar_rh_ver)):
    """ASO, CNH e outros documentos que vencem nos próximos N dias ou já venceram (colaboradores não desligados)."""
    await garantir_rh()
    pode = await _eh_rh(payload)
    rows = await ajard_query("""SELECT d.id, d.titulo, d.vencimento, d.categoria, c.nome AS categoria_nome, c.sigilosa, c.ver_nr,
                                       co.id AS cid, co.nome
                                  FROM rh.documentos d JOIN rh.colaboradores co ON co.id=d.colaborador_id AND co.ativo AND co.situacao <> 'desligado'
                                  LEFT JOIN rh.doc_categorias c ON c.codigo=d.categoria
                                 WHERE d.ativo AND d.vencimento IS NOT NULL AND d.vencimento <= %s
                                 ORDER BY d.vencimento""", (_hoje() + __import__("datetime").timedelta(days=max(1, min(dias, 365))),)) or []
    hoje = _hoje()
    return [{"id": str(r["id"]), "colaborador_id": str(r["cid"]), "colaborador": r["nome"], "categoria": r["categoria_nome"],
             "titulo": r["titulo"] if (pode or not r["sigilosa"]) else r["categoria_nome"],
             "vencimento": r["vencimento"].isoformat(), "status": _st_venc(r["vencimento"], hoje)}
            for r in rows if pode or (r["ver_nr"] and not r["sigilosa"])]


# ══════════════════════════════════════════════════════════════
# IMPORTAÇÃO EM LOTE DA PASTA DO SERVIDOR (.zip) — prévia → confirmar. Nada é apagado.
#   Documentos-NOME/            → ficha do colaborador (sugestão pelo nome; você confirma)
#   Documentos-NOME/ASO-NOME/…  → categoria pela "pastas" da categoria (começo do nome da subpasta)
#   arquivo solto               → categoria pelo começo do nome do arquivo (Contrato…, OS…, RECIBO…) ou Outros
#   pastas "x-…"                → ex-colaboradores: ignoradas por padrão
# O .zip fica guardado no servidor só até confirmar (máx. 1 h).
# ══════════════════════════════════════════════════════════════
_ZIP_DIR = "/tmp/rh_import"


def _limpa_zips():
    try:
        os.makedirs(_ZIP_DIR, exist_ok=True)
        for f in os.listdir(_ZIP_DIR):
            p = os.path.join(_ZIP_DIR, f)
            if _time.time() - os.path.getmtime(p) > 3600:
                os.remove(p)
    except Exception:
        pass


def _cat_por_nome(nome, cats):
    n = " ".join(_norm(nome))
    n2 = re.sub(r"[^A-Z0-9]+", "-", n)
    melhor = None
    for c in cats:
        for pref in [p.strip() for p in (c.get("pastas") or "").split(",") if p.strip()]:
            pn = " ".join(_norm(pref)) if pref.strip() else ""
            if pn and (n.startswith(pn) or n2.startswith(re.sub(r"[^A-Z0-9]+", "-", pn))):
                if not melhor or len(pn) > melhor[1]:
                    melhor = (c["codigo"], len(pn))
    return melhor[0] if melhor else None


def _nome_da_pasta(p):
    """'Documentos-Eliton José' → 'Eliton José'"""
    return re.sub(r"^\s*(documentos?|docs?)\s*[-_ ]\s*", "", p, flags=re.I).strip()


@router.post("/rh/api/importar-pasta/previa")
async def importar_pasta_previa(ficheiro: UploadFile = File(...), payload=Depends(verificar_rh)):
    await garantir_rh()
    _limpa_zips()
    dados = await ficheiro.read()
    if len(dados) > _MAX_ZIP:
        raise HTTPException(status_code=413, detail="O .zip passa de 60 MB — divida em partes (algumas pessoas por vez)")
    try:
        z = _zip.ZipFile(io.BytesIO(dados))
    except Exception:
        raise HTTPException(status_code=422, detail="Arquivo .zip inválido")
    cats = await _categorias()
    fichas = await ajard_query("SELECT id, nome, apelido FROM rh.colaboradores WHERE ativo ORDER BY nome") or []
    shas = {r["sha256"]: str(r["colaborador_id"]) for r in (await ajard_query(
        """SELECT a.sha256, d.colaborador_id FROM rh.documento_arquivos a JOIN rh.documentos d ON d.id=a.documento_id
            WHERE a.ativo AND d.ativo AND a.sha256 IS NOT NULL""") or [])}
    pessoas, arquivos = {}, []
    for info in z.infolist():
        if info.is_dir():
            continue
        partes = [p for p in info.filename.replace("\\", "/").split("/") if p]
        # nomes do Windows no zip podem vir em CP437 — tenta reconstruir os acentos
        if not (info.flag_bits & 0x800):
            try:
                partes = [p.encode("cp437").decode("cp850") for p in partes]
            except Exception:
                pass
        base = partes[-1]
        if base.startswith(("~$", ".")) or base.lower() in ("thumbs.db", "desktop.ini"):
            continue
        # acha a pasta da pessoa: o primeiro nível "Documentos-…" (ignora a pasta-mãe Documentos-FUNCIONARIOS)
        i_p = next((i for i, p in enumerate(partes[:-1]) if re.match(r"^\s*(x\s*-\s*)?documentos?\s*-", p, re.I)
                    and "FUNCIONARIO" not in p.upper()), None)
        if i_p is None:
            continue
        pasta = partes[i_p]
        ex = bool(re.match(r"^\s*x\s*-", pasta, re.I))
        sub = partes[i_p + 1] if len(partes) - 1 > i_p + 1 else ""
        cat = (_cat_por_nome(sub, cats) if sub else None) or _cat_por_nome(base, cats) or "OUTROS"
        ct = _tipo_por_nome(base)
        dt = None
        try:
            dt = _date(*info.date_time[:3]).isoformat()
        except Exception:
            pass
        if pasta not in pessoas:
            alvo = _norm(_nome_da_pasta(re.sub(r"^\s*x\s*-\s*", "", pasta, flags=re.I)))
            cand = []
            for f in fichas:
                pts = max(_pontos_nome(f["nome"], [alvo]), _pontos_nome(f["apelido"] or "", [alvo]) if f["apelido"] else 0,
                          _pontos_nome(" ".join(alvo), [_norm(f["nome"]), _norm(f["apelido"] or "")]))
                if pts:
                    cand.append((pts, f))
            cand.sort(key=lambda x: -x[0])
            sug = str(cand[0][1]["id"]) if cand and (len(cand) == 1 or cand[0][0] > cand[1][0]) else None
            pessoas[pasta] = {"pasta": pasta, "ex": ex, "colaborador_id": None if ex else sug, "arquivos": 0, "bytes": 0}
        p = pessoas[pasta]
        p["arquivos"] += 1
        p["bytes"] += info.file_size
        arquivos.append({"idx": len(arquivos), "zipname": info.filename, "pasta": pasta, "subpasta": sub, "nome": base,
                         "categoria": cat, "data": dt, "tamanho": info.file_size, "aceito": bool(ct),
                         "importar": bool(ct) and not ex and cat not in ("ANTIGO", "RECIBO")})
    if not arquivos:
        raise HTTPException(status_code=422, detail="Nenhuma pasta 'Documentos-NOME' encontrada no .zip")
    lote = _uuid.uuid4().hex
    with open(os.path.join(_ZIP_DIR, lote + ".zip"), "wb") as fh:
        fh.write(dados)
    uso = await ajard_query("SELECT COALESCE(SUM(tamanho),0) AS b FROM rh.documento_arquivos WHERE ativo", fetch="one")
    return {"lote": lote, "pessoas": list(pessoas.values()), "arquivos": arquivos,
            "colaboradores": [{"id": str(f["id"]), "nome": f["nome"]} for f in fichas],
            "categorias": [{"codigo": c["codigo"], "nome": c["nome"]} for c in cats],
            "uso_bytes": int(uso["b"]), "repetidos_conhecidos": len(shas) > 0}


@router.post("/rh/api/importar-pasta/aplicar")
async def importar_pasta_aplicar(request: Request, payload=Depends(verificar_rh)):
    await garantir_rh()
    d = await request.json()
    lote = re.sub(r"[^a-f0-9]", "", str(d.get("lote") or ""))
    caminho = os.path.join(_ZIP_DIR, lote + ".zip")
    if not lote or not os.path.exists(caminho):
        raise HTTPException(status_code=410, detail="A prévia expirou — envie o .zip de novo")
    pessoa_de = {p["pasta"]: p.get("colaborador_id") for p in (d.get("pessoas") or [])}
    escolhidos = [a for a in (d.get("arquivos") or []) if a.get("importar")]
    cats = {c["codigo"] for c in await _categorias()}
    z = _zip.ZipFile(caminho)
    ok, rep, erros, bytes_ = 0, 0, [], 0
    for a in escolhidos:
        cid = pessoa_de.get(a.get("pasta"))
        if not cid:
            continue
        cat = a.get("categoria") if a.get("categoria") in cats else "OUTROS"
        ct = _tipo_por_nome(a.get("nome") or "")
        if not ct:
            continue
        try:
            dados = z.read(a["zipname"])
            titulo = (a.get("nome") or "arquivo").rsplit(".", 1)[0][:150]
            r = await ajard_query("""INSERT INTO rh.documentos (colaborador_id, categoria, titulo, data_doc, origem, criado_por)
                                     VALUES (%s,%s,%s,%s,'zip',%s) RETURNING id""",
                                  (cid, cat, titulo, _data(a.get("data")), payload.get("sub")), fetch="one")
            aid = await _guardar_arquivo(cid, str(r["id"]), a.get("nome") or "arquivo", dados, ct, payload.get("sub"), "zip")
            if aid:
                ok += 1
                t = await ajard_query("SELECT tamanho FROM rh.documento_arquivos WHERE id=%s", (aid,), fetch="one")
                bytes_ += int(t["tamanho"] or 0)
            else:
                rep += 1
                await ajard_query("UPDATE rh.documentos SET ativo=false WHERE id=%s", (r["id"],), fetch="none")
        except HTTPException as e:
            erros.append(f"{a.get('nome')}: {e.detail}")
            if e.status_code == 507:
                break
        except Exception as e:
            erros.append(f"{a.get('nome')}: {str(e)[:100]}")
    try:
        os.remove(caminho)
    except Exception:
        pass
    return {"ok": True, "importados": ok, "repetidos": rep, "bytes": bytes_, "erros": erros[:50]}
