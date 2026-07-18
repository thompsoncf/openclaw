"""Módulo Vendas de Serviços — a ponte pipeline → financeiro.

Motor GENÉRICO de venda consultiva de serviço (o nicho 'tecnologia'/Aladdin é o
primeiro caso; o mesmo fluxo serve advocacia, agência, etc., trocando só o
catálogo). Aqui mora a ação que fecha o negócio:

    fechar_orcamento() — o orçamento vira contrato e cai no financeiro que já
    existe (módulo Empresa), como TÍTULOS A RECEBER:
      • setup  (valor único)      -> título a receber não-recorrente
      • mensal (recorrente)       -> título a receber RECORRENTE, que na baixa
                                     se auto-renova (dar_baixa_titulo do Empresa).

    Assim a receita entra pelo caminho de sempre (livro-caixa, fonte única) e o
    relatório de vendas continua unificado — sem PDV novo pra serviço.

Atômico: o status vira num único UPDATE ... WHERE status<>'fechado' RETURNING e
os dois títulos entram na MESMA transação. Fecha a corrida do duplo-clique (não
gera títulos em dobro) e, se algo falhar no meio, faz rollback total.
"""
from __future__ import annotations

from datetime import date, timedelta

from .empresa import _mes_seguinte

CAT_SERVICOS = "Serviços"

# SQL do título a receber (reusa a tabela titulos do módulo Empresa).
_SQL_TITULO = """insert into titulos
    (conta_id, tipo, descricao, contraparte, valor_centavos, vencimento,
     categoria, recorrente, criado_por)
  values (%s, 'receber', %s, %s, %s, %s, %s, %s, %s) returning id"""


def fechar_orcamento(pool, conta_id: int, orcamento_id: int,
                     criado_por: int | None = None,
                     dias_setup: int = 7) -> dict:
    """Fecha o orçamento e gera os títulos a receber (setup + mensal recorrente).

    conta_id é a conta que EMITE (a empresa de serviço, ex.: Aladdin) — os títulos
    são dela. Idempotente: orçamento já fechado não gera de novo.
    """
    hoje = date.today()
    with pool.connection() as c:
        # trava atômica: só o primeiro a fechar segue; os demais voltam vazios
        orc = c.execute(
            """update orcamentos set status='fechado', atualizado_em=now()
                where id=%s and status <> 'fechado'
             returning empresa, cliente, setup_centavos, mensal_centavos""",
            (orcamento_id,),
        ).fetchone()
        if not orc:
            estado = c.execute(
                "select status from orcamentos where id=%s", (orcamento_id,)
            ).fetchone()
            if not estado:
                return {"ok": False, "erro": "Orçamento não encontrado."}
            return {"ok": False, "erro": f"Orçamento já está '{estado[0]}'."}

        empresa, cliente, setup_cent, mensal_cent = orc
        contraparte = (empresa or cliente or "").strip()
        setup_cent = int(setup_cent or 0)
        mensal_cent = int(mensal_cent or 0)

        setup_id = mensal_id = None
        if setup_cent > 0:
            setup_id = c.execute(
                _SQL_TITULO,
                (conta_id, f"Setup — {contraparte}".strip(" —"), contraparte,
                 setup_cent, hoje + timedelta(days=dias_setup), CAT_SERVICOS,
                 False, criado_por),
            ).fetchone()[0]
        if mensal_cent > 0:
            mensal_id = c.execute(
                _SQL_TITULO,
                (conta_id, f"Mensalidade — {contraparte}".strip(" —"), contraparte,
                 mensal_cent, _mes_seguinte(hoje), CAT_SERVICOS, True, criado_por),
            ).fetchone()[0]
        c.commit()

    return {"ok": True, "setup_titulo_id": setup_id,
            "mensal_titulo_id": mensal_id}
