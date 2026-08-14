"""Comissão do vendedor — a conta, num lugar só.

O problema que isto resolve: a comissão era calculada em DOIS lugares, de jeitos
diferentes, e os números nunca batiam. O relatório do painel somava
`lancamentos.membro_id`; o Cockpit somava `prospeccao.valor_estimado_centavos`
(o valor que o próprio vendedor digita no lead). Duas telas, duas verdades, e o
vendedor via um número que o dono não reconhecia.

Aqui a regra é uma só, e as duas telas chamam esta função.

**A base é o RECEBIDO** (regime de caixa): a comissão nasce quando o dinheiro
entra, não quando a venda é prometida. Na prática, o gatilho é o `lancamento` de
receita — que aparece quando o PDV registra uma venda de balcão ou quando um
título a receber é baixado. Escolha deliberada: assim ninguém recebe comissão
sobre venda que o cliente não pagou.

Duas coisas que NÃO entram, de propósito:

* **lead marcado como ganho no Cockpit sem contrato gerado.** Ele só tem o
  `valor_estimado_centavos`, um palpite do vendedor, sem confirmação de ninguém.
  Vira funil (previsão), nunca comissão. Quem transforma em dinheiro é o "Fechar
  contrato", que gera os títulos.
* **título ainda em aberto.** Faturado não é recebido.

O que faz este cálculo funcionar é a ATRIBUIÇÃO estar certa lá atrás: o
`lancamentos.membro_id` tem que apontar pra quem VENDEU. Ver
`empresa.dar_baixa_titulo` (usa `titulos.criado_por`, não quem clicou em "pago")
e a rota do PDV, que passa o operador do caixa.
"""
from __future__ import annotations


# receita da empresa: é isso que vira base de comissão. Venda pessoal (natureza
# 'pessoal') é da vida do dono, não da operação.
_BASE_SQL = """
      from lancamentos l
      left join membros m on m.id = l.membro_id and m.conta_id = l.conta_id
     where l.conta_id=%s and l.tipo='receita' and l.natureza='empresa'
       and l.data >= %s and l.data <= %s
"""


def _pct(v) -> float:
    return float(v) if v is not None else 0.0


def por_vendedor(pool, conta_id: int, ini, fim) -> list[dict]:
    """Uma linha por vendedor no período, ordenada por quem mais vendeu.

    Quem não tem `comissao_pct` configurada aparece com comissão zero e
    `configurada=False` — a tela avisa em vez de esconder a pessoa.
    Vendas sem vendedor atribuído caem numa linha própria (`membro_id` nulo):
    é o sintoma de atribuição faltando, e some da tela se a operação estiver certa.
    """
    with pool.connection() as c:
        rows = c.execute(
            "select l.membro_id, coalesce(nullif(m.nome,''), m.email), m.comissao_pct, "
            "sum(l.valor_centavos), count(*) " + _BASE_SQL +
            " group by l.membro_id, m.nome, m.email, m.comissao_pct "
            "order by sum(l.valor_centavos) desc", (conta_id, ini, fim)).fetchall()
    out = []
    for membro_id, nome, pct_raw, total, n in rows:
        pct = _pct(pct_raw)
        recebido = int(total or 0)
        out.append({
            "membro_id": membro_id,
            "vendedor": nome or "Sem vendedor",
            "sem_vendedor": membro_id is None,
            "recebido_centavos": recebido,
            "n_vendas": int(n or 0),
            "comissao_pct": pct if pct > 0 else None,
            "configurada": pct > 0,
            "comissao_centavos": round(recebido * pct / 100),
        })
    return out


def de_um(pool, conta_id: int, membro_id: int, ini, fim) -> dict:
    """O mesmo cálculo, para UMA pessoa — é o que o Cockpit mostra pro vendedor.

    Passa pela mesma consulta de `por_vendedor` de propósito: se um dia a regra
    mudar, muda para os dois lados juntos, que é justamente o que faltava.
    """
    linha = next((r for r in por_vendedor(pool, conta_id, ini, fim)
                  if r["membro_id"] == membro_id), None)
    if linha:
        return linha
    with pool.connection() as c:
        r = c.execute("select comissao_pct from membros where id=%s and conta_id=%s",
                      (membro_id, conta_id)).fetchone()
    pct = _pct(r[0] if r else None)
    return {"membro_id": membro_id, "vendedor": "", "sem_vendedor": False,
            "recebido_centavos": 0, "n_vendas": 0,
            "comissao_pct": pct if pct > 0 else None, "configurada": pct > 0,
            "comissao_centavos": 0}
