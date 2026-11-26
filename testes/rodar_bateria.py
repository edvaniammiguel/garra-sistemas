# Bateria de fluxos do Garra Sistemas — executor (21/11/2026)
# Uso:  python3 testes/rodar_bateria.py            (todos os módulos)
#       python3 testes/rodar_bateria.py OT RH      (só os módulos citados)
# Sai com código 0 quando nada falhou. "NÃO RODOU" não é falha: é teste que este laboratório não comporta.
import sys, os, time, traceback
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bat_base as B

B.travar_producao()
import bat_estatica, bat_fumaca, bat_acesso, bat_os, bat_ot, bat_materiais, bat_abastecimento, bat_compras, bat_rh, bat_logs, bat_despacho, bat_aparelhos, bat_custo_mensal, bat_subsistemas   # noqa: E402,F401  (registram os testes)


def main(so):
    so = {s.lower() for s in so}
    if so != {"estrutura"}:
        st, _ = B.chamar(None, "GET", "/manutencao")
        if st >= 500 or st == 0:
            raise SystemExit("Servidor de teste não respondeu em " + B.URL)
    print(f"Bateria de fluxos — {B.URL} — rodada {B.TAG}\n")
    res, t0, atual = [], time.time(), None
    for modulo, desc, f in B.TESTES:
        if so and modulo.lower() not in so:
            continue
        if modulo != atual:
            print(modulo); atual = modulo
        try:
            f(); r, det = "PASSOU", ""
        except B.Pular as e:
            r, det = "NÃO RODOU", str(e)
        except AssertionError as e:
            r, det = "FALHOU", str(e)
        except KeyError as e:
            r, det = "NÃO RODOU", "depende de um teste anterior que não concluiu (" + str(e) + ")"
        except Exception as e:
            r, det = "ERRO", type(e).__name__ + ": " + str(e)[:200] + " @ " + traceback.format_exc().strip().splitlines()[-3].strip()[:120]
        res.append((modulo, desc, r, det))
        marca = {"PASSOU": "  ✔", "NÃO RODOU": "  –", "FALHOU": "  ✖", "ERRO": "  ✖"}[r]
        print(f"{marca} {desc}" + (f"\n      {r}: {det}" if det else ""))
    n = len(res); ok = sum(1 for x in res if x[2] == "PASSOU"); pul = sum(1 for x in res if x[2] == "NÃO RODOU"); ruim = n - ok - pul
    print(f"\nRESULTADO: {ok} de {n} passaram" + (f" · {pul} não rodaram" if pul else "") + (f" · {ruim} FALHARAM" if ruim else "") + f" · {time.time() - t0:.0f}s")
    return 1 if ruim else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
