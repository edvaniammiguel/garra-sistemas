"""(26/10/2026) Leituras suspeitas — horímetro/km que salta demais em relação à última leitura conhecida
do equipamento (ou recua). Usado pela parte diária e pelo checklist no momento em que a leitura entra.

O que faz quando a leitura é suspeita:
  • exclui a leitura do cálculo na hora (manutencao.leituras_correcao, anulada=true, motivo automático) —
    o documento de origem fica intacto; o Registro Funcionamento lista em "Leituras excluídas" com ↺ Restaurar;
  • avisa quem acompanha a manutenção (notificação 'leitura_suspeita', com quem lançou e de onde veio);
  • devolve True para quem chamou NÃO empurrar o horímetro/km do cadastro com esse valor.
Limites: LIMITE_H por horímetro, LIMITE_KM por km; recuo maior que 1/10 do limite também é suspeito.
"""
import logging
from core.db import ajard_query

log = logging.getLogger("leituras")
LIMITE_H = 500.0
LIMITE_KM = 5000.0


def limites(medicao):
    return (LIMITE_KM, "km") if (medicao or "") == "km" else (LIMITE_H, "h")


def salto(leitura, ultima, medicao):
    """None = normal; 'acima' = salto maior que o limite; 'recuo' = menor que a última em mais de 1/10 do limite."""
    try:
        v, u = float(leitura), float(ultima)
    except (TypeError, ValueError):
        return None
    if not (v > 0) or not (u > 0):
        return None
    lim, _ = limites(medicao)
    if v > u + lim:
        return "acima"
    if v < u - lim / 10:
        return "recuo"
    return None


async def ultima_conhecida(eq_id, fonte=None, ref=None):
    """Maior leitura válida do equipamento sem contar a própria (fonte/ref)."""
    row = await ajard_query(
        """SELECT MAX(leitura) AS l FROM operacional.v_leituras
           WHERE equipamento_id=%s AND NOT (fonte=%s AND ref=%s)""", (eq_id, fonte or "", str(ref or "")), fetch="one")
    if row and row.get("l") is not None:
        return float(row["l"])
    eq = await ajard_query("SELECT horimetro_atual, km_atual, medicao FROM operacional.equipamentos WHERE id=%s", (eq_id,), fetch="one")
    if not eq:
        return None
    v = eq["km_atual"] if (eq.get("medicao") or "") == "km" else eq["horimetro_atual"]
    return float(v) if v is not None else None


async def verificar(eq_id, fonte, ref, leitura, data=None, quem=None, origem=None):
    """Avalia a leitura que acabou de entrar. Retorna True se for suspeita (e já tratada). Nunca levanta."""
    try:
        if eq_id is None or leitura in (None, "", 0):
            return False
        eq = await ajard_query("SELECT codigo, medicao FROM operacional.equipamentos WHERE id=%s", (eq_id,), fetch="one")
        if not eq:
            return False
        ultima = await ultima_conhecida(eq_id, fonte, ref)
        tipo = salto(leitura, ultima, eq.get("medicao"))
        if not tipo:
            return False
        lim, un = limites(eq.get("medicao"))
        v = float(leitura)
        br = lambda x, d=1: f"{x:,.{d}f}".replace(",", "X").replace(".", ",").replace("X", ".")
        motivo = (f"⚠ automático: {'salto' if tipo == 'acima' else 'recuo'} de leitura — {br(v)} {un} contra última conhecida "
                  f"{br(ultima)} {un} (limite {br(lim, 0)} {un}). Confirme ou restaure no Registro Funcionamento.")
        await ajard_query(
            """INSERT INTO manutencao.leituras_correcao (equipamento_id, fonte, ref, data, leitura_original, anulada, motivo)
               VALUES (%s,%s,%s,%s,%s,true,%s)
               ON CONFLICT (fonte, ref) DO NOTHING""", (eq_id, fonte, str(ref), data, v, motivo), fetch="none")
        try:
            from core.notificar import notificar
            await notificar("leitura_suspeita", f"⚠ Leitura suspeita · {eq['codigo']}",
                            f"{br(v)} {un} contra {br(ultima)} {un} conhecidos"
                            + (f" · {origem}" if origem else "") + (f" · lançado por {quem}" if quem else "") + " — excluída do cálculo até conferência",
                            f"/manutencao?func={eq_id}", chave=f"leitura:{fonte}:{ref}")
        except Exception as e:
            log.warning("aviso de leitura suspeita não enviado: %s", e)
        return True
    except Exception as e:
        log.warning("verificar leitura (%s/%s) falhou: %s", fonte, ref, e)
        return False
