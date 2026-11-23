"""routers.politica_acesso — (21/11/2026) Administração do controle de acesso por aparelho e horário (versão simples).

Tudo restrito ao perfil admin (Master). A regra vive em core.acesso_politica. Tudo salva no clique.
  GET/PUT  /acesso/config                     horário de acesso da empresa (um só: dias, início, fim)
  GET      /acesso/usuarios                   lista: chaves de cada pessoa, liberação, nº de aparelhos
  GET/PUT  /acesso/usuario/{uid}              ficha — PUT parcial: so_empresa, so_horario, liberado_de + liberado_ate
  GET      /acesso/aparelhos                  aparelhos (um por aparelho, com quem já entrou nele)
  PUT      /acesso/dispositivo/{id}           classificar / dar nome — vale para o APARELHO (todos os logins nele)
  POST     /acesso/dispositivo/{id}/revogar   e /reativar — idem
  GET      /acesso/registro                   bloqueios dos últimos dias (guarda 90 dias)
  GET      /acesso/meu-modo                   (qualquer pessoa logada) Modo Trabalho ou Pessoal agora — o app consulta
"""
import json
import re
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request

from core.auth import verificar_admin, verificar_token
from core.db import ajard_query
from core.fuso import hoje
from core.acesso_politica import garantir_tabelas_acesso, ler_horario, limpar_cache, modo_acesso, HORARIO_PADRAO

router = APIRouter()

_HORA = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _quem(payload):
    return payload.get("login") or payload.get("sub") or ""


async def _login_de(uid):
    u = await ajard_query("SELECT login FROM public.usuarios_garra WHERE id=%s", (uid,), fetch="one")
    if not u:
        raise HTTPException(status_code=404, detail="Pessoa não encontrada")
    return u["login"]


@router.get("/acesso/config")
async def acesso_config(payload=Depends(verificar_admin)):
    await garantir_tabelas_acesso()
    limpar_cache()
    return {"horario": await ler_horario()}


@router.put("/acesso/config")
async def acesso_config_salvar(request: Request, payload=Depends(verificar_admin)):
    """PUT parcial do horário de acesso: junta com o atual e valida o conjunto."""
    await garantir_tabelas_acesso()
    d = (await request.json()).get("horario") or {}
    limpar_cache()
    h = {**HORARIO_PADRAO, **(await ler_horario())}
    if "dias" in d:
        dias = sorted({int(x) for x in (d.get("dias") or []) if str(x).isdigit() and 1 <= int(x) <= 7})
        if not dias:
            raise HTTPException(status_code=400, detail="Marque ao menos um dia")
        h["dias"] = ",".join(map(str, dias))
    for k in ("inicio", "fim"):
        if k in d:
            v = (d.get(k) or "").strip()
            if not _HORA.match(v):
                raise HTTPException(status_code=400, detail="Horário inválido (formato HH:MM)")
            h[k] = v
    if h["inicio"] >= h["fim"]:
        raise HTTPException(status_code=400, detail="O início deve ser antes do fim")
    await ajard_query("""INSERT INTO public.acesso_config (chave, valor, atualizado_por) VALUES ('horario', %s::jsonb, %s)
                         ON CONFLICT (chave) DO UPDATE SET valor=EXCLUDED.valor, atualizado_em=now(), atualizado_por=EXCLUDED.atualizado_por""",
                      (json.dumps({"dias": h["dias"], "inicio": h["inicio"], "fim": h["fim"]}), _quem(payload)), fetch="none")
    limpar_cache()
    return {"ok": True}


def _pessoa_json(p):
    de, ate = (p.get("liberado_de"), p.get("liberado_ate")) if p else (None, None)
    vale = bool(ate and ate >= hoje())                       # período que já passou não aparece mais
    return {"so_empresa": bool(p and p["so_empresa"]), "so_horario": bool(p and p["so_horario"]),
            "liberado_de": str(de or ate) if vale else None, "liberado_ate": str(ate) if vale else None}


@router.get("/acesso/usuarios")
async def acesso_usuarios(payload=Depends(verificar_admin)):
    await garantir_tabelas_acesso()
    rows = await ajard_query("""
        SELECT u.id, u.login, u.nome, u.perfil, p.so_empresa, p.so_horario, p.liberado_de, p.liberado_ate,
               (SELECT count(*) FROM public.acesso_dispositivos d WHERE d.usuario_id=u.id AND d.ativo AND d.substituido_por IS NULL) AS aparelhos
          FROM public.usuarios_garra u
          LEFT JOIN public.acesso_pessoa p ON p.usuario_id=u.id
         WHERE u.ativo=true
         ORDER BY u.nome, u.login""")
    return [{"id": str(r["id"]), "login": r["login"], "nome": r["nome"], "perfil": r["perfil"], "aparelhos": r["aparelhos"],
             **_pessoa_json(r if r["so_empresa"] is not None else None)} for r in (rows or [])]


@router.get("/acesso/usuario/{uid}")
async def acesso_usuario(uid: str, payload=Depends(verificar_admin)):
    await garantir_tabelas_acesso()
    await _login_de(uid)
    p = await ajard_query("SELECT so_empresa, so_horario, liberado_de, liberado_ate FROM public.acesso_pessoa WHERE usuario_id=%s",
                          (uid,), fetch="one")
    disps = await ajard_query("""SELECT d.id, d.classificacao, d.apelido, d.user_agent, (d.credential_id IS NOT NULL) AS biometria,
                                        d.criado_em, d.ultimo_uso, d.ativo, d.revogado_em,
                                        (SELECT string_agg(COALESCE(u.nome, u.login), ', ' ORDER BY u.nome)
                                           FROM public.acesso_dispositivos o JOIN public.usuarios_garra u ON u.id=o.usuario_id
                                          WHERE o.chave_hash=d.chave_hash AND o.usuario_id<>d.usuario_id) AS usado_por
                                   FROM public.acesso_dispositivos d WHERE d.usuario_id=%s AND d.substituido_por IS NULL
                                  ORDER BY d.ativo DESC, d.ultimo_uso DESC""", (uid,))
    return {"pessoa": _pessoa_json(p), "dispositivos": [{**d, "id": str(d["id"])} for d in (disps or [])]}


@router.put("/acesso/usuario/{uid}")
async def acesso_usuario_salvar(uid: str, request: Request, payload=Depends(verificar_admin)):
    """PUT parcial: so_empresa, so_horario (chaves) e o período liberado_de + liberado_ate ("Liberar fora do
    expediente"; os dois vazios = sem liberação)."""
    await garantir_tabelas_acesso()
    login = await _login_de(uid)
    d = await request.json()
    p = await ajard_query("SELECT so_empresa, so_horario, liberado_de, liberado_ate FROM public.acesso_pessoa WHERE usuario_id=%s",
                          (uid,), fetch="one") or {"so_empresa": False, "so_horario": False, "liberado_de": None, "liberado_ate": None}
    if "so_empresa" in d:
        p["so_empresa"] = bool(d["so_empresa"])
    if "so_horario" in d:
        p["so_horario"] = bool(d["so_horario"])
    if "liberado_de" in d or "liberado_ate" in d:
        de, ate = (d.get("liberado_de") or "").strip(), (d.get("liberado_ate") or "").strip()
        if de or ate:
            try:
                de_d, ate_d = date.fromisoformat(de or ate), date.fromisoformat(ate or de)
            except ValueError:
                raise HTTPException(status_code=400, detail="Período inválido")
            if de_d > ate_d:
                raise HTTPException(status_code=400, detail="O início do período deve ser antes do fim")
            if ate_d < hoje():
                raise HTTPException(status_code=400, detail="Esse período já passou")
            p["liberado_de"], p["liberado_ate"] = de_d, ate_d
        else:
            p["liberado_de"] = p["liberado_ate"] = None
    await ajard_query("""
        INSERT INTO public.acesso_pessoa (usuario_id, so_empresa, so_horario, liberado_de, liberado_ate, atualizado_em, atualizado_por)
        VALUES (%s,%s,%s,%s,%s,now(),%s)
        ON CONFLICT (usuario_id) DO UPDATE SET so_empresa=EXCLUDED.so_empresa, so_horario=EXCLUDED.so_horario,
               liberado_de=EXCLUDED.liberado_de, liberado_ate=EXCLUDED.liberado_ate,
               atualizado_em=now(), atualizado_por=EXCLUDED.atualizado_por""",
        (uid, p["so_empresa"], p["so_horario"], p["liberado_de"], p["liberado_ate"], _quem(payload)), fetch="none")
    limpar_cache(login)
    return {"ok": True}


@router.get("/acesso/aparelhos")
async def acesso_aparelhos(payload=Depends(verificar_admin)):
    """Um item por APARELHO (mesmo navegador = mesmo aparelho), com todos os logins que já entraram nele."""
    await garantir_tabelas_acesso()
    rows = await ajard_query("""
        SELECT d.chave_hash, d.id, d.classificacao, d.apelido, d.user_agent, d.ativo, d.ultimo_uso,
               COALESCE(u.nome, u.login) AS pessoa
          FROM public.acesso_dispositivos d JOIN public.usuarios_garra u ON u.id = d.usuario_id
         WHERE d.substituido_por IS NULL          -- (23/11/2026) duplicado do mesmo aparelho já conciliado não aparece
         ORDER BY d.ultimo_uso DESC""")
    grupos = {}
    for r in rows or []:
        g = grupos.setdefault(r["chave_hash"], {"id": str(r["id"]), "classificacao": r["classificacao"], "apelido": r["apelido"] or "",
                                                "user_agent": r["user_agent"], "ativo": r["ativo"],
                                                "ultimo_uso": r["ultimo_uso"].isoformat() if r["ultimo_uso"] else None, "pessoas": []})
        if r["apelido"] and not g["apelido"]:
            g["apelido"] = r["apelido"]
        g["pessoas"].append(r["pessoa"])
    return list(grupos.values())


@router.put("/acesso/dispositivo/{disp_id}")
async def acesso_dispositivo_salvar(disp_id: str, request: Request, payload=Depends(verificar_admin)):
    """Vale para o aparelho inteiro: todos os logins que entram nele (ex.: celular do administrativo)."""
    d = await request.json()
    sets, params = [], []
    if "classificacao" in d:
        if d["classificacao"] not in ("nao_classificado", "corporativo", "pessoal"):
            raise HTTPException(status_code=400, detail="Classificação inválida")
        sets.append("classificacao=%s"); params.append(d["classificacao"])
    if "apelido" in d:
        sets.append("apelido=%s"); params.append((d["apelido"] or "").strip()[:60])
    if not sets:
        return {"ok": True}
    row = await ajard_query(f"""UPDATE public.acesso_dispositivos SET {', '.join(sets)}
                                 WHERE chave_hash = (SELECT chave_hash FROM public.acesso_dispositivos WHERE id=%s)
                             RETURNING id""", (*params, disp_id), fetch="one")
    if not row:
        raise HTTPException(status_code=404, detail="Aparelho não encontrado")
    limpar_cache()
    return {"ok": True}


@router.post("/acesso/dispositivo/{disp_id}/revogar")
async def acesso_dispositivo_revogar(disp_id: str, payload=Depends(verificar_admin)):
    row = await ajard_query("""UPDATE public.acesso_dispositivos SET ativo=false, revogado_em=now(), revogado_por=%s
                                WHERE chave_hash = (SELECT chave_hash FROM public.acesso_dispositivos WHERE id=%s)
                            RETURNING id""", (_quem(payload), disp_id), fetch="one")
    if not row:
        raise HTTPException(status_code=404, detail="Aparelho não encontrado")
    limpar_cache()
    return {"ok": True}


@router.post("/acesso/dispositivo/{disp_id}/reativar")
async def acesso_dispositivo_reativar(disp_id: str, payload=Depends(verificar_admin)):
    row = await ajard_query("""UPDATE public.acesso_dispositivos SET ativo=true, revogado_em=NULL, revogado_por=NULL
                                WHERE chave_hash = (SELECT chave_hash FROM public.acesso_dispositivos WHERE id=%s)
                            RETURNING id""", (disp_id,), fetch="one")
    if not row:
        raise HTTPException(status_code=404, detail="Aparelho não encontrado")
    limpar_cache()
    return {"ok": True}


@router.get("/acesso/registro")
async def acesso_registro(dias: int = 7, payload=Depends(verificar_admin)):
    await garantir_tabelas_acesso()
    dias = max(1, min(int(dias or 7), 90))
    rows = await ajard_query("""
        SELECT l.em, l.login, u.nome, l.modulo, l.decisao, l.motivo, l.modo,
               d.apelido, d.classificacao
          FROM public.acesso_log l
          LEFT JOIN public.usuarios_garra u ON u.id = l.usuario_id
          LEFT JOIN public.acesso_dispositivos d ON d.id = l.dispositivo_id
         WHERE l.em > now() - make_interval(days => %s)
         ORDER BY l.em DESC LIMIT 500""", (dias,))
    return [{**r, "em": r["em"].isoformat() if r.get("em") else None} for r in (rows or [])]


@router.get("/acesso/meu-modo")
async def acesso_meu_modo(payload=Depends(verificar_token)):
    """O app consulta a cada poucos minutos: se mudou (ex.: deu 17h), recarrega as permissões."""
    login = payload.get("login") or payload.get("sub") or ""
    return await modo_acesso(login, payload.get("disp"))
