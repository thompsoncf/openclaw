"""O custo da IA vendedora, chamada por chamada (migração 394).

A régua de preço é a MESMA de `core/agent.py` (o assistente do Telegram), que é
onde o produto já precifica tokens: US$ por milhão de tokens, e o câmbio fixo de
R$ 5,40. Duas réguas no mesmo produto seria o painel de custos do admin e o do
desafio discordando sobre a mesma chamada.

Nunca levanta: o custo é conta, não é atendimento.
"""
from __future__ import annotations

import logging

_log = logging.getLogger(__name__)

#: US$ por milhão de tokens: (entrada, escrita de cache, leitura de cache, saída)
PRECOS = {
    "claude-sonnet-4-6": (3.0, 3.75, 0.30, 15.0),
    "claude-haiku-4-5-20251001": (1.0, 1.25, 0.10, 5.0),
}
#: modelo sem preço na tabela conta como o mais caro que ela tem — errar pra cima
#: num painel de custo é o lado seguro
_PADRAO = PRECOS["claude-sonnet-4-6"]
CAMBIO = 5.40


def custo_centavos(modelo: str | None, uso) -> int:
    """Centavos de real de uma chamada, pelo `usage` da resposta."""
    if uso is None:
        return 0
    p_in, p_cw, p_cr, p_out = PRECOS.get(modelo or "", _PADRAO)
    usd = (int(getattr(uso, "input_tokens", 0) or 0) * p_in
           + int(getattr(uso, "cache_creation_input_tokens", 0) or 0) * p_cw
           + int(getattr(uso, "cache_read_input_tokens", 0) or 0) * p_cr
           + int(getattr(uso, "output_tokens", 0) or 0) * p_out) / 1_000_000
    return int(round(usd * CAMBIO * 100))


def registrar(pool, conta_id: int, conversa_id: int | None, prospeccao_id: int | None,
              modelo: str | None, resp) -> None:
    """Uma linha em `ia_uso` pela resposta do modelo. Conexão própria e curta."""
    uso = getattr(resp, "usage", None)
    if uso is None:
        return
    try:
        with pool.connection() as c:
            c.execute(
                """insert into ia_uso (conta_id, conversa_id, prospeccao_id, modelo, input_tokens,
                                       cache_read_tokens, cache_write_tokens, output_tokens,
                                       custo_centavos)
                   values (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (conta_id, conversa_id, prospeccao_id, modelo,
                 int(getattr(uso, "input_tokens", 0) or 0),
                 int(getattr(uso, "cache_read_input_tokens", 0) or 0),
                 int(getattr(uso, "cache_creation_input_tokens", 0) or 0),
                 int(getattr(uso, "output_tokens", 0) or 0),
                 custo_centavos(modelo, uso)))
            c.commit()
    except Exception:  # noqa: BLE001 — banco sem a 394, ou qualquer outra coisa
        _log.info("ia_uso: não registrou (conta=%s conversa=%s)", conta_id, conversa_id)
