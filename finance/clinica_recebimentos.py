"""O PAGAMENTO DO ATENDIMENTO na agenda da clínica (entrega 2a do CRM).

Desenho aprovado: docs/mockups/clinica_crm_telas.html, seção 04 ("Veio, faltou, pagou"),
01/10/2026. Decisão B do dono em 02/10/2026: a consulta se recebe A QUALQUER HORA DO DIA
(do Chegou até depois do Finalizar) e não há sinal por Pix por enquanto. Tabela: 495.

COMO ANDA
  1. Cada linha da agenda do dia ganha um selo (`anotar`): pago, fica a receber, a
     receber, pacote (a sessão do pacote ou a inclusa da assinatura) ou sem custo.
     Antes de o paciente chegar, nada.
  2. "Receber" (`receber`) lança a receita no Financeiro pelo mesmo caminho do balcão
     (LivroCaixa, origem 'balcao', categoria Vendas), ligada à ficha do paciente, NA
     MESMA TRANSAÇÃO do recebimento: entra tudo ou nada. "Fica a receber" vira um título
     a receber do cliente, com vencimento em 30 dias.
  3. Um recebimento por atendimento: o `unique (evento_id)` é a trava do clique duplo.

O QUE NÃO SE RECEBE AQUI (`cobertura`, a mesma leitura da tela do agendamento): a sessão
que o pacote do paciente cobre e a sessão inclusa da assinatura (antes do Finalizar, pelo
que vai ser baixado; depois, pelo que foi). E o retorno sem preço no catálogo (Retorno,
Cortesia, Retirada de teste): "sem custo".

PREÇO ZERO NÃO É GRÁTIS. No catálogo, preço vazio é "sob consulta" (clinica_config.centavos):
em 02/10/2026, 13 dos 14 atendimentos da conta 39 estavam assim, inclusive vacina e
procedimento. Só o retorno sem preço é "sem custo"; o resto fica "a receber", com o
valor em branco pra recepção digitar.

A RECEITA NÃO DIZ O PROCEDIMENTO: o Financeiro lê "Atendimento · Maria (consulta)", a
categoria do catálogo, nunca o nome do procedimento.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from finance import clinica_agenda as ca
from finance import clinica_config as cc

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


def cobertura(c, conta_id: int, ev: dict) -> str | None:
    """O pacote ou a assinatura que cobre este atendimento: o nome, ou None. A mesma
    leitura da tela do agendamento: finalizado, o que foi baixado; antes, o que vai ser
    (e a sessão inclusa da assinatura vem antes do pacote, como no Finalizar)."""
    from finance import clinica_assinaturas as cas
    from finance import clinica_pacotes as ckp
    try:
        if ev["situacao"] == "finalizado":
            nome = cas.do_evento(c, conta_id, ev["id"])
            if nome:
                return nome
            k = ckp.do_evento(c, conta_id, ev["id"])
            return k["nome"] if k else None
        a = cas.cobre(c, conta_id, ev)
        if a:
            return a["plano"]
        k = ckp.para_o_evento(c, conta_id, ev)
        return k["nome"] if k else None
    except Exception:  # noqa: BLE001 — sem as tabelas de pacote ou de assinatura
        return None


def _recebidos(c, conta_id: int, ids: list[int]) -> dict:
    if not ids:
        return {}
    try:
        with c.transaction():
            return {r[0]: (r[1], r[2]) for r in c.execute(
                """select evento_id, forma, valor_centavos from clinica_recebimentos
                    where conta_id=%s and evento_id = any(%s)""", (conta_id, ids)).fetchall()}
    except Exception:  # noqa: BLE001 — sem a 495
        return {}


def _situacao(c, conta_id: int, e: dict, recebido) -> str | None:
    if recebido:
        return "fica" if recebido[0] == "fiado" else "pago"
    if e["situacao"] not in SITUACOES:
        return None
    if cobertura(c, conta_id, e):
        return "pacote"
    if not e.get("preco") and e.get("categoria") == "retorno":
        return "sem_custo"
    return "a_receber"


def anotar(c, conta_id: int, evs: list[dict]) -> None:
    """Põe em cada agendamento `pgto` (a chave de SELO, ou None antes de o paciente
    chegar) e, se recebido, `pgto_valor` e `pgto_forma`."""
    rec = _recebidos(c, conta_id, [e["id"] for e in evs])
    for e in evs:
        r = rec.get(e["id"])
        e["pgto"] = _situacao(c, conta_id, e, r)
        if r:
            e["pgto_forma"], e["pgto_valor"] = r


def receber(pool, conta_id: int, evento_id: int, *, valor_centavos: int | None, forma: str,
            membro_id: int | None, agora: datetime | None = None) -> str | None:
    """Recebe o atendimento. Devolve None, ou a mensagem de erro pra tela."""
    agora = agora or datetime.now(timezone.utc)
    if forma not in FORMAS:
        return "Escolha a forma de pagamento."
    if not valor_centavos or valor_centavos <= 0:
        return "Digite o valor recebido (formato 150,00)."
    with pool.connection() as c:
        ev = ca.evento(c, conta_id, evento_id)
        if not ev:
            return "Agendamento não encontrado."
        situacao = _situacao(c, conta_id, ev, _recebidos(c, conta_id, [evento_id]).get(evento_id))
        if situacao in ("pago", "fica"):
            return "Esse atendimento já foi recebido."
        if situacao == "pacote":
            return "Esse atendimento é coberto pelo pacote ou pela assinatura do paciente: nada a receber."
        if situacao == "sem_custo":
            return "Esse retorno é sem custo."
        if situacao != "a_receber":
            return "O pagamento é recebido do Chegou em diante: marque o paciente como presente."
        # a ficha do paciente: a que a recepção ligou ao agendamento; sem ela, a do card
        from finance import clinica_ficha_link as cfl
        cliente_id = cfl.cliente_do_evento(c, conta_id, evento_id)
    if not cliente_id:
        from finance import clinica_pacientes as _cpa
        cliente_id = _cpa.ficha_do_paciente(pool, conta_id, ev.get("lead"), ev.get("paciente") or "",
                                            ev.get("fone") or "")
    if forma == "fiado":
        return _fica_a_receber(pool, conta_id, ev, int(valor_centavos), membro_id, cliente_id, agora)
    with pool.connection() as c:
        try:
            r = c.execute("""insert into clinica_recebimentos (conta_id, evento_id, prospeccao_id, valor_centavos,
                                                               forma, criado_por)
                             values (%s,%s,%s,%s,%s,%s) on conflict (evento_id) do nothing returning id""",
                          (conta_id, evento_id, ev["lead"], int(valor_centavos), forma, membro_id)).fetchone()
            if not r:
                c.rollback()
                return "Esse atendimento já foi recebido."
            # TUDO OU NADA: a receita, a ficha e o vínculo entram no mesmo commit do
            # recebimento. Em dois commits, a falha no meio lançava a receita duas
            # vezes (o "tente de novo") ou deixava "pago" sem receita (revisão de 02/10)
            lanc_id = _lancar_receita(pool, c, conta_id, ev, int(valor_centavos), forma, membro_id, cliente_id)
            c.execute("update clinica_recebimentos set lancamento_id=%s where id=%s and conta_id=%s",
                      (lanc_id, r[0], conta_id))
            c.commit()
        except Exception:  # noqa: BLE001
            c.rollback()
            _log.warning("recebimentos: não registrado (evento %s)", evento_id, exc_info=True)
            return "Não deu pra lançar no Financeiro. Nada foi registrado: tente de novo."
    return None


def _categoria(ev: dict) -> str:
    """A categoria que vai pro Financeiro: só as do catálogo (a linha antiga pode ter
    texto livre na categoria)."""
    cat = ev.get("categoria") or ""
    return dict(cc.CATEGORIAS).get(cat, "atendimento").lower()


def _descricao(ev: dict) -> str:
    primeiro = (ev.get("paciente") or "Paciente").split(" ")[0]
    return f"Atendimento · {primeiro} ({_categoria(ev)})"


def _lancar_receita(pool, c, conta_id: int, ev: dict, valor: int, forma: str, membro_id: int | None,
                    cliente_id: int | None) -> int:
    """A receita no livro-caixa, o mesmo caminho do balcão, DENTRO da transação `c`."""
    from finance.livro_caixa import LivroCaixa
    from finance.models import Lancamento, Tipo
    lanc = LivroCaixa(pool, conta_id, membro_id=membro_id).adicionar(
        Lancamento(tipo=Tipo.RECEITA, valor_centavos=valor, categoria="Vendas", descricao=_descricao(ev),
                   pagamento=forma, origem="balcao", natureza="empresa"),
        forcar=True, conn=c)
    if cliente_id:
        c.execute("update lancamentos set cliente_id=%s where id=%s and conta_id=%s", (cliente_id, lanc.id, conta_id))
    return lanc.id


def _fica_a_receber(pool, conta_id: int, ev: dict, valor: int, membro_id: int | None, cliente_id: int | None,
                    agora: datetime) -> str | None:
    """"Fica a receber": o título a receber do cliente. `criar_titulo` faz o próprio
    commit; o recebimento nasce antes (a trava do clique duplo) e sai se o título não
    nascer."""
    with pool.connection() as c:
        r = c.execute("""insert into clinica_recebimentos (conta_id, evento_id, prospeccao_id, valor_centavos,
                                                           forma, criado_por)
                         values (%s,%s,%s,%s,'fiado',%s) on conflict (evento_id) do nothing returning id""",
                      (conta_id, ev["id"], ev["lead"], valor, membro_id)).fetchone()
        c.commit()
    if not r:
        return "Esse atendimento já foi recebido."
    from finance import empresa as _emp
    try:
        t = _emp.criar_titulo(pool, conta_id, "receber", _descricao(ev) + " — fica a receber", valor,
                              ca.hoje_br(agora) + timedelta(days=FICA_DIAS), contraparte=ev.get("paciente") or "",
                              categoria="Vendas", criado_por=membro_id, cliente_id=cliente_id)
    except Exception:  # noqa: BLE001 — o título não nasceu: o recebimento não vale
        _log.warning("recebimentos: título não criado (evento %s)", ev["id"], exc_info=True)
        with pool.connection() as c:
            c.execute("delete from clinica_recebimentos where id=%s and conta_id=%s", (r[0], conta_id))
            c.commit()
        return "Não deu pra criar o título a receber. Nada foi registrado: tente de novo."
    try:
        with pool.connection() as c:
            c.execute("update clinica_recebimentos set titulo_id=%s where id=%s and conta_id=%s",
                      (t["id"], r[0], conta_id))
            c.commit()
    except Exception:  # noqa: BLE001 — o título existe; só o vínculo se perdeu
        _log.warning("recebimentos: título %s sem vínculo (evento %s)", t["id"], ev["id"], exc_info=True)
    return None


def do_evento(c, conta_id: int, evento_id: int) -> dict | None:
    """O recebimento deste atendimento, pra tela do agendamento."""
    try:
        with c.transaction():
            r = c.execute("""select forma, valor_centavos, criado_em from clinica_recebimentos
                              where conta_id=%s and evento_id=%s""", (conta_id, evento_id)).fetchone()
    except Exception:  # noqa: BLE001 — sem a 495
        return None
    return {"forma": r[0], "forma_d": FORMAS.get(r[0], r[0]), "valor": r[1], "em": r[2]} if r else None
