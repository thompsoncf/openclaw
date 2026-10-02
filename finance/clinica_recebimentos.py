"""O PAGAMENTO DO ATENDIMENTO na agenda da clínica (entrega 2a do CRM).

Desenho aprovado: docs/mockups/clinica_crm_telas.html, seção 04 ("Veio, faltou, pagou"),
01/10/2026. Decisão B do dono em 02/10/2026: a consulta se recebe A QUALQUER HORA DO DIA
(do Chegou até depois do Finalizar) e não há sinal por Pix por enquanto. Tabela: 495.

COMO ANDA
  1. Cada linha da agenda do dia ganha um selo (`anotar`): pago, fica a receber, a
     receber, pacote (a sessão do pacote ou a inclusa da assinatura) ou sem custo
     (retorno sem custo, cortesia). Antes de o paciente chegar, nada.
  2. "Receber" (`receber`) lança a receita no Financeiro pelo mesmo caminho do balcão
     (LivroCaixa, origem 'balcao', categoria Vendas) e liga ao cliente. "Fica a receber"
     vira um título a receber do cliente, com vencimento em 30 dias.
  3. Um recebimento por atendimento: o `unique (evento_id)` é a trava do clique duplo.

A RECEITA NÃO DIZ O PROCEDIMENTO: o Financeiro lê "Atendimento · Maria (consulta)", a
categoria do catálogo, nunca o nome do procedimento.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from finance import clinica_agenda as ca

_log = logging.getLogger("clinica.recebimentos")

#: do Chegou em diante: é quando a recepção recebe (decisão B, "a qualquer hora do dia")
SITUACOES = ("presente", "atendimento", "finalizado")
#: as mesmas formas do balcão (clinica_produtos.PAGAMENTOS)
FORMAS = {"pix": "Pix", "credito": "cartão de crédito", "debito": "cartão de débito", "especie": "dinheiro",
          "fiado": "fica a receber"}
FICA_DIAS = 30

#: o que o selo diz
SELO = {"pago": "pago", "fica": "fica a receber", "a_receber": "a receber", "pacote": "pacote",
        "sem_custo": "sem custo"}


def _ids(c, sql: str, args) -> set[int]:
    try:
        with c.transaction():
            return {r[0] for r in c.execute(sql, args).fetchall()}
    except Exception:  # noqa: BLE001 — tabela de uma migração que este banco não tem
        return set()


def anotar(c, conta_id: int, evs: list[dict]) -> None:
    """Põe em cada agendamento `pgto` (a chave de SELO, ou None antes de o paciente
    chegar) e, se recebido, `pgto_valor` e `pgto_forma`."""
    ids = [e["id"] for e in evs]
    if not ids:
        return
    rec = {}
    try:
        with c.transaction():
            rec = {r[0]: (r[1], r[2]) for r in c.execute(
                """select evento_id, forma, valor_centavos from clinica_recebimentos
                    where conta_id=%s and evento_id = any(%s)""", (conta_id, ids)).fetchall()}
    except Exception:  # noqa: BLE001 — sem a 495
        rec = {}
    coberto = _ids(c, "select evento_id from clinica_pacote_consumos where conta_id=%s and evento_id = any(%s)",
                   (conta_id, ids))
    coberto |= _ids(c, "select evento_id from clinica_assinatura_usos where conta_id=%s and evento_id = any(%s)",
                    (conta_id, ids))
    for e in evs:
        r = rec.get(e["id"])
        if r:
            e["pgto"], e["pgto_forma"], e["pgto_valor"] = ("fica" if r[0] == "fiado" else "pago"), r[0], r[1]
        elif e["situacao"] not in SITUACOES:
            e["pgto"] = None
        elif e["id"] in coberto or e.get("categoria") == "sessao":
            e["pgto"] = "pacote"
        elif not e.get("preco"):
            e["pgto"] = "sem_custo"
        else:
            e["pgto"] = "a_receber"


def receber(pool, conta_id: int, evento_id: int, *, valor_centavos: int | None, forma: str,
            membro_id: int | None, agora: datetime | None = None) -> str | None:
    """Recebe o atendimento. Devolve None, ou a mensagem de erro pra tela."""
    agora = agora or datetime.now(timezone.utc)
    if forma not in FORMAS:
        return "Escolha a forma de pagamento."
    if not valor_centavos or valor_centavos <= 0:
        return "Valor inválido. Use o formato 150,00."
    with pool.connection() as c:
        ev = ca.evento(c, conta_id, evento_id)
        if not ev:
            return "Agendamento não encontrado."
        if ev["situacao"] not in SITUACOES:
            return "O pagamento é recebido do Chegou em diante: marque o paciente como presente."
        try:
            with c.transaction():
                r = c.execute("""insert into clinica_recebimentos (conta_id, evento_id, prospeccao_id, valor_centavos,
                                                                   forma, criado_por)
                                 values (%s,%s,%s,%s,%s,%s) on conflict (evento_id) do nothing returning id""",
                              (conta_id, evento_id, ev["lead"], int(valor_centavos), forma, membro_id)).fetchone()
        except Exception:  # noqa: BLE001 — sem a 495
            _log.warning("recebimentos: tabela indisponível (conta %s)", conta_id, exc_info=True)
            return "O recebimento ainda não está disponível. Tente de novo em alguns minutos."
        if not r:
            return "Esse atendimento já foi recebido."
        c.commit()
        rid = r[0]
    try:
        lanc_id, titulo_id = _lancar(pool, conta_id, ev, int(valor_centavos), forma, membro_id, agora)
    except Exception:  # noqa: BLE001 — o dinheiro não entrou no Financeiro: o recebimento não vale
        _log.warning("recebimentos: lançamento falhou (evento %s)", evento_id, exc_info=True)
        with pool.connection() as c:
            c.execute("delete from clinica_recebimentos where id=%s and conta_id=%s", (rid, conta_id))
            c.commit()
        return "Não deu pra lançar no Financeiro. Nada foi registrado: tente de novo."
    with pool.connection() as c:
        c.execute("update clinica_recebimentos set lancamento_id=%s, titulo_id=%s where id=%s and conta_id=%s",
                  (lanc_id, titulo_id, rid, conta_id))
        c.commit()
    return None


def _descricao(ev: dict) -> str:
    primeiro = (ev.get("paciente") or "Paciente").split(" ")[0]
    return f"Atendimento · {primeiro} ({ev.get('categoria') or 'consulta'})"


def _lancar(pool, conta_id: int, ev: dict, valor: int, forma: str, membro_id: int | None,
            agora: datetime) -> tuple[int | None, int | None]:
    """A receita no livro-caixa (o mesmo caminho do balcão) ou, em "fica a receber", o
    título a receber do cliente. Devolve (lancamento_id, titulo_id)."""
    from finance import clinica_pacientes as _cpa
    cliente_id = _cpa.ficha_do_paciente(pool, conta_id, ev.get("lead"), ev.get("paciente") or "", ev.get("fone") or "")
    if forma == "fiado":
        from finance import empresa as _emp
        t = _emp.criar_titulo(pool, conta_id, "receber", _descricao(ev) + " — fica a receber", valor,
                              ca.hoje_br(agora) + timedelta(days=FICA_DIAS), contraparte=ev.get("paciente") or "",
                              categoria="Vendas", criado_por=membro_id, cliente_id=cliente_id)
        return None, t["id"]
    from finance.livro_caixa import LivroCaixa
    from finance.models import Lancamento, Tipo
    lc = LivroCaixa(pool, conta_id, membro_id=membro_id)
    lanc = lc.adicionar(Lancamento(tipo=Tipo.RECEITA, valor_centavos=valor, categoria="Vendas",
                                   descricao=_descricao(ev), pagamento=forma, origem="balcao", natureza="empresa"),
                        forcar=True)
    if cliente_id:
        with pool.connection() as c:
            c.execute("update lancamentos set cliente_id=%s where id=%s and conta_id=%s", (cliente_id, lanc.id, conta_id))
            c.commit()
    return lanc.id, None


def do_evento(c, conta_id: int, evento_id: int) -> dict | None:
    """O recebimento deste atendimento, pra tela do agendamento."""
    try:
        with c.transaction():
            r = c.execute("""select forma, valor_centavos, criado_em from clinica_recebimentos
                              where conta_id=%s and evento_id=%s""", (conta_id, evento_id)).fetchone()
    except Exception:  # noqa: BLE001 — sem a 495
        return None
    return {"forma": r[0], "forma_d": FORMAS.get(r[0], r[0]), "valor": r[1], "em": r[2]} if r else None
