"""Módulo Empresa (PJ) — Fase 1.

Camada GERAL de empresa (qualquer segmento), separada do nicho Fornecedor:
- Títulos a pagar/receber (a baixa vira lançamento no livro-caixa — fonte única).
- Equipe e folha GERENCIAL (vales, extras, custo real com encargos estimados).
  A folha OFICIAL (eSocial/FGTS/guias) segue com o contador — isto é gestão.
- Fluxo de caixa projetado, DRE simplificado e relatório do contador (CSV).

Gate: modulo_pj_ativo() — automático pra plano PJ (trial/ativa); conta_modulos
funciona como override de cortesia (admin libera piloto sem cobrança).

Multi-tenant sagrado: todo método é escopado por conta_id.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from .livro_caixa import LivroCaixa
from .models import Lancamento, Tipo

_log = logging.getLogger("openclaw.empresa")

# Categorias empresariais usadas nos lançamentos gerados pelo módulo.
CAT_PESSOAL = "Pessoal"
CAT_FORNECEDORES = "Fornecedores"
CAT_VENDAS = "Vendas"

# Custo real GERENCIAL (estimativa p/ Simples Nacional, transparente na tela):
# FGTS 8% + provisão 13º (1/12) + provisão férias com 1/3 ((1/12)*(4/3)).
# INSS patronal não entra (no Simples está dentro do DAS). Pró-labore: fator 1.
FATOR_ENCARGOS = 1.0 + 0.08 + (1.0 / 12.0) + (1.0 / 12.0) * (4.0 / 3.0)


# ─────────────────────────────────────────────────────────────────────────
# Gate do módulo
# ─────────────────────────────────────────────────────────────────────────
def modulo_pj_ativo(pool, conta_id: int) -> bool:
    """True se a conta tem o módulo Empresa.

    Automático: plano de tipo_conta='pj' com status trial/ativa.
    Override:   linha ativa em conta_modulos (codigo 'pj') — cortesia/admin.
    """
    with pool.connection() as c:
        r = c.execute(
            """select 1
                 from contas co join planos p on p.codigo = co.plano
                where co.id = %s and p.tipo_conta = 'pj'
                  and co.status in ('trial','ativa')""",
            (conta_id,),
        ).fetchone()
        if r:
            return True
        r = c.execute(
            "select 1 from conta_modulos where conta_id=%s and modulo='pj' and ativo",
            (conta_id,),
        ).fetchone()
    return r is not None


# ─────────────────────────────────────────────────────────────────────────
# Títulos (contas a pagar e a receber)
# ─────────────────────────────────────────────────────────────────────────
def criar_titulo(pool, conta_id: int, tipo: str, descricao: str,
                 valor_centavos: int, vencimento: date,
                 contraparte: str = "", categoria: str = "",
                 recorrente: bool = False,
                 criado_por: int | None = None) -> dict:
    """Cria um título aberto. tipo: 'pagar' | 'receber'."""
    if tipo not in ("pagar", "receber"):
        raise ValueError("tipo deve ser 'pagar' ou 'receber'")
    if not categoria:
        categoria = CAT_FORNECEDORES if tipo == "pagar" else CAT_VENDAS
    with pool.connection() as c:
        r = c.execute(
            """insert into titulos
                 (conta_id, tipo, descricao, contraparte, valor_centavos,
                  vencimento, categoria, recorrente, criado_por)
               values (%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id""",
            (conta_id, tipo, (descricao or "").strip(), (contraparte or "").strip(),
             int(valor_centavos), vencimento, categoria, bool(recorrente),
             criado_por),
        ).fetchone()
        c.commit()
    return {"id": r[0], "tipo": tipo, "descricao": descricao,
            "valor_centavos": int(valor_centavos), "vencimento": vencimento,
            "status": "aberto", "recorrente": bool(recorrente)}


def listar_titulos(pool, conta_id: int, status: str = "aberto",
                   tipo: str | None = None, limite: int = 100) -> list[dict]:
    """Títulos da conta. Abertos vêm por vencimento; pagos, do mais recente."""
    cond = "conta_id=%s and status=%s"
    args: list = [conta_id, status]
    if tipo in ("pagar", "receber"):
        cond += " and tipo=%s"
        args.append(tipo)
    ordem = "vencimento asc, id asc" if status == "aberto" else "pago_em desc, id desc"
    with pool.connection() as c:
        rows = c.execute(
            f"""select id, tipo, descricao, contraparte, valor_centavos,
                       vencimento, status, recorrente, categoria,
                       cobranca_link_url, pago_em, lancamento_id
                  from titulos where {cond}
                 order by {ordem} limit %s""",
            (*args, limite),
        ).fetchall()
    hoje = date.today()
    out = []
    for r in rows:
        venc = r[5]
        out.append({
            "id": r[0], "tipo": r[1], "descricao": r[2], "contraparte": r[3],
            "valor_centavos": int(r[4] or 0), "vencimento": venc,
            "status": r[6], "recorrente": bool(r[7]), "categoria": r[8],
            "cobranca_link_url": r[9], "pago_em": r[10], "lancamento_id": r[11],
            "atrasado": (r[6] == "aberto" and venc is not None and venc < hoje),
        })
    return out


def resumo_titulos(pool, conta_id: int, dias: int = 7) -> dict:
    """Cards da visão geral: a pagar/receber nos próximos N dias + atrasados."""
    hoje = date.today()
    fim = hoje + timedelta(days=dias)
    with pool.connection() as c:
        rows = c.execute(
            """select tipo,
                      sum(case when vencimento between %s and %s
                               then valor_centavos else 0 end),
                      count(*) filter (where vencimento between %s and %s),
                      sum(case when vencimento < %s then valor_centavos else 0 end),
                      count(*) filter (where vencimento < %s)
                 from titulos
                where conta_id=%s and status='aberto'
                group by tipo""",
            (hoje, fim, hoje, fim, hoje, hoje, conta_id),
        ).fetchall()
    res = {"a_pagar_centavos": 0, "n_pagar": 0,
           "a_receber_centavos": 0, "n_receber": 0,
           "atrasados_centavos": 0, "n_atrasados": 0, "dias": dias}
    for tipo, soma, n, atras_v, atras_n in rows:
        if tipo == "pagar":
            res["a_pagar_centavos"] = int(soma or 0)
            res["n_pagar"] = int(n or 0)
        else:
            res["a_receber_centavos"] = int(soma or 0)
            res["n_receber"] = int(n or 0)
        res["atrasados_centavos"] += int(atras_v or 0)
        res["n_atrasados"] += int(atras_n or 0)
    return res


def dar_baixa_titulo(pool, conta_id: int, titulo_id: int,
                     data_pagto: date | None = None,
                     membro_id: int | None = None) -> dict:
    """Marca o título como pago e LANÇA no livro-caixa (fonte única).

    pagar → despesa; receber → receita. Se recorrente, já cria o do mês
    seguinte. Idempotente: título já pago/cancelado não lança de novo.
    """
    data_pagto = data_pagto or date.today()
    with pool.connection() as c:
        t = c.execute(
            """select tipo, descricao, contraparte, valor_centavos, categoria,
                      recorrente, vencimento, status
                 from titulos where id=%s and conta_id=%s""",
            (titulo_id, conta_id),
        ).fetchone()
    if not t:
        return {"ok": False, "erro": "Título não encontrado."}
    if t[7] != "aberto":
        return {"ok": False, "erro": f"Título já está '{t[7]}'."}

    tipo_lanc = Tipo.DESPESA if t[0] == "pagar" else Tipo.RECEITA
    quem = f" — {t[2]}" if t[2] else ""
    lanc = Lancamento(tipo=tipo_lanc, valor_centavos=int(t[3]),
                      categoria=t[4] or (CAT_FORNECEDORES if t[0] == "pagar"
                                         else CAT_VENDAS),
                      descricao=f"{t[1]}{quem}", data=data_pagto,
                      origem="titulo")
    livro = LivroCaixa(pool, conta_id, membro_id)
    salvo = livro.adicionar(lanc, forcar=True)

    proximo_id = None
    with pool.connection() as c:
        c.execute(
            """update titulos set status='pago', pago_em=%s, lancamento_id=%s
                where id=%s and conta_id=%s and status='aberto'""",
            (data_pagto, salvo.id, titulo_id, conta_id),
        )
        if t[5]:  # recorrente mensal: cria o próximo
            prox = _mes_seguinte(t[6])
            r = c.execute(
                """insert into titulos
                     (conta_id, tipo, descricao, contraparte, valor_centavos,
                      vencimento, categoria, recorrente)
                   values (%s,%s,%s,%s,%s,%s,%s,true) returning id""",
                (conta_id, t[0], t[1], t[2], int(t[3]), prox, t[4]),
            ).fetchone()
            proximo_id = r[0]
        c.commit()
    return {"ok": True, "lancamento_id": salvo.id, "proximo_titulo_id": proximo_id}


def cancelar_titulo(pool, conta_id: int, titulo_id: int) -> bool:
    with pool.connection() as c:
        r = c.execute(
            """update titulos set status='cancelado'
                where id=%s and conta_id=%s and status='aberto' returning id""",
            (titulo_id, conta_id),
        ).fetchone()
        c.commit()
    return r is not None


def registrar_link_cobranca(pool, conta_id: int, titulo_id: int, url: str) -> None:
    """Guarda o link Asaas do 'cobrar via Pix' (títulos a receber)."""
    with pool.connection() as c:
        c.execute(
            "update titulos set cobranca_link_url=%s where id=%s and conta_id=%s",
            (url, titulo_id, conta_id),
        )
        c.commit()


def marcar_titulo_recebido(pool, conta_id: int, titulo_id: int) -> dict:
    """Webhook Asaas: cobrança do título paga online → baixa automática."""
    return dar_baixa_titulo(pool, conta_id, titulo_id)


def _mes_seguinte(d: date) -> date:
    ano, mes = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
    dia = min(d.day, 28)  # evita 31/fev
    return date(ano, mes, dia)


# ─────────────────────────────────────────────────────────────────────────
# Equipe e folha gerencial
# ─────────────────────────────────────────────────────────────────────────
def custo_real_centavos(salario_centavos: int, pro_labore: bool = False) -> int:
    """Custo mensal estimado: salário + FGTS 8% + provisões de 13º e férias+1/3.

    Estimativa GERENCIAL (Simples Nacional; INSS patronal está no DAS).
    Pró-labore não carrega esses encargos aqui (fator 1).
    """
    if pro_labore:
        return int(salario_centavos)
    return int(round(salario_centavos * FATOR_ENCARGOS))


def criar_funcionario(pool, conta_id: int, nome: str, cargo: str = "",
                      salario_centavos: int = 0, dia_pagamento: int = 5,
                      pro_labore: bool = False,
                      admitido_em: date | None = None) -> dict:
    with pool.connection() as c:
        r = c.execute(
            """insert into funcionarios
                 (conta_id, nome, cargo, salario_centavos, dia_pagamento,
                  pro_labore, admitido_em)
               values (%s,%s,%s,%s,%s,%s,%s) returning id""",
            (conta_id, (nome or "").strip(), (cargo or "").strip(),
             int(salario_centavos), int(dia_pagamento), bool(pro_labore),
             admitido_em),
        ).fetchone()
        c.commit()
    return {"id": r[0], "nome": nome, "salario_centavos": int(salario_centavos)}


def atualizar_funcionario(pool, conta_id: int, funcionario_id: int, *,
                          nome: str | None = None, cargo: str | None = None,
                          salario_centavos: int | None = None,
                          dia_pagamento: int | None = None) -> bool:
    sets, args = [], []
    if nome is not None:
        sets.append("nome=%s"); args.append(nome.strip())
    if cargo is not None:
        sets.append("cargo=%s"); args.append(cargo.strip())
    if salario_centavos is not None:
        sets.append("salario_centavos=%s"); args.append(int(salario_centavos))
    if dia_pagamento is not None:
        sets.append("dia_pagamento=%s"); args.append(int(dia_pagamento))
    if not sets:
        return False
    with pool.connection() as c:
        r = c.execute(
            f"update funcionarios set {', '.join(sets)} "
            "where id=%s and conta_id=%s returning id",
            (*args, funcionario_id, conta_id),
        ).fetchone()
        c.commit()
    return r is not None


def desativar_funcionario(pool, conta_id: int, funcionario_id: int) -> bool:
    with pool.connection() as c:
        r = c.execute(
            """update funcionarios set ativo=false
                where id=%s and conta_id=%s returning id""",
            (funcionario_id, conta_id),
        ).fetchone()
        c.commit()
    return r is not None


def listar_funcionarios(pool, conta_id: int, so_ativos: bool = True) -> list[dict]:
    cond = "conta_id=%s" + (" and ativo" if so_ativos else "")
    with pool.connection() as c:
        rows = c.execute(
            f"""select id, nome, cargo, salario_centavos, dia_pagamento,
                       pro_labore, ativo, admitido_em
                  from funcionarios where {cond}
                 order by pro_labore, nome""",
            (conta_id,),
        ).fetchall()
    return [{
        "id": r[0], "nome": r[1], "cargo": r[2],
        "salario_centavos": int(r[3] or 0), "dia_pagamento": int(r[4] or 5),
        "pro_labore": bool(r[5]), "ativo": bool(r[6]), "admitido_em": r[7],
        "custo_real_centavos": custo_real_centavos(int(r[3] or 0), bool(r[5])),
    } for r in rows]


def registrar_evento_folha(pool, conta_id: int, funcionario_id: int, tipo: str,
                           valor_centavos: int, competencia: date | None = None,
                           descricao: str = "",
                           membro_id: int | None = None) -> dict:
    """Vale / extra / desconto na competência (mês).

    VALE também SAI DO CAIXA na hora (despesa Pessoal) — dinheiro que já foi.
    Extra e desconto só ajustam a folha; viram caixa no 'pagar folha'.
    """
    if tipo not in ("vale", "extra", "desconto"):
        raise ValueError("tipo deve ser 'vale', 'extra' ou 'desconto'")
    comp = _competencia(competencia)
    with pool.connection() as c:
        f = c.execute(
            "select nome from funcionarios where id=%s and conta_id=%s and ativo",
            (funcionario_id, conta_id),
        ).fetchone()
    if not f:
        return {"ok": False, "erro": "Funcionário não encontrado."}

    lanc_id = None
    if tipo == "vale":
        lanc = Lancamento(tipo=Tipo.DESPESA, valor_centavos=int(valor_centavos),
                          categoria=CAT_PESSOAL,
                          descricao=f"Vale/adiantamento — {f[0]}"
                                    + (f" ({descricao})" if descricao else ""),
                          origem="folha")
        salvo = LivroCaixa(pool, conta_id, membro_id).adicionar(lanc, forcar=True)
        lanc_id = salvo.id

    with pool.connection() as c:
        r = c.execute(
            """insert into folha_eventos
                 (conta_id, funcionario_id, tipo, valor_centavos, competencia,
                  descricao, lancamento_id)
               values (%s,%s,%s,%s,%s,%s,%s) returning id""",
            (conta_id, funcionario_id, tipo, int(valor_centavos), comp,
             descricao, lanc_id),
        ).fetchone()
        c.commit()
    return {"ok": True, "id": r[0], "lancamento_id": lanc_id}


def folha_do_mes(pool, conta_id: int, ano: int, mes: int) -> dict:
    """A folha da competência: por funcionário, salário + extras − vales −
    descontos = a pagar; o que já foi pago; e o custo real total estimado."""
    comp = date(ano, mes, 1)
    funcs = listar_funcionarios(pool, conta_id, so_ativos=True)
    with pool.connection() as c:
        rows = c.execute(
            """select funcionario_id, tipo, coalesce(sum(valor_centavos),0)
                 from folha_eventos
                where conta_id=%s and competencia=%s
                group by funcionario_id, tipo""",
            (conta_id, comp),
        ).fetchall()
    ev: dict[int, dict[str, int]] = {}
    for fid, tipo, soma in rows:
        ev.setdefault(fid, {})[tipo] = int(soma or 0)

    itens, total_a_pagar, total_pago, total_custo = [], 0, 0, 0
    for f in funcs:
        e = ev.get(f["id"], {})
        vales = e.get("vale", 0)
        extras = e.get("extra", 0)
        descontos = e.get("desconto", 0)
        pago = e.get("pagamento", 0)
        liquido = f["salario_centavos"] + extras - vales - descontos
        a_pagar = max(liquido - pago, 0)
        total_a_pagar += a_pagar
        total_pago += pago
        total_custo += custo_real_centavos(
            f["salario_centavos"] + extras, f["pro_labore"])
        itens.append({**f, "vales_centavos": vales, "extras_centavos": extras,
                      "descontos_centavos": descontos, "pago_centavos": pago,
                      "a_pagar_centavos": a_pagar,
                      "quitado": a_pagar == 0 and liquido > 0})
    return {"competencia": comp, "itens": itens,
            "total_a_pagar_centavos": total_a_pagar,
            "total_pago_centavos": total_pago,
            "custo_real_total_centavos": total_custo}


def pagar_folha(pool, conta_id: int, ano: int, mes: int,
                funcionario_id: int | None = None,
                membro_id: int | None = None) -> dict:
    """Paga o restante da folha (de um funcionário, ou de todos) em 1 clique:
    registra o evento 'pagamento' e lança a despesa 'Pessoal' no caixa."""
    folha = folha_do_mes(pool, conta_id, ano, mes)
    comp = folha["competencia"]
    pagos, total = [], 0
    livro = LivroCaixa(pool, conta_id, membro_id)
    for item in folha["itens"]:
        if funcionario_id is not None and item["id"] != funcionario_id:
            continue
        valor = item["a_pagar_centavos"]
        if valor <= 0:
            continue
        rotulo = "Pró-labore" if item["pro_labore"] else "Folha"
        lanc = Lancamento(tipo=Tipo.DESPESA, valor_centavos=valor,
                          categoria=CAT_PESSOAL,
                          descricao=f"{rotulo} {mes:02d}/{ano} — {item['nome']}",
                          origem="folha")
        salvo = livro.adicionar(lanc, forcar=True)
        with pool.connection() as c:
            c.execute(
                """insert into folha_eventos
                     (conta_id, funcionario_id, tipo, valor_centavos,
                      competencia, descricao, lancamento_id)
                   values (%s,%s,'pagamento',%s,%s,%s,%s)""",
                (conta_id, item["id"], valor, comp,
                 f"{rotulo} {mes:02d}/{ano}", salvo.id),
            )
            c.commit()
        pagos.append({"funcionario_id": item["id"], "nome": item["nome"],
                      "valor_centavos": valor, "lancamento_id": salvo.id})
        total += valor
    return {"ok": True, "pagos": pagos, "total_centavos": total}


def _competencia(d: date | None) -> date:
    d = d or date.today()
    return date(d.year, d.month, 1)


# ─────────────────────────────────────────────────────────────────────────
# Visão do dono: fluxo projetado, DRE e relatório do contador
# ─────────────────────────────────────────────────────────────────────────
def fluxo_projetado(pool, conta_id: int, semanas: int = 4) -> dict:
    """Saldo atual (livro-caixa) + títulos abertos → saldo projetado semanal."""
    saldo = LivroCaixa(pool, conta_id).saldo_centavos()
    hoje = date.today()
    fim = hoje + timedelta(days=7 * semanas)
    with pool.connection() as c:
        rows = c.execute(
            """select vencimento, tipo, valor_centavos
                 from titulos
                where conta_id=%s and status='aberto' and vencimento <= %s
                order by vencimento""",
            (conta_id, fim),
        ).fetchall()
    pontos = []
    acumulado = saldo
    for i in range(1, semanas + 1):
        ini = hoje + timedelta(days=7 * (i - 1))
        fim_sem = hoje + timedelta(days=7 * i)
        receber = sum(int(v) for d, t, v in rows
                      if t == "receber" and d is not None and ini <= d < fim_sem)
        # atrasados (vencimento < hoje) entram todos na 1ª semana
        pagar = sum(int(v) for d, t, v in rows
                    if t == "pagar" and d is not None and ini <= d < fim_sem)
        if i == 1:
            receber += sum(int(v) for d, t, v in rows
                           if t == "receber" and d is not None and d < hoje)
            pagar += sum(int(v) for d, t, v in rows
                         if t == "pagar" and d is not None and d < hoje)
        acumulado += receber - pagar
        pontos.append({"semana": i, "ate": fim_sem,
                       "receber_centavos": receber, "pagar_centavos": pagar,
                       "saldo_centavos": acumulado})
    return {"saldo_atual_centavos": saldo, "pontos": pontos,
            "saldo_projetado_centavos": acumulado}


def dre_mes(pool, conta_id: int, ano: int, mes: int, top: int = 5) -> dict:
    """DRE simplificado do mês a partir do livro-caixa (fonte única).

    Agrega por categoria DIRETO (sem a canonização de categorias PF do
    livro-caixa, que jogaria 'Pessoal'/'Fornecedores' em 'Outros')."""
    ini = date(ano, mes, 1)
    fim = _mes_seguinte(ini)
    with pool.connection() as c:
        rows = c.execute(
            """select tipo, categoria, sum(valor_centavos)
                 from lancamentos
                where conta_id=%s and data >= %s and data < %s
                group by tipo, categoria""",
            (conta_id, ini, fim),
        ).fetchall()
    receitas = sorted(((cat, int(v or 0)) for t, cat, v in rows
                       if t == "receita"), key=lambda kv: kv[1], reverse=True)
    despesas = sorted(((cat, int(v or 0)) for t, cat, v in rows
                       if t == "despesa"), key=lambda kv: kv[1], reverse=True)
    tot_r = sum(v for _, v in receitas)
    tot_d = sum(v for _, v in despesas)
    resultado = tot_r - tot_d
    margem = round(100.0 * resultado / tot_r, 1) if tot_r else 0.0
    return {"ano": ano, "mes": mes,
            "receitas_centavos": tot_r, "despesas_centavos": tot_d,
            "resultado_centavos": resultado, "margem_pct": margem,
            "top_receitas": receitas[:top], "top_despesas": despesas[:top]}


def csv_contador(pool, conta_id: int, ano: int, mes: int) -> str:
    """Relatório do mês pro contador: todos os lançamentos + títulos abertos.

    CSV separado por ';' (Excel BR), valores em reais com vírgula.
    """
    def brl(cent: int) -> str:
        return f"{cent/100:.2f}".replace(".", ",")

    ini = date(ano, mes, 1)
    fim = _mes_seguinte(ini)
    with pool.connection() as c:
        lanc = c.execute(
            """select data, tipo, categoria, descricao, valor_centavos, origem
                 from lancamentos
                where conta_id=%s and data >= %s and data < %s
                order by data, id""",
            (conta_id, ini, fim),
        ).fetchall()
    linhas = ["data;tipo;categoria;descricao;valor;origem"]
    for d, t, cat, desc, v, orig in lanc:
        desc = (desc or "").replace(";", ",").replace("\n", " ")
        linhas.append(f"{d};{t};{cat};{desc};{brl(int(v or 0))};{orig}")

    abertos = listar_titulos(pool, conta_id, status="aberto")
    linhas.append("")
    linhas.append("TITULOS EM ABERTO")
    linhas.append("vencimento;tipo;descricao;contraparte;valor;atrasado")
    for t in abertos:
        desc = (t["descricao"] or "").replace(";", ",")
        linhas.append(
            f"{t['vencimento']};{t['tipo']};{desc};{t['contraparte']};"
            f"{brl(t['valor_centavos'])};{'sim' if t['atrasado'] else 'nao'}")
    return "\n".join(linhas) + "\n"
