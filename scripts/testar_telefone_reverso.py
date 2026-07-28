"""Testa a consulta REVERSA de telefone da Credify (número -> titular).

Consulta 'PF Telefone' (/pftelefone): entra DDD+Telefone, sai CPF/NOME/endereço do
titular. IdConsulta é PRÓPRIO dessa consulta (a Credify libera à parte) — pega no
painel account.credify.com.br (linha "API PF Telefone - ID XXX").

Rode no Shell do Render:
    export CREDIFY_CLIENT_ID=45547
    export CREDIFY_CLIENT_SECRET=67797495
    export CREDIFY_ID_TELREV=<id da consulta PF Telefone>
    python scripts/testar_telefone_reverso.py 86994397117
    # ou passando o IdConsulta direto como 2º argumento:
    # python scripts/testar_telefone_reverso.py 86994397117 <IdConsulta>

O CPF é MASCARADO na saída (LGPD).
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from finance import credify as cf  # noqa: E402


def _mask_cpf(c):
    d = cf._so_digitos(c or "")
    return f"***{d[3:6]}***{d[9:]}" if len(d) == 11 else "***"


def main():
    if len(sys.argv) < 2:
        print("uso: python scripts/testar_telefone_reverso.py <DDD+numero> [IdConsulta]")
        return
    numero = cf._so_digitos(sys.argv[1])
    idc = sys.argv[2] if len(sys.argv) > 2 else cf._id_telrev()
    print(f"NUMERO: {numero}  IdConsulta: {idc or '(não setado)'}")
    if not cf.tem_credenciais():
        print("faltam CREDIFY_CLIENT_ID / CREDIFY_CLIENT_SECRET"); return
    if not idc:
        print(">>> FALTA o IdConsulta da consulta 'PF Telefone'. Pegue no painel "
              "(account.credify.com.br) e rode com: export CREDIFY_ID_TELREV=<id>")
        return
    try:
        tok = cf._obter_token()
        print(f"auth OK (token {len(tok)} chars)")
    except Exception as e:  # noqa: BLE001
        print(f"auth FALHOU: {e}"); return

    ddd, tel = numero[:2], numero[2:]
    corpo = {"Consulta": {"IdConsulta": str(idc), "Ddd": ddd, "Telefone": tel, "TipoPessoa": "F"}}
    try:
        raw = cf._post("/pftelefone", corpo)
        # mascara o CPF no dump cru
        import re
        crua = re.sub(r'("CPF"\s*:\s*")(\d{11})(")', lambda m: m.group(1) + "***" + m.group(3),
                      json.dumps(raw, ensure_ascii=False, indent=2))
        print("\n===== PF TELEFONE (raw, CPF mascarado) =====")
        print(crua[:4000])
    except Exception as e:  # noqa: BLE001
        print(f"\n/pftelefone FALHOU: {e}")

    r = cf.titular_por_telefone("", numero, id_consulta=idc)
    print("\n===== RESULTADO titular_por_telefone() =====")
    if r.get("titulares"):
        for t in r["titulares"]:
            t = dict(t); t["cpf"] = _mask_cpf(t["cpf"]) if t.get("cpf") else None
            print(" ", t)
    print("ok:", r.get("ok"), "| erro:", r.get("erro"))


if __name__ == "__main__":
    main()
