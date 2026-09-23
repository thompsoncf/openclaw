"""Quando o dinheiro que entrou não fecha com a parcela — pedido 9 do dono.

"Ajuste da parcela do valor do sinal [quando] for outro valor que não o mesmo que
foi gerado — se pode automatizar ou manual" (23/09/2026). A resposta dele, no
mesmo dia, depois do mockup:

    "ele continua devendo quando o valor não fechar, e quando passar fica de
     crédito e lança pra próxima parcela" — "sempre pergunta pro gestor".

Então são DOIS caminhos, e nenhum é automático — a tela pergunta, o gestor
escolhe, e só aí algo muda:

* ENTROU A MENOS -> `restante`: a parcela passa a valer o que entrou e nasce uma
  conta NOVA com o que falta, no MESMO vencimento (a cobrança continua atrasada
  se estava). Ou `desconto`, quando a diferença foi combinada — o caminho que a
  baixa já tinha (acréscimo negativo, migração 197).

* ENTROU A MAIS -> `abater`: a parcela passa a valer o que entrou e o que passou
  ABATE da parcela que o gestor escolher; se passar dela, o resto segue pras
  seguintes, por vencimento. Ou `juros`, quando o a mais foi multa e juros do
  atraso — de novo o caminho que já existia.

TUDO NUMA TRANSAÇÃO SÓ. As parcelas mudam e a baixa acontece juntas
(`dar_baixa_titulo(conn=...)`): o crédito só abate se o dinheiro de fato entrou
no caixa, e o restante só nasce se a parcela de fato foi baixada.

NADA SE PERDE (regra 0): cada conta mexida ganha uma linha em `titulo_ajustes`
(migração 323) com o valor de antes e o de depois. Parcela que o crédito cobre
inteira não é apagada: vira `cancelado`, com o valor combinado intacto e o
motivo na descrição.

O caso que motivou: orçamento nº 23 da Prime — sinal combinado de R$ 2.340,00,
conta editada à mão pra R$ 2.415,00 e baixada; R$ 75,00 a mais que não abateram
nada. `creditos_pendentes` acha esses casos já baixados e oferece o mesmo abater.
"""
from __future__ import annotations

from datetime import date, datetime, time

#: As escolhas que a tela oferece, por lado da diferença.
MENOS = ("restante", "desconto")
MAIS = ("abater", "juros")


def _titulo(c, conta_id: int, titulo_id: int):
    return c.execute(
        """select id, tipo, status, valor_centavos, orcamento_id, cliente_id,
                  lower(btrim(contraparte)), parcela_idx, descricao, recorrente
             from titulos where id=%s and conta_id=%s""",
        (titulo_id, conta_id)).fetchone()


def _alvos(c, conta_id: int, t) -> list[dict]:
    """Onde o crédito pode abater: as contas a RECEBER em aberto do mesmo
    negócio. Mesmo orçamento quando há; sem orçamento, o mesmo cliente do
    cadastro; sem cadastro, o mesmo nome. Nunca de outro cliente — crédito de uma
    pessoa abatendo a dívida de outra é o erro que não se desfaz calado."""
    tid, orc, cli, nome = t[0], t[4], t[5], t[6]
    if orc:
        filtro, arg = "orcamento_id = %s", orc
    elif cli:
        filtro, arg = "cliente_id = %s", cli
    elif nome:
        filtro, arg = "lower(btrim(contraparte)) = %s", nome
    else:
        return []
    rows = c.execute(
        f"""select id, descricao, valor_centavos, vencimento from titulos
             where conta_id=%s and tipo='receber' and status='aberto'
               and id <> %s and valor_centavos > 0 and {filtro}
             order by vencimento, id""",
        (conta_id, tid, arg)).fetchall()
    return [{"id": r[0], "descricao": r[1], "valor_centavos": int(r[2]),
             "vencimento": r[3]} for r in rows]


def alvos_por_titulo(pool, conta_id: int) -> dict[int, list[dict]]:
    """{titulo_id: alvos} de TODAS as contas a receber em aberto, numa consulta
    só — a tela precisa da lista em cada linha, e uma consulta por linha seria a
    aba Empresa fazendo 40 idas ao banco. O critério é o MESMO de `_alvos`."""
    try:
        with pool.connection() as c:
            rows = c.execute(
                """select id, descricao, valor_centavos, vencimento, orcamento_id,
                          cliente_id, lower(btrim(contraparte))
                     from titulos
                    where conta_id=%s and tipo='receber' and status='aberto'
                      and valor_centavos > 0
                    order by vencimento, id""", (conta_id,)).fetchall()
    except Exception:  # noqa: BLE001
        return {}
    out: dict[int, list[dict]] = {}
    for r in rows:
        tid, orc, cli, nome = r[0], r[4], r[5], r[6]
        if orc:
            casa = [x for x in rows if x[4] == orc]
        elif cli:
            casa = [x for x in rows if x[5] == cli]
        elif nome:
            casa = [x for x in rows if x[6] == nome]
        else:
            casa = []
        out[int(tid)] = [{"id": x[0], "descricao": x[1], "valor_centavos": int(x[2]),
                          "vencimento": x[3]} for x in casa if x[0] != tid]
    return out


def alvos_do_credito(pool, conta_id: int, titulo_id: int) -> list[dict]:
    with pool.connection() as c:
        t = _titulo(c, conta_id, titulo_id)
        return _alvos(c, conta_id, t) if t else []


def _ajuste(c, conta_id, titulo_id, origem_id, tipo, antes, depois, membro_id):
    c.execute(
        """insert into titulo_ajustes (conta_id, titulo_id, origem_titulo_id, tipo,
                                       valor_antes, valor_depois, criado_por)
           values (%s,%s,%s,%s,%s,%s,%s)""",
        (conta_id, titulo_id, origem_id, tipo, int(antes), int(depois), membro_id))


def _abater(c, conta_id: int, t, credito: int, alvo_id: int, membro_id) -> dict | None:
    """Distribui `credito` a partir da parcela escolhida. Devolve {erro} ou None."""
    alvos = _alvos(c, conta_id, t)
    escolhido = next((a for a in alvos if a["id"] == int(alvo_id or 0)), None)
    if not escolhido:
        return {"erro": "Escolha de qual parcela abater o crédito."}
    fila = [escolhido] + [a for a in alvos if a["id"] != escolhido["id"]]
    disponivel = sum(a["valor_centavos"] for a in fila)
    if credito > disponivel:
        from finance.contrato import reais
        return {"erro": f"O crédito de {reais(credito)} passa de tudo que ainda falta "
                        f"receber ({reais(disponivel)}). Se foi multa e juros, escolha "
                        "\"foi juros\"."}
    resta = credito
    for a in fila:
        if resta <= 0:
            break
        tira = min(resta, a["valor_centavos"])
        if tira == a["valor_centavos"]:
            # coberta inteira: valor > 0 é regra da tabela (196), e apagar seria
            # perder o combinado. Fica cancelada, com o motivo escrito nela.
            c.execute(
                """update titulos set status='cancelado',
                          descricao = descricao || ' — quitada com crédito'
                    where id=%s and conta_id=%s and status='aberto'""",
                (a["id"], conta_id))
            _ajuste(c, conta_id, a["id"], t[0], "quitada_credito",
                    a["valor_centavos"], 0, membro_id)
        else:
            c.execute("update titulos set valor_centavos = valor_centavos - %s "
                      "where id=%s and conta_id=%s and status='aberto'",
                      (tira, a["id"], conta_id))
            _ajuste(c, conta_id, a["id"], t[0], "abatimento",
                    a["valor_centavos"], a["valor_centavos"] - tira, membro_id)
        resta -= tira
    return None


def _avisar_orcamento_do_sinal(c, conta_id: int, titulo_id: int, pago_em: date) -> None:
    """A baixa do SINAL pela aba Empresa conta pro orçamento que o sinal caiu.

    Medido em 23/09/2026 na Prime: os sinais dos orçamentos nº 8, 20 e 23 foram
    baixados pela Empresa, e o orçamento seguiu achando que não — o funil e o
    Raio-X não os contam. Só PREENCHE o vazio (quem confirmou pelo "Sinal
    recebido" já tem a data dele) e só daqui pra frente; não firma agenda nem
    cria contrato, que são do "Fechar contrato" e do "Sinal recebido"."""
    from finance.vendas import indice_do_sinal
    r = c.execute(
        """select o.id, o.parcelas, t.parcela_idx from titulos t
             join orcamentos o on o.id = t.orcamento_id and o.conta_id = t.conta_id
            where t.id=%s and t.conta_id=%s and o.sinal_pago_em is null""",
        (titulo_id, conta_id)).fetchone()
    if not r or r[2] is None or indice_do_sinal(r[1]) != r[2]:
        return
    # meio-dia de Brasília: a data do Pix vira um instante que não muda de dia
    # em fuso nenhum que a tela use
    from zoneinfo import ZoneInfo
    quando = datetime.combine(pago_em, time(12, 0), tzinfo=ZoneInfo("America/Sao_Paulo"))
    c.execute("update orcamentos set sinal_pago_em=%s where id=%s and conta_id=%s "
              "and sinal_pago_em is null", (quando, r[0], conta_id))


def baixar(pool, conta_id: int, titulo_id: int, recebido_centavos: int | None,
           destino: str = "", alvo_id: int | None = None,
           data_pagto: date | None = None, membro_id: int | None = None) -> dict:
    """A baixa de uma conta a receber com o valor que DE FATO entrou.

    `recebido_centavos` None (ou igual à parcela) é a baixa de sempre. Diferente,
    exige `destino` do lado certo (MENOS ou MAIS) — sem ele, nada acontece e a
    tela pergunta de novo. Devolve o que `dar_baixa_titulo` devolve, mais
    `restante_id` quando nasceu a conta do que falta."""
    from finance import empresa as emp
    data_pagto = data_pagto or date.today()
    with pool.connection() as c:
        t = _titulo(c, conta_id, titulo_id)
        if not t:
            return {"ok": False, "erro": "Título não encontrado."}
        valor = int(t[3])
        dif = 0 if recebido_centavos is None else int(recebido_centavos) - valor
        if t[1] != "receber" or dif == 0:
            r = emp.dar_baixa_titulo(pool, conta_id, titulo_id, data_pagto=data_pagto,
                                     membro_id=membro_id, conn=c)
        else:
            if t[2] != "aberto":
                return {"ok": False, "erro": f"Título já está '{t[2]}'."}
            if int(recebido_centavos) <= 0:
                return {"ok": False, "erro": "Diga quanto entrou."}
            if (dif < 0 and destino not in MENOS) or (dif > 0 and destino not in MAIS):
                return {"ok": False, "erro": "Entrou um valor diferente da parcela: "
                                             "escolha o que fazer com a diferença."}
            if destino in ("desconto", "juros"):
                r = emp.dar_baixa_titulo(pool, conta_id, titulo_id, data_pagto=data_pagto,
                                         membro_id=membro_id, acrescimo_centavos=dif, conn=c)
            else:
                recebido = int(recebido_centavos)
                if destino == "abater":
                    erro = _abater(c, conta_id, t, dif, alvo_id, membro_id)
                    if erro:
                        c.rollback()
                        return {"ok": False, **erro}
                c.execute("update titulos set valor_centavos=%s "
                          "where id=%s and conta_id=%s and status='aberto'",
                          (recebido, titulo_id, conta_id))
                _ajuste(c, conta_id, titulo_id, titulo_id, "recebido", valor, recebido,
                        membro_id)
                restante_id = None
                if destino == "restante":
                    restante_id = c.execute(
                        """insert into titulos
                             (conta_id, tipo, descricao, contraparte, valor_centavos,
                              vencimento, categoria, recorrente, criado_por, cliente_id,
                              orcamento_id, aprovacao, plano_conta_id, centro_custo_id)
                           select conta_id, tipo, descricao || ' — restante', contraparte,
                                  %s, vencimento, categoria, false, criado_por, cliente_id,
                                  orcamento_id, aprovacao, plano_conta_id, centro_custo_id
                             from titulos where id=%s and conta_id=%s
                           returning id""",
                        (valor - recebido, titulo_id, conta_id)).fetchone()[0]
                    _ajuste(c, conta_id, restante_id, titulo_id, "restante", 0,
                            valor - recebido, membro_id)
                r = emp.dar_baixa_titulo(pool, conta_id, titulo_id, data_pagto=data_pagto,
                                         membro_id=membro_id, conn=c)
                if r.get("ok"):
                    r["restante_id"] = restante_id
                    # a mensalidade que repete nasce com o valor COMBINADO, não
                    # com o que entrou desta vez
                    if r.get("proximo_titulo_id"):
                        c.execute("update titulos set valor_centavos=%s where id=%s "
                                  "and conta_id=%s and valor_centavos=%s",
                                  (valor, r["proximo_titulo_id"], conta_id, recebido))
        if not r.get("ok"):
            c.rollback()
            return r
        if t[1] == "receber" and t[4]:
            # consequência, não o registro: num savepoint, pra que um orçamento
            # estranho nunca desfaça a baixa que já está feita
            try:
                with c.transaction():
                    _avisar_orcamento_do_sinal(c, conta_id, titulo_id, data_pagto)
            except Exception:  # noqa: BLE001
                import logging
                logging.getLogger("recebido_diferente").warning(
                    "baixa %s: não deu pra avisar o orçamento do sinal", titulo_id,
                    exc_info=True)
        c.commit()
    return r


# ─────────────────────────────── o crédito que JÁ entrou e não abateu nada

def creditos_pendentes(pool, conta_id: int) -> dict[int, dict]:
    """{titulo_id: {credito, combinado, alvos}} das parcelas de orçamento JÁ
    baixadas por MAIS do que o plano combinava, sem ajuste registrado.

    É o caso do orçamento nº 23: a conta foi editada pro que entrou antes da
    baixa, e a diferença ficou solta. Só o lado A MAIS — valor pago abaixo do
    plano pode ter sido desconto combinado, e o sistema não tem como saber.
    Tolerante: sem as tabelas, nada a oferecer."""
    try:
        with pool.connection() as c:
            rows = c.execute(
                """select t.id, t.valor_centavos, t.parcela_idx, o.parcelas
                     from titulos t
                     join orcamentos o on o.id = t.orcamento_id and o.conta_id = t.conta_id
                    where t.conta_id=%s and t.tipo='receber' and t.status='pago'
                      and t.parcela_idx is not null
                      and not exists (select 1 from titulo_ajustes a
                                       where a.origem_titulo_id = t.id)""",
                (conta_id,)).fetchall()
            out = {}
            from finance.vendas import _parcelas
            for tid, valor, idx, parcelas in rows:
                itens = _parcelas(parcelas)
                if not (0 <= idx < len(itens)):
                    continue
                combinado = int(itens[idx].get("valor_centavos") or 0)
                if combinado <= 0 or int(valor) <= combinado:
                    continue
                t = _titulo(c, conta_id, tid)
                alvos = _alvos(c, conta_id, t)
                if alvos:
                    out[int(tid)] = {"credito": int(valor) - combinado,
                                     "combinado": combinado, "alvos": alvos}
            return out
    except Exception:  # noqa: BLE001
        return {}


def abater_credito_pendente(pool, conta_id: int, titulo_id: int, alvo_id: int,
                            membro_id: int | None = None) -> dict:
    """O gestor escolheu onde abater o crédito de uma parcela já baixada. O valor
    vem de `creditos_pendentes` — nunca da tela —, e a conta paga não muda: o
    dinheiro já está no caixa com o valor certo."""
    p = creditos_pendentes(pool, conta_id).get(int(titulo_id))
    if not p:
        return {"ok": False, "erro": "Essa parcela não tem crédito a abater."}
    with pool.connection() as c:
        t = _titulo(c, conta_id, titulo_id)
        erro = _abater(c, conta_id, t, p["credito"], alvo_id, membro_id)
        if erro:
            c.rollback()
            return {"ok": False, **erro}
        c.commit()
    return {"ok": True, "credito": p["credito"]}


def ajustes_por_titulo(pool, conta_id: int) -> dict[int, dict]:
    """{titulo_id: {tipo, antes, depois}} — o último ajuste de cada conta, pra
    linha dizer por que ela vale o que vale. Tolerante como o resto."""
    try:
        with pool.connection() as c:
            rows = c.execute(
                """select distinct on (titulo_id) titulo_id, tipo, valor_antes, valor_depois
                     from titulo_ajustes where conta_id=%s and titulo_id is not null
                    order by titulo_id, id desc""", (conta_id,)).fetchall()
    except Exception:  # noqa: BLE001
        return {}
    return {int(r[0]): {"tipo": r[1], "antes": int(r[2]), "depois": int(r[3])}
            for r in rows}
