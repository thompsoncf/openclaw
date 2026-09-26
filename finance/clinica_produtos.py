"""Produto junto do atendimento (fase 7c): o dermocosmético que já está na prateleira.

Desenho aprovado: docs/mockups/clinica_planos_pacotes_assinatura.html, seção 06.
Migração 386. Tela: /painel/clinica/produtos (web/painel_clinica_produtos.py); a venda
também sai da tela do agendamento, no fim do atendimento.

REUSO: o produto e o estoque são os do Zaq (finance/catalogo.py) e a venda é a do
balcão (finance/pdv.py: baixa o estoque e lança a receita em Vendas, ou o título no
fiado). Aqui fica o que é da clínica:
  * a venda LIGADA ao paciente e ao atendimento, com o desconto do assinante (7b);
  * a DURAÇÃO do produto: na data prevista da reposição, o agente pergunta se acabou —
    SEM dizer o nome do produto (pode revelar o tratamento), no horário de atendimento,
    1 automática por paciente por dia (clinica_lembretes, a mesma trava de vaga, plano
    e pacote) e respeitando SAIR/PARAR. A resposta do paciente vai pra recepção
    (clinica_agente, motivo "produto"), que sabe qual é o produto;
  * o LOTE com validade: o que vence em até 60 dias vira sugestão de venda.

O saldo do lote é estimado: a entrada mais antiga sai primeiro (quem vende pega o que
vence antes). Toda entrada do estoque conta como camada — a da tela genérica também,
só que sem validade —, e o saldo do produto fica nas camadas mais novas.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation

from finance import clinica_agenda as ca
from finance import clinica_config as cc

_log = logging.getLogger("clinica.produtos")
_LOCK = 771168          # 771161..771167: agenda, marcar, conversa, vagas, planos, pacotes, assinaturas
VENCE_DIAS = 60         # lote que vence em até 60 dias vira sugestão de venda
RECOMPRA_ANTES = 15     # a tela mostra a reposição que chega nos próximos 15 dias
REPETIDA_SEG = 90       # a mesma venda (paciente, produto, quantidade) de novo em 90 s é clique duplo
RESPOSTA_HORAS = 72     # a resposta ao lembrete de reposição vai pra recepção por até 3 dias
PAGAMENTOS = {"pix": "Pix", "credito": "cartão de crédito", "debito": "cartão de débito", "especie": "dinheiro",
              "fiado": "fica a receber"}
SITUACOES_VENDA = ("presente", "atendimento", "finalizado")


def _falta_migracao(e: Exception) -> bool:
    s = str(e)
    return "does not exist" in s and ("clinica_produto" in s or "recompra" in s or "estoque_mov" in s
                                      or "catalogo_produtos" in s)


def _qtd(txt, lo=Decimal("0.001"), hi=Decimal("999")) -> Decimal | None:
    try:
        v = Decimal(str(txt or "").strip().replace(",", "."))
        if not v.is_finite():
            return None
        return v if lo <= v <= hi else None
    except (InvalidOperation, ValueError):
        return None


def _fmt_qtd(q) -> str:
    return f"{Decimal(str(q)).normalize():f}"


# ------------------------------------------------------------------ config

def config(c, conta_id: int) -> dict:
    try:
        with c.transaction():
            r = c.execute("select produto_recompra from clinica_agenda_config where conta_id=%s",
                          (conta_id,)).fetchone()
    except Exception:  # noqa: BLE001 — sem a 386
        return {"recompra": "off"}
    return {"recompra": r[0] if r else "ligado"}


def salvar_config(c, conta_id: int, recompra: str) -> str | None:
    if recompra not in ("ligado", "off"):
        return "Opção inválida."
    c.execute("""insert into clinica_agenda_config (conta_id, produto_recompra) values (%s,%s)
                 on conflict (conta_id) do update set produto_recompra=excluded.produto_recompra""",
              (conta_id, recompra))
    return None


# ------------------------------------------------------------------ produtos, lotes e validade

def _lotes_vivos(c, conta_id: int) -> dict[int, list[dict]]:
    """produto → os lotes COM validade que ainda têm saldo, do que vence antes pro que
    vence depois. Estimativa: o saldo do produto fica nas entradas mais novas (a mais
    antiga saiu primeiro), contando TODAS as entradas do estoque; a que não tem lote
    (tela genérica, compra) ocupa saldo mas não aparece."""
    try:
        with c.transaction():
            rows = c.execute(
                """select m.produto_id, m.quantidade, l.validade, p.saldo
                     from estoque_mov m
                     join catalogo_produtos p on p.id = m.produto_id and p.fornecedor_id = m.fornecedor_id
                     left join clinica_produto_lotes l on l.mov_id = m.id and l.conta_id = m.fornecedor_id
                    where m.fornecedor_id=%s and m.tipo='entrada' and p.ativo
                    order by m.produto_id, m.id desc""", (conta_id,)).fetchall()
    except Exception as e:  # noqa: BLE001
        if _falta_migracao(e):
            return {}
        raise
    out: dict[int, list[dict]] = {}
    resta: dict[int, Decimal] = {}
    for pid, qtd, validade, saldo in rows:
        r = resta.setdefault(pid, max(Decimal(str(saldo or 0)), Decimal(0)))
        if r <= 0:
            continue
        fica = min(Decimal(str(qtd)), r)
        resta[pid] = r - fica
        if validade:
            out.setdefault(pid, []).append({"validade": validade, "quantidade": fica})
    for v in out.values():
        v.sort(key=lambda x: x["validade"])
    return out


def produtos(c, conta_id: int, hoje: date) -> list[dict]:
    try:
        with c.transaction():
            rows = c.execute(
                """select id, nome, unidade, preco_venda_centavos, saldo, recompra_dias, coalesce(categoria, ''),
                          custo_medio_centavos
                     from catalogo_produtos where fornecedor_id=%s and ativo order by nome, id""",
                (conta_id,)).fetchall()
    except Exception as e:  # noqa: BLE001
        if "does not exist" in str(e):      # sem a 386 (ou sem o catálogo)
            return []
        raise
    lotes = _lotes_vivos(c, conta_id)
    out = []
    for r in rows:
        vivos = lotes.get(r[0], [])
        prox = vivos[0]["validade"] if vivos else None
        out.append({"id": r[0], "nome": r[1], "unidade": r[2], "preco_centavos": int(r[3] or 0),
                    "preco": cc.reais(r[3]) if r[3] else "sem preço", "saldo": Decimal(str(r[4] or 0)),
                    "saldo_txt": _fmt_qtd(r[4] or 0), "recompra_dias": r[5], "categoria": r[6],
                    "custo_medio": int(r[7] or 0),
                    "validade": prox, "vence_logo": bool(prox and prox <= hoje + timedelta(days=VENCE_DIAS)),
                    "vencido": bool(prox and prox < hoje)})
    return out


def perto_de_vencer(c, conta_id: int, hoje: date, dias: int = VENCE_DIAS) -> list[dict]:
    nomes = {p["id"]: p for p in produtos(c, conta_id, hoje)}
    out = []
    for pid, vivos in _lotes_vivos(c, conta_id).items():
        for lote in vivos:
            if lote["validade"] <= hoje + timedelta(days=dias) and pid in nomes:
                out.append({"produto_id": pid, "nome": nomes[pid]["nome"], "validade": lote["validade"],
                            "quantidade": _fmt_qtd(lote["quantidade"]), "vencido": lote["validade"] < hoje,
                            "dias": (lote["validade"] - hoje).days})
    return sorted(out, key=lambda x: x["validade"])


def salvar_recompra(c, conta_id: int, produto_id: int, dias) -> str | None:
    s = str(dias or "").strip()
    if s and (not s.isdigit() or not 1 <= int(s) <= 730):
        return "Duração de 1 a 730 dias (ou vazio: o produto não tem reposição)."
    r = c.execute("update catalogo_produtos set recompra_dias=%s, atualizado_em=now() "
                  "where id=%s and fornecedor_id=%s returning id",
                  (int(s) if s else None, produto_id, conta_id)).fetchone()
    return None if r else "Produto não encontrado."


def entrada(pool, conta_id: int, *, produto_id: int | None, quantidade, custo, validade,
            membro_id: int | None, hoje: date | None = None) -> str | None:
    """Entrada de estoque com a validade do lote (o estoque é o do catálogo). Custo em
    branco: o custo médio atual (entrada a custo zero derrubaria o custo médio)."""
    from finance import catalogo as cat
    hoje = hoje or ca.hoje_br()
    qtd = _qtd(quantidade)
    if not produto_id or qtd is None:
        return "Informe o produto e a quantidade."
    with pool.connection() as c:
        p = next((x for x in produtos(c, conta_id, hoje) if x["id"] == int(produto_id)), None)
    if not p:
        return "Produto não encontrado."
    if str(custo or "").strip():
        custo_c = cc.centavos(str(custo))
        if custo_c is None:
            return "Custo inválido: use um valor como 45 ou 45,90."
    elif p["custo_medio"]:
        custo_c = p["custo_medio"]
    else:
        return "Informe o custo por unidade (é a primeira entrada deste produto)."
    try:
        val = date.fromisoformat(str(validade or "").strip()) if str(validade or "").strip() else None
    except ValueError:
        return "Validade inválida."
    if val and val < hoje:
        return "Esse lote já está vencido: registre como perda na tela de Produtos."
    try:
        cat.registrar_movimentacao(pool, conta_id, int(produto_id), "entrada", float(qtd), custo_c,
                                   motivo="entrada pela clínica")
    except ValueError as e:
        return str(e)
    if val:
        with pool.connection() as c:
            mov = c.execute("""select max(id) from estoque_mov where fornecedor_id=%s and produto_id=%s
                                 and tipo='entrada'""", (conta_id, int(produto_id))).fetchone()[0]
            c.execute("""insert into clinica_produto_lotes (conta_id, produto_id, mov_id, quantidade, validade,
                                                            criado_por)
                         values (%s,%s,%s,%s,%s,%s)""", (conta_id, int(produto_id), mov, qtd, val, membro_id))
            c.commit()
    return None


# ------------------------------------------------------------------ a venda

def _paciente(c, conta_id: int, evento_id: int | None, lead: int | None) -> dict | None:
    """O paciente pelo atendimento ou pelo card — nunca pelo que veio digitado."""
    if evento_id:
        ev = ca.evento(c, conta_id, evento_id)
        if ev:
            return {"evento_id": ev["id"], "lead": ev["lead"], "paciente": ev["paciente"], "fone": ev["fone"],
                    "situacao": ev["situacao"]}
        return None
    if lead:
        r = c.execute("""select coalesce(nullif(contato,''), empresa, ''), coalesce(nullif(whatsapp,''), telefone, '')
                           from prospeccao where id=%s and conta_id=%s""", (lead, conta_id)).fetchone()
        if r:
            return {"evento_id": None, "lead": lead, "paciente": r[0] or "Paciente", "fone": r[1], "situacao": None}
    return None


def vender(pool, conta_id: int, *, evento_id: int | None = None, lead: int | None = None,
           itens: list[tuple], pagamento: str, membro_id: int | None, hoje: date | None = None
           ) -> tuple[dict | None, str | None]:
    """itens: [(produto_id, quantidade)]. Preço do cadastro; o assinante leva o desconto
    do plano dele sozinho. Devolve ({total, desconto, plano}, None) ou (None, erro).

    A venda do balcão faz os próprios commits; por isso a linha da venda da clínica é
    gravada ANTES (sem lançamento) e apagada se o balcão recusar. Uma venda de cada vez
    por conta, e a mesma venda repetida em 90 segundos é recusada (clique duplo)."""
    from finance import clinica_assinaturas as cas
    from finance import pdv
    hoje = hoje or ca.hoje_br()
    if pagamento not in PAGAMENTOS:
        return None, "Escolha a forma de pagamento."
    with pool.connection() as lockc:
        lockc.execute("select pg_advisory_lock(%s, %s)", (_LOCK, conta_id))
        try:
            with pool.connection() as c:
                pac = _paciente(c, conta_id, evento_id, lead)
                if not pac:
                    return None, "Paciente não encontrado."
                if pac["evento_id"] and pac["situacao"] not in SITUACOES_VENDA:
                    return None, "A venda é no atendimento: marque o paciente como presente primeiro."
                cat = {p["id"]: p for p in produtos(c, conta_id, hoje)}
                linhas = []
                for pid, q in itens:
                    qtd = _qtd(q, hi=Decimal("99"))
                    p = cat.get(int(pid)) if str(pid or "").isdigit() else None
                    if not p or qtd is None:
                        return None, "Escolha o produto e a quantidade (até 99)."
                    if not p["preco_centavos"]:
                        return None, f"{p['nome']} está sem preço de venda: cadastre em Produtos."
                    linhas.append((p, qtd))
                if not linhas:
                    return None, "Escolha o produto."
                for p, qtd in linhas:
                    if c.execute("""select 1 from clinica_produto_vendas
                                     where conta_id=%s and produto_id=%s and quantidade=%s
                                       and coalesce(evento_id, 0) = coalesce(%s, 0)
                                       and coalesce(prospeccao_id, 0) = coalesce(%s, 0)
                                       and criado_em > now() - make_interval(secs => %s) limit 1""",
                                 (conta_id, p["id"], qtd, pac["evento_id"], pac["lead"], REPETIDA_SEG)).fetchone():
                        return None, f"{p['nome']} acabou de ser vendido para {pac['paciente']}: confira antes de repetir."
                a = cas.do_paciente(c, conta_id, pac["lead"], pac["fone"], pac["paciente"], so_card=True)
                pct = a["desconto_produto_pct"] if a and a["desconto_produto_pct"] else 0.0
                brutos = [int((Decimal(p["preco_centavos"]) * q).quantize(Decimal("1"))) for p, q in linhas]
                descontos = [round(b * pct / 100) for b in brutos]
                ids = []
                for (p, q), b, d in zip(linhas, brutos, descontos):
                    rec = hoje + timedelta(days=int(p["recompra_dias"] * q)) if p["recompra_dias"] else None
                    ids.append(c.execute(
                        """insert into clinica_produto_vendas (conta_id, produto_id, evento_id, prospeccao_id,
                                                              paciente_nome, paciente_fone, quantidade,
                                                              valor_centavos, desconto_centavos, recompra_em,
                                                              recompra_estado, criado_por)
                           values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id""",
                        (conta_id, p["id"], pac["evento_id"], pac["lead"], pac["paciente"][:120],
                         (pac["fone"] or "")[:40], q, b - d, d, rec, "aguardando" if rec else "sem",
                         membro_id)).fetchone()[0])
                c.commit()
                try:
                    r = pdv.registrar_venda_balcao(
                        pool, conta_id,
                        [{"produto_id": p["id"], "quantidade": q, "preco_unit_centavos": p["preco_centavos"]}
                         for p, q in linhas],
                        cliente_nome=pac["paciente"], cliente_telefone=pac["fone"] or None, pagamento=pagamento,
                        desconto_centavos=sum(descontos), membro_id=membro_id,
                        vencimento=(hoje + timedelta(days=30)).isoformat() if pagamento == "fiado" else None)
                except Exception as e:  # noqa: BLE001 — o balcão recusou (estoque) ou caiu: a venda não houve
                    c.execute("delete from clinica_produto_vendas where conta_id=%s and id = any(%s)", (conta_id, ids))
                    c.commit()
                    if isinstance(e, ValueError):
                        return None, f"Não deu pra vender: {e}"
                    raise
                # comprou de novo: a reposição que estava esperando está feita (pelo card, ou
                # pelo celular quando o atendimento não tem card)
                dig = ca._digitos(pac["fone"])[-8:]
                for p, _q in linhas:
                    c.execute(
                        r"""update clinica_produto_vendas set recompra_estado='comprou'
                             where conta_id=%s and produto_id=%s and recompra_estado in ('aguardando','lembrado')
                               and id <> all(%s)
                               and (prospeccao_id = %s
                                    or (%s::bigint is null and length(%s) = 8
                                        and right(regexp_replace(paciente_fone, '\D', '', 'g'), 8) = %s))""",
                        (conta_id, p["id"], ids, pac["lead"], pac["lead"], dig, dig))
                c.execute("""update clinica_produto_vendas set lancamento_id=%s, titulo_id=%s
                              where conta_id=%s and id = any(%s)""",
                          (r.get("lancamento_id"), r.get("titulo_id"), conta_id, ids))
                c.commit()
        finally:
            lockc.execute("select pg_advisory_unlock(%s, %s)", (_LOCK, conta_id))
    return {"total": r.get("total_centavos"), "desconto": sum(descontos), "plano": a["nome"] if pct else ""}, None


def vendas_do_evento(c, conta_id: int, evento_id: int) -> list[dict]:
    try:
        with c.transaction():
            rows = c.execute(
                """select p.nome, v.quantidade, v.valor_centavos, v.desconto_centavos, v.recompra_em
                     from clinica_produto_vendas v
                     join catalogo_produtos p on p.id = v.produto_id and p.fornecedor_id = v.conta_id
                    where v.conta_id=%s and v.evento_id=%s order by v.id""", (conta_id, evento_id)).fetchall()
    except Exception as e:  # noqa: BLE001
        if _falta_migracao(e):
            return []
        raise
    return [{"nome": r[0], "qtd": _fmt_qtd(r[1]), "valor": cc.reais(r[2]), "desconto": r[3],
             "recompra_em": r[4]} for r in rows]


def recompras(c, conta_id: int, hoje: date, dias: int = RECOMPRA_ANTES, lead: int | None = None) -> list[dict]:
    """A reposição chegando (e a que já passou e ninguém resolveu), de produto ativo."""
    try:
        with c.transaction():
            rows = c.execute(
                """select v.id, v.produto_id, p.nome, v.prospeccao_id, v.paciente_nome, v.paciente_fone,
                          v.recompra_em, v.recompra_estado, v.criado_em
                     from clinica_produto_vendas v
                     join catalogo_produtos p on p.id = v.produto_id and p.fornecedor_id = v.conta_id
                    where v.conta_id=%s and p.ativo and v.recompra_estado in ('aguardando','lembrado')
                      and v.recompra_em <= %s and v.recompra_em > %s - interval '90 days'
                      and (%s::bigint is null or v.prospeccao_id = %s)
                    order by v.recompra_em, v.id""",
                (conta_id, hoje + timedelta(days=dias), hoje, lead, lead)).fetchall()
    except Exception as e:  # noqa: BLE001
        if _falta_migracao(e):
            return []
        raise
    return [{"id": r[0], "produto_id": r[1], "produto": r[2], "lead": r[3], "paciente": r[4], "fone": r[5],
             "recompra_em": r[6], "estado": r[7], "comprado_em": ca.local(r[8]).date(),
             "chegou": r[6] <= hoje} for r in rows]


def sugestoes(c, conta_id: int, ev: dict, hoje: date) -> dict:
    """O que a tela do agendamento sugere no fim do atendimento: a reposição do que o
    paciente já leva e o que vence logo na prateleira."""
    rep = recompras(c, conta_id, hoje, dias=30, lead=ev.get("lead")) if ev.get("lead") else []
    return {"recompra": rep, "vencendo": [x for x in perto_de_vencer(c, conta_id, hoje) if not x["vencido"]][:5]}


def dispensar(c, conta_id: int, venda_id: int) -> bool:
    return c.execute("""update clinica_produto_vendas set recompra_estado='dispensado'
                         where id=%s and conta_id=%s and recompra_estado in ('aguardando','lembrado') returning id""",
                     (venda_id, conta_id)).fetchone() is not None


def vendido_no_periodo(c, conta_id: int, a: datetime, b: datetime) -> int:
    """O que entrou de produto no período (o fiado fica fora: é a receber)."""
    try:
        with c.transaction():
            return int(c.execute("""select coalesce(sum(valor_centavos), 0) from clinica_produto_vendas
                                     where conta_id=%s and criado_em >= %s and criado_em < %s
                                       and titulo_id is null and lancamento_id is not null""",
                                 (conta_id, a, b)).fetchone()[0])
    except Exception:  # noqa: BLE001 — sem a 386
        return 0


# ------------------------------------------------------------------ o lembrete da reposição

def texto_recompra(nome: str) -> str:
    """Sem o nome do produto: o nome pode dizer o tratamento."""
    from finance.voltar_a_chamar import primeiro_nome
    n = primeiro_nome(nome)
    return (f"Oi{', ' + n if n else ''}! Já deve estar na hora de repor o produto que você levou aqui da "
            "clínica 😊 Quer que eu separe outro pra você? É só responder por aqui.")


def lembrar(c, conta_id: int, agora: datetime) -> int:
    """Uma passada: a reposição que chegou, 1 lembrete por pessoa (os produtos que
    vencem juntos vão no mesmo), dentro da janela, 1 automática por paciente por dia
    (somando vaga, plano, pacote e voltar a chamar), sem quem pediu SAIR/PARAR. Se o
    envio falhar, tenta no dia seguinte."""
    from finance import clinica_pacotes as ckp
    from finance import clinica_planos as cp
    from finance import clinica_vagas as cvg
    from finance import funil_regua as fr
    if config(c, conta_id)["recompra"] != "ligado" or not fr.dentro_da_janela(agora, fr.config(c, conta_id)):
        c.commit()
        return 0
    hoje = ca.hoje_br(agora)
    bloq = cvg._bloqueados(c, conta_id)
    inicio_dia = ca.utc(hoje, time(0))
    por_conversa: dict[int, dict] = {}
    for r in recompras(c, conta_id, hoje, dias=0):
        if r["estado"] != "aguardando" or r["comprado_em"] > hoje - timedelta(days=2):
            continue
        conv = ca._conversa(c, conta_id, {"lead": r["lead"], "fone": r["fone"]})
        if not conv:
            continue
        g = por_conversa.setdefault(conv, {"ids": [], "paciente": r["paciente"]})
        g["ids"].append(r["id"])
    n = 0
    for conv, g in por_conversa.items():
        destino = cp._destino_da_conversa(c, conta_id, conv)
        if not destino or destino[-8:] in bloq or cvg._recebeu_hoje(c, conta_id, conv, inicio_dia):
            continue
        if c.execute("""select 1 from clinica_lembretes where conta_id=%s and conversa_id=%s and tipo='recompra'
                         and estado='falhou' and enviado_em >= %s limit 1""",
                     (conta_id, conv, inicio_dia)).fetchone():
            continue                        # o chip caiu hoje: amanhã tenta de novo
        c.execute("""update clinica_produto_vendas set recompra_estado='lembrado'
                      where conta_id=%s and id = any(%s)""", (conta_id, g["ids"]))
        if ckp._mandar_lembrete(c, conta_id, conv, destino, "recompra", g["ids"][0], texto_recompra(g["paciente"]),
                                agora):
            n += 1
        else:
            c.execute("""update clinica_produto_vendas set recompra_estado='aguardando'
                          where conta_id=%s and id = any(%s)""", (conta_id, g["ids"]))
            c.commit()
    c.commit()
    return n


def respondeu_recompra(c, conta_id: int, conversa_id: int) -> bool:
    """A última mensagem nossa nesta conversa é o lembrete de reposição (de até 3 dias):
    o que o paciente respondeu é pra recepção, que sabe qual é o produto."""
    try:
        with c.transaction():
            r = c.execute(
                """select l.mensagem_id,
                          (select max(m.id) from mensagens m where m.conversa_id = l.conversa_id and m.direcao = 'out')
                     from clinica_lembretes l
                    where l.conta_id=%s and l.conversa_id=%s and l.tipo='recompra' and l.estado='enviado'
                      and l.enviado_em > now() - make_interval(hours => %s)
                    order by l.id desc limit 1""", (conta_id, conversa_id, RESPOSTA_HORAS)).fetchone()
    except Exception:  # noqa: BLE001 — sem a 386
        return False
    return bool(r and r[0] and r[0] == r[1])


def rodar(pool, agora: datetime | None = None) -> dict:
    agora = agora or datetime.now(timezone.utc)
    total = {"contas": 0, "recompra": 0}
    with pool.connection() as lockc:
        if not lockc.execute("select pg_try_advisory_lock(%s)", (_LOCK,)).fetchone()[0]:
            return total
        try:
            with pool.connection() as c:
                try:
                    with c.transaction():
                        contas = [r[0] for r in c.execute(
                            """select distinct conta_id from clinica_produto_vendas
                                where recompra_estado='aguardando' and recompra_em <= %s""",
                            (ca.hoje_br(agora),)).fetchall()]
                except Exception:  # noqa: BLE001 — sem a 386
                    contas = []
                for conta_id in contas:
                    try:
                        total["recompra"] += lembrar(c, conta_id, agora)
                        total["contas"] += 1
                    except Exception:  # noqa: BLE001
                        c.rollback()
                        _log.warning("produtos: conta %s falhou", conta_id, exc_info=True)
        finally:
            lockc.execute("select pg_advisory_unlock(%s)", (_LOCK,))
    return total
