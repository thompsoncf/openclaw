"""Agente conversacional da cesta (WhatsApp) — Camada 1: RESPONDER (read-only).

O assinante de cesta conversa pelo WhatsApp. Este agente reconhece que ele é
assinante de cesta (não cliente do app financeiro) e responde sobre a CESTA:
- quando chega a próxima entrega
- o que vem na cesta dessa semana
- quanto custa e quando paga

CAMADA 1 (esta): só lê dados (consultar/listar/explicar). Não muda nada no banco —
seguro pra testar primeiro. As AÇÕES (remover item, pausar, cancelar, confirmar) são
a Camada 2, virão depois, cada uma com confirmação SIM/NÃO pras perigosas.

Reusa a infra do projeto: a classe Ferramenta/Agente (core/agent.py) e o Brain.
Monta um agente com a persona da cesta + as ferramentas read-only abaixo.
"""
from __future__ import annotations


PERSONA_CESTA = """Você é o assistente de cestas do Zaq, falando pelo WhatsApp com um
cliente que ASSINA uma cesta de hortifruti (frutas, legumes, verduras, temperos) de um
fornecedor. Você NÃO fala sobre finanças pessoais, cupom fiscal ou controle de gastos —
isso é outro produto. Seu assunto é a CESTA dele.

Tom: simpático, brasileiro, direto, como uma feira de bairro. Curto (é WhatsApp).
Use emoji com moderação (🧺🥬🥕).

O que você sabe fazer (Camada 1 — só informar):
- dizer quando chega a próxima cesta
- listar o que vem na cesta da semana
- explicar o preço (fixo) e quando é cobrado (4 dias antes da entrega)

Se o cliente pedir pra MUDAR algo (tirar um produto, pausar, cancelar, confirmar),
diga que por enquanto isso é feito pelo link da cesta na web (ele recebe o link no
aviso semanal), e que em breve dará pra fazer por aqui. NÃO invente que fez a mudança.

Se perguntarem algo que não é sobre a cesta, redirecione gentilmente pro assunto cesta.
Nunca invente dados — use só o que as ferramentas retornam."""


def construir_ferramentas_cesta(pool, cliente_id: int) -> list:
    """Cria as ferramentas read-only do agente da cesta, amarradas a este cliente.

    Recebe pool + cliente_id (de quem está falando) e devolve a lista de Ferramenta.
    Importa a classe Ferramenta do projeto (core.agent).
    """
    from core.agent import Ferramenta
    from finance import assinaturas as assin_mod

    def _proxima_entrega(args: dict) -> str:
        """Quando chega a próxima cesta do cliente."""
        with pool.connection() as c:
            row = c.execute(
                """select cs.data_entrega, cs.status, co.nome
                   from cesta_semana cs
                   join contas co on co.id = cs.fornecedor_id
                   where cs.cliente_id = %s
                     and cs.status in ('sugerida','em_ajuste','confirmada','cobrada')
                   order by cs.data_entrega asc limit 1""",
                (cliente_id,),
            ).fetchone()
        if row is None:
            return "Ainda não há uma cesta agendada. Assim que sua próxima cesta for montada, te aviso por aqui!"
        data, status, forn = row
        mapa = {"sugerida": "está sendo preparada", "em_ajuste": "está aberta pra ajuste",
                "confirmada": "está confirmada", "cobrada": "está confirmada e paga"}
        return f"Sua próxima cesta de {forn} chega em {data} e {mapa.get(status, status)}. 🧺"

    def _itens_da_cesta(args: dict) -> str:
        """O que vem na cesta da semana."""
        with pool.connection() as c:
            cab = c.execute(
                """select id from cesta_semana
                   where cliente_id = %s
                     and status in ('sugerida','em_ajuste','confirmada','cobrada')
                   order by data_entrega asc limit 1""",
                (cliente_id,),
            ).fetchone()
            if cab is None:
                return "Ainda não tem uma cesta montada pra essa semana. Te aviso quando estiver pronta!"
            itens = c.execute(
                """select p.nome, ci.grupo, ci.quantidade, p.unidade
                   from cesta_itens ci join catalogo_produtos p on p.id = ci.produto_id
                   where ci.cesta_id = %s order by ci.grupo""",
                (cab[0],),
            ).fetchall()
        if not itens:
            return "Sua cesta dessa semana ainda está sendo montada."
        linhas = [f"- {r[0]} ({r[2]:g} {r[3]})" for r in itens]
        return "Essa semana sua cesta vem com:\n" + "\n".join(linhas) + "\n🧺"

    def _preco_e_cobranca(args: dict) -> str:
        """Quanto custa e quando é cobrado."""
        assinaturas = assin_mod.listar_assinaturas_cliente(pool, cliente_id)
        ativas = [a for a in assinaturas if a["status"] in ("ativa", "aguardando_pagamento")]
        if not ativas:
            return "Você não tem uma assinatura ativa no momento."
        a = ativas[0]
        preco = a["preco_centavos"] / 100
        return (
            f"Sua cesta {a['tamanho_nome']} de {a['fornecedor_nome']} custa "
            f"R$ {preco:.2f} (preço fixo), {a['frequencia']}. "
            f"A cobrança é feita 4 dias antes de cada entrega. 👍"
        )

    return [
        Ferramenta(
            nome="proxima_entrega",
            descricao="Diz quando chega a próxima cesta do cliente e o status dela.",
            parametros={"type": "object", "properties": {}},
            executar=_proxima_entrega,
        ),
        Ferramenta(
            nome="itens_da_cesta",
            descricao="Lista os produtos que vêm na cesta da semana do cliente.",
            parametros={"type": "object", "properties": {}},
            executar=_itens_da_cesta,
        ),
        Ferramenta(
            nome="preco_e_cobranca",
            descricao="Informa o preço fixo da cesta e quando é cobrado.",
            parametros={"type": "object", "properties": {}},
            executar=_preco_e_cobranca,
        ),
    ]


def criar_agente_cesta(pool, cliente_id: int, brain, memoria=None):
    """Monta o agente da cesta (Camada 1) pra um cliente. Reusa core.agent.Agente.

    brain: o Brain do projeto (mesma infra do agente financeiro).
    """
    from core.agent import Agente
    ferramentas = construir_ferramentas_cesta(pool, cliente_id)
    return Agente(
        nome="cesta",
        persona=PERSONA_CESTA,
        ferramentas=ferramentas,
        brain=brain,
        memoria=memoria,
    )
