"""Testa a integração Credify contra um CNPJ real e IMPRIME AS RESPOSTAS CRUAS.

Rode onde a Credify é alcançável (ex: shell do Render) e com as credenciais:
    export CREDIFY_CLIENT_ID=...       # do painel da Credify (Credenciais/API)
    export CREDIFY_CLIENT_SECRET=...
    python scripts/testar_credify.py 20873229000148

Objetivo: ver o JSON REAL de cada endpoint pra confirmar os nomes de campo
(os parsers em finance/credify.py são tolerantes, mas o ideal é validar). O CPF
é MASCARADO na saída (LGPD) — não imprime documento completo no log.

Diagnóstico: se um endpoint der 404, o CAMINHO está diferente do assumido
('/pj/quadrosocietario' e '/pf/telefonecpf') — ajuste em finance/credify.py.
"""
import json
import os
import sys

# roda de qualquer lugar: garante a raiz do repo no path pra achar `finance`
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from finance import credify as cf  # noqa: E402


def _mask_cpf(c: str) -> str:
    d = cf._so_digitos(c)
    return f"***{d[3:6]}***{d[9:]}" if len(d) == 11 else "***"


def _dump(nome: str, obj) -> None:
    print(f"\n===== {nome} =====")
    try:
        print(json.dumps(obj, ensure_ascii=False, indent=2)[:4000])
    except Exception:
        print(repr(obj)[:2000])


def main() -> None:
    cnpj = (sys.argv[1] if len(sys.argv) > 1 else "20873229000148")
    print(f"CNPJ: {cnpj}")
    print("credenciais:", "OK" if cf.tem_credenciais() else "AUSENTES (defina CREDIFY_CLIENT_ID/SECRET)")
    if not cf.tem_credenciais():
        return

    # 1) auth
    try:
        tok = cf._obter_token()
        shape = cf._AUTH_SHAPES[cf._AUTH_SHAPE_OK](".", ".") if cf._AUTH_SHAPE_OK is not None else {}
        print(f"auth: OK (token de {len(tok)} chars) — formato aceito: {list(shape.keys())}")
    except Exception as e:  # noqa: BLE001
        print(f"auth FALHOU: {e}")
        print("  -> 'LOGON OU SENHA INVALIDOS' aqui = os VALORES estão errados "
              "(confira se são da Credify e não da Credly).")
        return

    # 2) quadro societário — RAW
    try:
        raw_qs = cf._post("/quadrosocietario",
                          cf._corpo_consulta(cf._id_qs(), cf._so_digitos(cnpj), "J"))
        _dump("QUADRO SOCIETÁRIO (raw)", raw_qs)
    except Exception as e:  # noqa: BLE001
        print(f"\nquadro societário FALHOU: {e}")
        raw_qs = None

    socios = cf.quadro_societario(cnpj)
    print("\n-- sócios parseados --")
    for s in socios:
        print("  ", {"nome": s["nome"], "cpf": _mask_cpf(s["cpf"]) if s["cpf"] else None,
                     "qualificacao": s["qualificacao"]})

    dec = cf._escolher_decisor(socios)
    print("\ndecisor escolhido:", dec["nome"] if dec else None,
          "| CPF", _mask_cpf(dec["cpf"]) if dec and dec.get("cpf") else "(sem CPF)")

    # 3) telefone por CPF — RAW (só se o decisor tem CPF)
    if dec and dec.get("cpf"):
        try:
            raw_tel = cf._post("/pftelefonecpf",
                               cf._corpo_consulta(cf._id_tel(), dec["cpf"], "F"))
            _dump("TELEFONE POR CPF (raw)", raw_tel)
        except Exception as e:  # noqa: BLE001
            print(f"\ntelefone por CPF FALHOU: {e}")

    # 4) resultado final da orquestração
    final = cf.decisor_com_telefone(cnpj)
    if final.get("telefones") is not None:
        # não vaza nada sensível além do telefone (que é o objetivo)
        pass
    _dump("RESULTADO decisor_com_telefone()", final)


if __name__ == "__main__":
    main()
