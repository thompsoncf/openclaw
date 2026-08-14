"""Módulo Vendas de Serviços — a ponte pipeline → financeiro.

Motor GENÉRICO de venda consultiva de serviço (o nicho 'tecnologia'/Aladdin é o
primeiro caso; o mesmo fluxo serve advocacia, agência, etc., trocando só o
catálogo). Aqui mora a ação que fecha o negócio:

    fechar_orcamento() — o orçamento vira contrato e cai no financeiro que já
    existe (módulo Empresa), como TÍTULOS A RECEBER:
      • setup  (valor único)      -> título a receber não-recorrente
      • mensal (recorrente)       -> título a receber RECORRENTE, que na baixa
                                     se auto-renova (dar_baixa_titulo do Empresa).

    No modo EVENTO (nicho eventos, migração 147) não existe mensalidade: cada
    parcela do plano de pagamento vira um título no vencimento combinado —
    sinal no Pix hoje, 12x no cartão a partir do mês que vem.

    Assim a receita entra pelo caminho de sempre (livro-caixa, fonte única) e o
    relatório de vendas continua unificado — sem PDV novo pra serviço.

Atômico: o status vira num único UPDATE ... WHERE status<>'fechado' RETURNING e
os dois títulos entram na MESMA transação. Fecha a corrida do duplo-clique (não
gera títulos em dobro) e, se algo falhar no meio, faz rollback total.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta

from .empresa import _mes_seguinte

CAT_SERVICOS = "Serviços"

# SQL do título a receber (reusa a tabela titulos do módulo Empresa).
_SQL_TITULO = """insert into titulos
    (conta_id, tipo, descricao, contraparte, valor_centavos, vencimento,
     categoria, recorrente, criado_por)
  values (%s, 'receber', %s, %s, %s, %s, %s, %s, %s) returning id"""


def _venc(v, padrao: date) -> date:
    """Vencimento da parcela: aceita date, ISO ('2025-11-13') e 'dd/mm/aaaa'.
    O que não der pra ler cai no padrão — parcela sem data válida vira título
    com vencimento, nunca título perdido."""
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v or "").strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return padrao


def _parcelas(bruto) -> list[dict]:
    """As parcelas do jsonb, já filtradas: só o que tem valor > 0 vira título."""
    if isinstance(bruto, str):
        try:
            bruto = json.loads(bruto)
        except ValueError:
            bruto = []
    itens = []
    for p in (bruto or []):
        if not isinstance(p, dict):
            continue
        valor = int(p.get("valor_centavos") or 0)
        if valor > 0:
            itens.append({"valor_centavos": valor, "venc": p.get("venc"),
                          "forma": (p.get("forma") or "").strip(),
                          "obs": (p.get("obs") or "").strip()})
    return itens


def fechar_orcamento(pool, conta_id: int, orcamento_id: int,
                     criado_por: int | None = None,
                     dias_setup: int = 7) -> dict:
    """Fecha o orçamento e gera os títulos a receber.

    • modo 'recorrente' (o de sempre): setup (único) + mensalidade (recorrente).
    • modo 'evento': UM título por parcela do plano de pagamento, no vencimento
      combinado — evento não tem mensalidade, tem sinal + parcelas. Orçamento de
      evento sem plano de pagamento cai no título único do valor total.

    conta_id é a conta que EMITE (a empresa de serviço, ex.: Aladdin) — os títulos
    são dela. Idempotente: orçamento já fechado não gera de novo.
    """
    hoje = date.today()
    with pool.connection() as c:
        # trava atômica + escopo por conta: só o dono fecha, e só o primeiro a
        # fechar segue; os demais (duplo-clique) voltam vazios.
        orc = c.execute(
            """update orcamentos set status='fechado', atualizado_em=now()
                where id=%s and conta_id=%s and status <> 'fechado'
             returning empresa, cliente, setup_centavos, mensal_centavos,
                       coalesce(modo,'recorrente'), parcelas""",
            (orcamento_id, conta_id),
        ).fetchone()
        if not orc:
            estado = c.execute(
                "select status from orcamentos where id=%s and conta_id=%s",
                (orcamento_id, conta_id),
            ).fetchone()
            if not estado:
                return {"ok": False, "erro": "Orçamento não encontrado."}
            return {"ok": False, "erro": f"Orçamento já está '{estado[0]}'."}

        empresa, cliente, setup_cent, mensal_cent, modo, parcelas_raw = orc
        contraparte = (empresa or cliente or "").strip()
        setup_cent = int(setup_cent or 0)
        mensal_cent = int(mensal_cent or 0)

        if modo == "evento":
            parcelas = _parcelas(parcelas_raw)
            base = f"Evento — {contraparte}".strip(" —")
            ids = []
            for i, p in enumerate(parcelas, 1):
                # a observação da parcela ("Sinal", "12x no cartão") é o que a
                # empresa escreveu pro cliente — vale mais na conciliação do que
                # um "parcela 2/13" genérico, então ela manda quando existe.
                rotulo = p["obs"] or f"parcela {i}/{len(parcelas)}"
                ids.append(c.execute(
                    _SQL_TITULO,
                    (conta_id, f"{base} · {rotulo}"[:200], contraparte,
                     p["valor_centavos"], _venc(p["venc"], hoje + timedelta(days=dias_setup)),
                     CAT_SERVICOS, False, criado_por),
                ).fetchone()[0])
            if not ids and setup_cent > 0:
                # sem plano de pagamento: um título só, com o total do evento.
                ids.append(c.execute(
                    _SQL_TITULO,
                    (conta_id, base, contraparte, setup_cent,
                     hoje + timedelta(days=dias_setup), CAT_SERVICOS, False, criado_por),
                ).fetchone()[0])
            c.commit()
            return {"ok": True, "modo": "evento", "titulos": ids,
                    "setup_titulo_id": None, "mensal_titulo_id": None}

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

    return {"ok": True, "modo": "recorrente", "setup_titulo_id": setup_id,
            "mensal_titulo_id": mensal_id}
