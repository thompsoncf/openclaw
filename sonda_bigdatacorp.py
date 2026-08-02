#!/usr/bin/env python3
"""Sonda da API da BigDataCorp - MISSAO DE RECONHECIMENTO.

NAO faz parte do sistema. E' um script de teste pra rodar NO RENDER (onde a
rede e' liberada), pra responder a pergunta que importa antes de integrar de
verdade: o quadro societario da BigDataCorp devolve o CPF do socio COMPLETO
(como a Credify) ou MASCARADO (como a Receita Federal publica de graca)?

Se vier completo, tentamos a 2a parte: CPF -> telefone/WhatsApp, do mesmo jeito
que finance/credify.py ja faz hoje (quadro_societario -> decisor -> telefone).
Se vier mascarado, a BigDataCorp NAO substitui a Credify nesse fluxo - so' serve
pra outra coisa (dados cadastrais, por ex).

NAO MEXE em nada do sistema (nao grava no banco, nao chama finance/credify.py).
So' LE a resposta da API deles e imprime tudo, pra gente montar o cliente real
com base no formato de verdade (em vez de chutar campos).

Como rodar no Render:
  1) No Render (openclaw-web -> Environment), adicione as 2 credenciais que a
     BigDataCorp te deu ao criar a conta (BDC Center -> Tokens):
       BIGDATACORP_TOKEN_ID
       BIGDATACORP_ACCESS_TOKEN
  2) Troque CNPJ_TESTE abaixo por um CNPJ de verdade da SUA base (uma clinica
     pequena, tipo as que voce ja prospecta) - o teste so' vale a pena com um
     CNPJ desse perfil, nao com uma empresa grande.
  3) Suba este arquivo (git push) e no Shell do servico rode:
       python sonda_bigdatacorp.py
  4) Copie TODA a saida e cole no chat.
"""
import json
import os
import re
import ssl
import urllib.error
import urllib.request

# TROQUE por um CNPJ real da sua base antes de rodar (só dígitos ou formatado,
# tanto faz). O default abaixo é só pra não quebrar se esquecer de trocar -
# é o CNPJ público da Petrobras, não vai te dizer nada útil sobre o teu nicho.
CNPJ_TESTE = "33000167000101"

BASE = "https://plataforma.bigdatacorp.com.br"
_CTX = ssl.create_default_context()


def _so_digitos(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def _headers() -> dict:
    return {
        "accept": "application/json",
        "content-type": "application/json",
        "TokenId": (os.environ.get("BIGDATACORP_TOKEN_ID") or "").strip(),
        "AccessToken": (os.environ.get("BIGDATACORP_ACCESS_TOKEN") or "").strip(),
    }


def _post(caminho: str, corpo: dict):
    url = BASE + caminho
    data = json.dumps(corpo).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=_headers(), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=25, context=_CTX) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        try:
            return e.code, e.read(2000).decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            return e.code, ""
    except Exception as e:  # noqa: BLE001
        return None, f"ERRO DE REDE: {e}"


def _secao(t: str):
    print("\n" + "=" * 72 + f"\n{t}\n" + "=" * 72)


def _mostra(status, body: str):
    print(f"STATUS: {status}")
    try:
        j = json.loads(body)
        print(json.dumps(j, indent=2, ensure_ascii=False)[:4000])
        return j
    except Exception:  # noqa: BLE001
        print("(nao veio JSON) primeiros 800 chars:")
        print((body or "")[:800])
        return None


def _cpfs_no_texto(txt: str) -> list[str]:
    """Acha sequências de 11 dígitos (candidatos a CPF) no JSON bruto, sem
    depender de saber o nome do campo de antemão."""
    return sorted(set(re.findall(r"\b\d{11}\b", txt or "")))


def _cpfs_mascarados_no_texto(txt: str) -> list[str]:
    """Formato clássico da Receita: 3 dígitos + asteriscos + 2 dígitos (ex.:
    123.***.**9-01 ou 123******901)."""
    return sorted(set(re.findall(r"\d{3}\.?\*{3}\.?\*{2}\d?-?\d{2}", txt or "")))


def main():
    if not (os.environ.get("BIGDATACORP_TOKEN_ID") and os.environ.get("BIGDATACORP_ACCESS_TOKEN")):
        print("FALTAM as env BIGDATACORP_TOKEN_ID / BIGDATACORP_ACCESS_TOKEN. "
              "Configure no Render (Environment) antes de rodar.")
        return
    cnpj = _so_digitos(CNPJ_TESTE)
    print(f"SONDA BIGDATACORP - reconhecimento de quadro societario + CPF->telefone")
    print(f"CNPJ de teste: {cnpj}")

    # -------- A) quadro societario: CNPJ -> sócios (CPF completo ou mascarado?)
    _secao("A) Quadro societário (CNPJ -> sócios) - candidatos de Datasets")
    candidatos_empresa = [
        "empresas_dynamic_qsa_data",
        "registration_data",
        "basic_data,empresas_dynamic_qsa_data",
    ]
    cpfs_completos = []
    for ds in candidatos_empresa:
        print(f"\n--- Datasets='{ds}' ---")
        status, body = _post("/empresas", {"q": f"doc{{{cnpj}}}", "Datasets": ds})
        j = _mostra(status, body)
        bruto = json.dumps(j, ensure_ascii=False) if j else (body or "")
        completos = _cpfs_no_texto(bruto)
        mascarados = _cpfs_mascarados_no_texto(bruto)
        if completos:
            print(f"  >>> achou {len(completos)} sequência(s) de 11 dígitos (candidato a CPF completo): {completos}")
            cpfs_completos.extend(completos)
        if mascarados:
            print(f"  >>> achou CPF MASCARADO (formato Receita): {mascarados}")

    # -------- B) CPF -> telefone/WhatsApp (só se achou CPF completo acima)
    _secao("B) CPF -> telefone/WhatsApp - candidatos de Datasets")
    if not cpfs_completos:
        print("Nenhum CPF completo (11 dígitos sem máscara) apareceu na parte A.")
        print("Isso é forte indício de que o quadro societário da BigDataCorp vem")
        print("MASCARADO (igual à Receita Federal pública) — nesse caso ela NÃO")
        print("substitui a Credify nesse fluxo específico. Pula a parte B.")
    else:
        cpf_teste = cpfs_completos[0]
        print(f"Usando o 1º CPF candidato achado: {cpf_teste}")
        candidatos_pessoa = [
            "basic_data",
            "phones",
            "contact_data",
            "phone_score_data_person",
        ]
        for ds in candidatos_pessoa:
            print(f"\n--- Datasets='{ds}' ---")
            status, body = _post("/pessoas", {"q": f"doc{{{cpf_teste}}}", "Datasets": ds})
            _mostra(status, body)

    _secao("FIM - cola TODA esta saída no chat")


if __name__ == "__main__":
    main()
