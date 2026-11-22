"""routers.logs_sistema_consulta — (22/11/2026) Página e consulta do log do sistema (somente leitura).

Restrito ao perfil admin (Master). A gravação vive em core.logs_sistema.
  GET /logs-sistema                      a página
  GET /logs-sistema/api/lista            eventos do período (de, ate, modulo, q)
  GET /logs-sistema/api/evento/{id}      um evento: o que mudou + rastreio completo do item
  GET /logs-sistema/api/relatorio        o que está na tela, em Excel (formato=xlsx) ou PDF (formato=pdf)
Não existe rota para alterar ou apagar log.
"""
import io
import os
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse

from core.auth import verificar_admin
from core.config import ADMIN_DIR
from core.db import ajard_query
from core.fuso import hoje, inicio_dia, dia_seguinte, TZ_SQL
from core.logs_sistema import garantir_log_sistema

router = APIRouter()

MAX_TELA = 1000          # linhas na tela
MAX_RELATORIO = 5000     # linhas no relatório
MODULOS = ["Operacional", "Manutenção", "Compras", "Abastecimento", "RH", "Checklist", "Jardinagem",
           "Atendimento", "Acesso", "Administração"]


@router.get("/logs-sistema", response_class=HTMLResponse)
async def pagina_logs():
    p = os.path.join(ADMIN_DIR, "logs-sistema.html")
    if not os.path.isfile(p):
        raise HTTPException(status_code=404, detail="Página de logs não encontrada")
    return HTMLResponse(open(p, encoding="utf-8").read(),
                        headers={"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"})


def _periodo(de, ate):
    """Padrão: últimos 7 dias (hoje e os 6 anteriores). Nunca devolve período vazio ou invertido."""
    h = hoje()
    try:
        d_ate = date.fromisoformat(ate) if ate else h
    except ValueError:
        raise HTTPException(status_code=422, detail="Data final inválida")
    try:
        d_de = date.fromisoformat(de) if de else d_ate - timedelta(days=6)
    except ValueError:
        raise HTTPException(status_code=422, detail="Data inicial inválida")
    if d_de > d_ate:
        d_de, d_ate = d_ate, d_de
    return d_de, d_ate


async def _buscar(de, ate, modulo, q, limite, com_detalhe=False):
    await garantir_log_sistema()
    d_de, d_ate = _periodo(de, ate)
    cond, par = ["em >= %s", "em < %s"], [inicio_dia(d_de), dia_seguinte(d_ate)]
    if modulo and modulo != "Todos":
        cond.append("modulo = %s")
        par.append(modulo)
    q = (q or "").strip()
    if q:
        cond.append("(usuario_nome ILIKE %s OR item_rotulo ILIKE %s OR evento ILIKE %s OR resumo ILIKE %s "
                    "OR modulo ILIKE %s OR motivo ILIKE %s)")
        par += ["%" + q + "%"] * 6
    onde = " AND ".join(cond)
    total = await ajard_query(f"SELECT count(*) AS n FROM public.log_sistema WHERE {onde}", tuple(par), fetch="one")
    linhas = await ajard_query(
        f"""SELECT id, to_char(em AT TIME ZONE '{TZ_SQL}', 'DD/MM/YYYY') AS data,
                   to_char(em AT TIME ZONE '{TZ_SQL}', 'HH24:MI') AS hora,
                   usuario_nome AS usuario, modulo, item_rotulo AS item, evento, resumo, motivo
                   {", detalhe" if com_detalhe else ""}
            FROM public.log_sistema WHERE {onde}
            ORDER BY em DESC, id DESC LIMIT {int(limite)}""", tuple(par))
    return d_de, d_ate, int((total or {}).get("n") or 0), linhas or []


@router.get("/logs-sistema/api/lista")
async def logs_lista(de: str = None, ate: str = None, modulo: str = None, q: str = None, _=Depends(verificar_admin)):
    d_de, d_ate, total, linhas = await _buscar(de, ate, modulo, q, MAX_TELA)
    return {"de": d_de.isoformat(), "ate": d_ate.isoformat(), "total": total, "mostrando": len(linhas),
            "modulos": MODULOS, "linhas": linhas}


def _json(v):
    import json
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:
            return None
    return v


@router.get("/logs-sistema/api/evento/{log_id}")
async def logs_evento(log_id: int, _=Depends(verificar_admin)):
    await garantir_log_sistema()
    ev = await ajard_query(
        f"""SELECT id, to_char(em AT TIME ZONE '{TZ_SQL}', 'DD/MM/YYYY') AS data,
                   to_char(em AT TIME ZONE '{TZ_SQL}', 'HH24:MI:SS') AS hora,
                   usuario_nome AS usuario, modulo, item_tipo, item_id, item_rotulo AS item, evento, resumo,
                   origem, motivo, status, metodo, rota, detalhe
            FROM public.log_sistema WHERE id=%s""", (log_id,), fetch="one")
    if not ev:
        raise HTTPException(status_code=404, detail="Evento não encontrado")
    ev["detalhe"] = _json(ev.get("detalhe")) or []
    rastreio = []
    if ev.get("item_tipo") and ev.get("item_id"):
        rastreio = await ajard_query(
            f"""SELECT id, to_char(em AT TIME ZONE '{TZ_SQL}', 'DD/MM HH24:MI') AS quando,
                       usuario_nome AS usuario, evento, resumo
                FROM public.log_sistema WHERE item_tipo=%s AND item_id=%s
                ORDER BY em, id LIMIT 300""", (ev["item_tipo"], ev["item_id"])) or []
    ev.pop("item_tipo", None)
    ev.pop("item_id", None)
    return {"evento": ev, "rastreio": rastreio}


_COLUNAS = ["Data", "Hora", "Usuário", "Módulo", "Item", "Evento"]


@router.get("/logs-sistema/api/relatorio")
async def logs_relatorio(formato: str = "xlsx", de: str = None, ate: str = None, modulo: str = None, q: str = None,
                         _=Depends(verificar_admin)):
    """O que está na tela (mesmo período, módulo e pesquisa). PDF: só a grade. Excel: grade + o que mudou."""
    if formato not in ("xlsx", "pdf"):
        raise HTTPException(status_code=422, detail="Formato inválido (use xlsx ou pdf)")
    d_de, d_ate, total, linhas = await _buscar(de, ate, modulo, q, MAX_RELATORIO, com_detalhe=(formato == "xlsx"))
    titulo = "Logs do sistema"
    sub = f"{d_de.strftime('%d/%m/%Y')} a {d_ate.strftime('%d/%m/%Y')}" \
          + (f" · {modulo}" if modulo and modulo != "Todos" else "") + (f" · pesquisa: {q.strip()}" if (q or "").strip() else "")
    nome = f"logs-{d_de.isoformat()}-a-{d_ate.isoformat()}.{formato}"
    bio = io.BytesIO()
    if formato == "xlsx":
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment
        wb = Workbook()
        ws = wb.active
        ws.title = "Logs"
        ws.append([titulo])
        ws.append([sub + f" · {len(linhas)} de {total} registro(s)"])
        ws.append([])
        cab = _COLUNAS + ["O que aconteceu", "Campo", "Antes", "Depois", "Motivo da recusa"]
        ws.append(cab)
        ws["A1"].font = Font(name="Arial", bold=True, size=14, color="1A2A5E")
        ws["A2"].font = Font(name="Arial", size=10, color="55637A")
        for c in ws[4]:
            c.font = Font(name="Arial", bold=True, color="FFFFFF")
            c.fill = PatternFill("solid", fgColor="1A2A5E")
            c.alignment = Alignment(vertical="center")
        for l in linhas:
            fixo = [l["data"], l["hora"], l["usuario"], l["modulo"], l["item"], l["evento"], l["resumo"]]
            difs = _json(l.get("detalhe")) or []
            if not difs:
                ws.append(fixo + [None, None, None, l["motivo"]])
            for d in difs:
                ws.append(fixo + [d.get("campo"), d.get("antes") or "(vazio)", d.get("depois") or "(vazio)", l["motivo"]])
        for i, w in enumerate([12, 8, 22, 16, 14, 16, 52, 22, 22, 22, 40], start=1):
            ws.column_dimensions[chr(64 + i)].width = w
        for row in ws.iter_rows(min_row=5):
            for c in row:
                c.font = Font(name="Arial", size=10)
        ws.freeze_panes = "A5"
        wb.save(bio)
        tipo = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
        st = getSampleStyleSheet()
        azul = colors.HexColor("#1A2A5E")
        doc = SimpleDocTemplate(bio, pagesize=A4, leftMargin=14 * mm, rightMargin=14 * mm, topMargin=14 * mm,
                                bottomMargin=14 * mm, title=titulo)
        cel = ParagraphStyle("cel", parent=st["Normal"], fontSize=8.5, leading=10.5)
        dados = [_COLUNAS] + [[l["data"], l["hora"], Paragraph(l["usuario"] or "", cel), l["modulo"],
                               Paragraph(l["item"] or "", cel), l["evento"]] for l in linhas]
        t = Table(dados, colWidths=[22 * mm, 14 * mm, 46 * mm, 32 * mm, 34 * mm, 34 * mm], repeatRows=1)
        t.setStyle(TableStyle([
            ("FONTSIZE", (0, 0), (-1, -1), 8.5),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("TEXTCOLOR", (0, 0), (-1, 0), azul),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F1F5F9")),
            ("LINEBELOW", (0, 0), (-1, -1), 0.4, colors.HexColor("#E2E8F0")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        doc.build([
            Paragraph(f'<font color="#1A2A5E" size="14"><b>Garra Terraplenagem · {titulo}</b></font>', st["Normal"]),
            Spacer(1, 2 * mm),
            Paragraph(f'<font color="#55637A" size="9">{sub} · {len(linhas)} de {total} registro(s)</font>', st["Normal"]),
            Spacer(1, 5 * mm), t])
        tipo = "application/pdf"
    bio.seek(0)
    return StreamingResponse(bio, media_type=tipo, headers={"Content-Disposition": f'attachment; filename="{nome}"'})
