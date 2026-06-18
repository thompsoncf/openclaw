"""finance/asaas.py — cliente da API do Asaas (cobrança).

Cobre o fluxo "começa avulsa": cria um LINK DE PAGAMENTO (billingType UNDEFINED =
o cliente escolhe Pix, boleto ou cartão na tela do Asaas) pra uma conta, e devolve
a URL pra mandar no WhatsApp/portal.

Quando o cliente paga, o Asaas chama o webhook /webhook/asaas (Claude Code) com o
evento PAYMENT_RECEIVED/PAYMENT_CONFIRMED. O JSON traz externalReference = conta_id,
que liga o pagamento à conta -> o webhook chama ct.ativar(conta_id, ...).

Config por ambiente (env):
  ASAAS_API_KEY   -> a chave (sandbox ou producao)
  ASAAS_AMBIENTE  -> 'sandbox' (default) ou 'producao'

Fronteira: este arquivo é do assistente (finance/). O webhook e o wiring (botão
no portal, comando no chat) são do Claude Code (ver SPEC).
"""
from __future__ import annotations

import os
import httpx


_SANDBOX = "https://sandbox.asaas.com/api/v3"
_PRODUCAO = "https://api.asaas.com/v3"


def _base_url() -> str:
    amb = (os.environ.get("ASAAS_AMBIENTE", "sandbox") or "sandbox").lower()
    return _PRODUCAO if amb.startswith("prod") else _SANDBOX


def _headers() -> dict:
    key = os.environ.get("ASAAS_API_KEY", "")
    if not key:
        raise RuntimeError("ASAAS_API_KEY nao configurada no ambiente")
    return {"access_token": key, "Content-Type": "application/json"}


class AsaasErro(Exception):
    """Erro vindo da API do Asaas (status != 2xx)."""


def _post(caminho: str, payload: dict, timeout: float = 30.0) -> dict:
    url = _base_url() + caminho
    with httpx.Client(timeout=timeout) as c:
        r = c.post(url, json=payload, headers=_headers())
    if r.status_code >= 300:
        raise AsaasErro(f"Asaas {r.status_code} em {caminho}: {r.text[:300]}")
    return r.json()


def criar_link_pagamento(conta_id: int, nome_plano: str, valor_reais: float,
                         descricao: str | None = None,
                         max_parcelas_cartao: int = 1) -> dict:
    """Cria um link de pagamento avulso pra uma conta.

    - billingType UNDEFINED: o cliente escolhe Pix, boleto ou cartao na tela Asaas.
    - chargeType DETACHED: cobranca avulsa (uma vez). (Recorrente vem depois.)
    - externalReference = conta_id: e' como o webhook descobre QUEM pagou.
    - dueDateLimitDays: prazo do boleto (obrigatorio quando boleto e' possivel;
      com UNDEFINED o Asaas inclui boleto, entao informamos).

    valor_reais em REAIS (ex: 29.00), nao centavos — a API do Asaas usa reais.

    Retorna o JSON do Asaas; os campos uteis sao:
      {"id": "<paymentLink id>", "url": "<link pra mandar ao cliente>", ...}
    """
    payload = {
        "name": f"Zaq — {nome_plano}",
        "description": descricao or f"Assinatura {nome_plano} do Zaq",
        "value": round(float(valor_reais), 2),
        "billingType": "UNDEFINED",       # cliente escolhe Pix/boleto/cartao
        "chargeType": "DETACHED",         # avulsa (uma vez)
        "dueDateLimitDays": 3,            # prazo do boleto, em dias
        "externalReference": str(conta_id),
        "notificationEnabled": True,
    }
    if max_parcelas_cartao and max_parcelas_cartao > 1:
        payload["chargeType"] = "INSTALLMENT"
        payload["maxInstallmentCount"] = int(max_parcelas_cartao)
    return _post("/paymentLinks", payload)


def criar_cobranca_avulsa(asaas_customer_id: str, valor_reais: float,
                          vencimento_iso: str, descricao: str,
                          conta_id: int) -> dict:
    """Alternativa: cobranca direta pra um cliente JA existente no Asaas.

    Util se voce preferir criar o cliente no Asaas primeiro (com CPF/CNPJ) e depois
    a cobranca. Para o fluxo simples (link de pagamento), use criar_link_pagamento.

    vencimento_iso no formato 'AAAA-MM-DD'.
    """
    payload = {
        "customer": asaas_customer_id,
        "billingType": "UNDEFINED",
        "value": round(float(valor_reais), 2),
        "dueDate": vencimento_iso,
        "description": descricao,
        "externalReference": str(conta_id),
    }
    return _post("/payments", payload)


def criar_cliente(nome: str, cpf_cnpj: str | None = None,
                  email: str | None = None, telefone: str | None = None,
                  conta_id: int | None = None) -> dict:
    """Cria um cliente no Asaas. Retorna o JSON (campo 'id' = customer id).

    So' precisa se voce for usar criar_cobranca_avulsa (cobranca por customer).
    Pro link de pagamento, o Asaas cria o cliente sozinho no preenchimento.
    """
    payload: dict = {"name": nome}
    if cpf_cnpj:
        payload["cpfCnpj"] = cpf_cnpj
    if email:
        payload["email"] = email
    if telefone:
        payload["mobilePhone"] = telefone
    if conta_id is not None:
        payload["externalReference"] = str(conta_id)
    return _post("/customers", payload)


def sandbox_confirmar_pagamento(payment_id: str) -> dict:
    """Confirma um pagamento no SANDBOX (simula o cliente pagando), sem cartao real.

    So' use em sandbox. Em producao isso nao existe — o pagamento e' real.
    """
    return _post(f"/payments/{payment_id}/confirmRealReceiveSandbox", {})
