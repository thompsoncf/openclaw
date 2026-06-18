"""finance/degustacao.py — modo DEGUSTAÇÃO pro cliente NOVO (lead).

O desconhecido que chega pela landing pode testar ATÉ 5 gastos digitados ANTES de
cadastrar. O gasto é processado DE VERDADE pelo agente (o lead vê a mágica), mas
cai numa conta-degustação isolada ("Visitantes"), separada das contas reais.

Fronteira: este arquivo é do assistente (finance/). Quem chama (webhook) e a
tabela de leads são do Claude Code (ver SPECs).

Uso no webhook (lead identificado, dentro do limite de 5):
    from finance.degustacao import responder_degustacao
    resposta = responder_degustacao(pool, brain, texto, cidade=cidade_do_lead)
    _responder_whatsapp(to, resposta)

A conta-degustação é criada UMA vez (status 'degustacao') e reusada por todos os
leads. Os lançamentos dela não contam como cliente real e podem ser limpos a
qualquer momento (delete from lancamentos where conta_id = <id degustacao>).
"""
from __future__ import annotations

from core.brain import Brain
from core.memory import MemoriaConversa
from finance.livro_caixa import LivroCaixa
from finance.agente_financeiro import criar_agente_financeiro


# nome fixo da conta-degustação (uma só, compartilhada por todos os visitantes)
CONTA_DEGUSTACAO_NOME = "__degustacao_visitantes__"


def _id_conta_degustacao(pool) -> int:
    """Retorna o id da conta-degustação, criando-a na primeira vez.

    Não usa contas.criar_conta de propósito: a degustação é uma conta técnica
    (status 'degustacao'), fora do fluxo trial/ativa. Idempotente por nome.
    """
    with pool.connection() as conn:
        row = conn.execute(
            "select id from contas where nome = %s limit 1",
            (CONTA_DEGUSTACAO_NOME,),
        ).fetchone()
        if row:
            return int(row[0])
        row = conn.execute(
            """insert into contas (tipo, nome, plano, status)
               values ('pf', %s, NULL, 'suspensa')
               returning id""",
            (CONTA_DEGUSTACAO_NOME,),
        ).fetchone()
        conn.commit()
        return int(row[0])


def responder_degustacao(pool, brain: Brain, texto: str,
                         cidade: str | None = None) -> str:
    """Processa UM gasto digitado de um lead, de verdade, e devolve a resposta.

    - Sem memória persistente entre mensagens (cada teste é stateless: o lead
      ainda não é cliente, não guardamos histórico de conversa dele).
    - Sem banco de preços e sem lista (degustação enxuta = mais barato).
    - O lançamento cai na conta-degustação isolada.
    - NÃO processa cupom/foto aqui: o webhook deve barrar mídia antes de chamar
      esta função (cupom é só pós-cadastro). Esta função é só pra TEXTO.
    """
    conta_id = _id_conta_degustacao(pool)
    livro = LivroCaixa(pool, conta_id, None)
    memoria = MemoriaConversa()  # efêmera: só esta troca
    agente = criar_agente_financeiro(
        brain, livro, memoria,
        lista=None, papel="dono", banco=None, cidade=cidade,
    )
    resposta = agente.responder(texto, None, "image/jpeg")
    return resposta or "Pronto! Anotei aqui. 👍"
