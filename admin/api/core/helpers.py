"""core.helpers — compressão de imagem, códigos, semanas e e-mail SMTP."""
# Extraído do main.py na Refatoração Fase 1 (03/07/2026) — código idêntico ao original.

import io, smtplib, calendar
from datetime import datetime, timedelta, date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from PIL import Image
from .config import MAIL_HOST, MAIL_PORT, MAIL_USERNAME, MAIL_PASSWORD, MAIL_CC
from .db import jard_query

# ── HELPERS JARDINAGEM ────────────────────────────────────────
def comprimir_imagem(dados: bytes, max_px: int = 1400, qualidade: int = 82) -> bytes:
    img = Image.open(io.BytesIO(dados))
    if img.mode not in ("RGB","L"):
        img = img.convert("RGB")
    img.thumbnail((max_px, max_px), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=qualidade, optimize=True)
    return buf.getvalue()

def next_code(n: int = 2) -> int:
    row = jard_query("SELECT valor FROM jardinagem.config WHERE chave='next_code'", fetch="one")
    atual = int(row["valor"])
    jard_query("UPDATE jardinagem.config SET valor=%s WHERE chave='next_code'",
               (str(atual + n),), fetch="none")
    return atual

def semanas_do_mes(ano: int, mes: int, mes_id: int):
    _, ultimo_dia = calendar.monthrange(ano, mes)
    intervalos = [(1,7),(8,14),(15,21),(22,ultimo_dia)]
    for i, (ini, fim) in enumerate(intervalos):
        label = f"Semana {i+1} — {ini:02d}/{mes:02d} a {fim:02d}/{mes:02d}/{ano}"
        jard_query("""INSERT INTO jardinagem.semanas
                      (mes_id,label,data_ini,data_fim,ordem,status)
                      VALUES (%s,%s,%s,%s,%s,'aberta')""",
                   (mes_id, label,
                    f"{ano}-{mes:02d}-{ini:02d}",
                    f"{ano}-{mes:02d}-{fim:02d}", i), fetch="none")

def enviar_email_smtp(destino: str, assunto: str, corpo_html: str, anexos: list = None, incluir_cc: bool = True):
    # Suporta múltiplos destinatários separados por vírgula em MAIL_DESTINO e MAIL_CC.
    # incluir_cc=False para emails PESSOAIS (ex: redefinição de senha) — o CC
    # da empresa não pode receber links sensíveis de outros colaboradores.
    lista_to = [e.strip() for e in destino.split(",") if e.strip()]
    lista_cc = [e.strip() for e in MAIL_CC.split(",") if e.strip()] if (MAIL_CC and incluir_cc) else []
    msg = MIMEMultipart("mixed")
    msg["Subject"] = assunto
    msg["From"]    = f"Garra Terraplenagem <{MAIL_USERNAME}>"
    msg["To"]      = ", ".join(lista_to)
    if lista_cc:
        msg["Cc"]  = ", ".join(lista_cc)
    msg.attach(MIMEText(corpo_html, "html", "utf-8"))
    if anexos:
        for nome, dados in anexos:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(dados)
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", f'attachment; filename="{nome}"')
            msg.attach(part)
    destinatarios = lista_to + lista_cc
    with smtplib.SMTP(MAIL_HOST, MAIL_PORT) as s:
        s.ehlo(); s.starttls()
        s.login(MAIL_USERNAME, MAIL_PASSWORD)
        s.sendmail(MAIL_USERNAME, destinatarios, msg.as_string())

