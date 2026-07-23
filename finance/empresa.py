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

# ── Desconto do EMPREGADO na folha (o que sai do líquido dele) ────────────────
# INSS PROGRESSIVO: cada alíquota incide só sobre a parte do salário DENTRO da
# faixa (igual ao holerite). Faixas em centavos: (limite_superior, alíquota).
# Acima do teto (última faixa) o desconto trava no valor do teto. Ajustar aqui
# quando a tabela do governo mudar (ou o salário mínimo reajustar as faixas).
INSS_FAIXAS = (
    (162100, 0.075),   # até 1.621,00
    (290284, 0.090),   # 1.621,01 até 2.902,84
    (435427, 0.120),   # 2.902,85 até 4.354,27
    (847555, 0.140),   # 4.354,28 até 8.475,55 (teto)
)
INSS_TETO_CENTAVOS = INSS_FAIXAS[-1][0]
# percentual do salário descontado de quem opta por vale-transporte (máx. legal)
VALE_TRANSPORTE_PCT = 0.06


def inss_desconto_centavos(base_centavos: int) -> int:
    """Desconto de INSS do empregado sobre `base_centavos`, PROGRESSIVO por faixa.
    Cada alíquota incide só sobre o trecho do salário dentro da sua faixa; acima
    do teto o desconto para no valor do teto. Retorna centavos (arredondado)."""
    base = max(0, int(base_centavos))
    base = min(base, INSS_TETO_CENTAVOS)   # trava no teto
    total = 0.0
    piso = 0
    for limite, aliq in INSS_FAIXAS:
        if base <= piso:
            break
        trecho = min(base, limite) - piso
        total += trecho * aliq
        piso = limite
    return int(round(total))


def vale_transporte_desconto_centavos(salario_centavos: int,
                                      opta: bool = True) -> int:
    """Desconto de vale-transporte: 6% do salário (limite legal do empregado).
    Só desconta quem opta pelo benefício. Retorna centavos (arredondado)."""
    if not opta:
        return 0
    return int(round(max(0, int(salario_centavos)) * VALE_TRANSPORTE_PCT))


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


def acesso_pj(pool, conta_id: int) -> bool:
    """Gate das abas do PJ base (Produtos/Servicos/Vendas). Uma conta acessa o PJ
    base se tem o modulo PJ OU se e' fornecedor. Motivo: os produtos vivem no PJ
    base e o marketplace le' de la' — entao ser fornecedor IMPLICA usar o PJ base.
    Ponto unico de decisao: menu e rotas usam esta funcao pra nunca discordarem."""
    if modulo_pj_ativo(pool, conta_id):
        return True
    with pool.connection() as c:
        r = c.execute(
            "select 1 from contas where id=%s and eh_fornecedor",
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
                 criado_por: int | None = None,
                 cliente_id: int | None = None) -> dict:
    """Cria um título aberto. tipo: 'pagar' | 'receber'. cliente_id LIGA o título
    a um cliente da base (honorário/venda a prazo aparece na ficha dele)."""
    if tipo not in ("pagar", "receber"):
        raise ValueError("tipo deve ser 'pagar' ou 'receber'")
    if not categoria:
        categoria = CAT_FORNECEDORES if tipo == "pagar" else CAT_VENDAS
    cli_id = int(cliente_id) if cliente_id else None
    with pool.connection() as c:
        r = c.execute(
            """insert into titulos
                 (conta_id, tipo, descricao, contraparte, valor_centavos,
                  vencimento, categoria, recorrente, criado_por, cliente_id)
               values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id""",
            (conta_id, tipo, (descricao or "").strip(), (contraparte or "").strip(),
             int(valor_centavos), vencimento, categoria, bool(recorrente),
             criado_por, cli_id),
        ).fetchone()
        c.commit()
    return {"id": r[0], "tipo": tipo, "descricao": descricao,
            "valor_centavos": int(valor_centavos), "vencimento": vencimento,
            "status": "aberto", "recorrente": bool(recorrente),
            "cliente_id": cli_id}


def listar_titulos(pool, conta_id: int, status: str = "aberto",
                   tipo: str | None = None, limite: int = 100) -> list[dict]:
    """Títulos da conta. Abertos vêm por vencimento; pagos, do mais recente."""
    cond = "t.conta_id=%s and t.status=%s"
    args: list = [conta_id, status]
    if tipo in ("pagar", "receber"):
        cond += " and t.tipo=%s"
        args.append(tipo)
    ordem = "t.vencimento asc, t.id asc" if status == "aberto" else "t.pago_em desc, t.id desc"
    with pool.connection() as c:
        rows = c.execute(
            f"""select t.id, t.tipo, t.descricao, t.contraparte, t.valor_centavos,
                       t.vencimento, t.status, t.recorrente, t.categoria,
                       t.cobranca_link_url, t.pago_em, t.lancamento_id,
                       t.cliente_id, coalesce(p.nome, cl.nome) as cliente_nome
                  from titulos t
                  left join clientes cl on cl.id = t.cliente_id
                  left join pessoas p on p.id = cl.pessoa_id
                 where {cond}
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
            "cliente_id": r[12], "cliente_nome": r[13],
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
           "atrasados_pagar_centavos": 0, "n_atrasados_pagar": 0,
           "atrasados_receber_centavos": 0, "n_atrasados_receber": 0,
           "atrasados_centavos": 0, "n_atrasados": 0, "dias": dias}
    for tipo, soma, n, atras_v, atras_n in rows:
        if tipo == "pagar":
            res["a_pagar_centavos"] = int(soma or 0)
            res["n_pagar"] = int(n or 0)
            res["atrasados_pagar_centavos"] = int(atras_v or 0)
            res["n_atrasados_pagar"] = int(atras_n or 0)
        else:
            res["a_receber_centavos"] = int(soma or 0)
            res["n_receber"] = int(n or 0)
            res["atrasados_receber_centavos"] = int(atras_v or 0)
            res["n_atrasados_receber"] = int(atras_n or 0)
        # combinado mantido p/ compat com quem já lê atrasados_centavos/n_atrasados
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
    # Baixa ATÔMICA numa ÚNICA transação/conexão: o status vira num UPDATE ...
    # WHERE status='aberto' RETURNING e, na MESMA conexão, o lançamento entra
    # (adicionar(conn=c)) e o lancamento_id/recorrente são gravados — tudo commita
    # junto. Benefícios sobre a versão de 3 conexões:
    #   • título e lançamento entram juntos ou não entram (se o insert do caixa
    #     falhar, o UPDATE do status faz rollback e o título volta a 'aberto');
    #   • uma conexão por chamada — dispensa pool com max_size ≥ 2.
    # A corrida (webhook duplicado / duplo-toque) segue fechada: o UPDATE toma o
    # lock da linha até o commit; a 2ª chamada reavalia o WHERE já com 'pago' e
    # volta vazia, sem lançar.
    proximo_id = None
    with pool.connection() as c:
        t = c.execute(
            """update titulos set status='pago', pago_em=%s
                where id=%s and conta_id=%s and status='aberto'
             returning tipo, descricao, contraparte, valor_centavos, categoria,
                       recorrente, vencimento""",
            (data_pagto, titulo_id, conta_id),
        ).fetchone()
        if not t:
            # não ganhou a corrida: ou o título não existe, ou já não está aberto
            estado = c.execute(
                "select status from titulos where id=%s and conta_id=%s",
                (titulo_id, conta_id),
            ).fetchone()
            if not estado:
                return {"ok": False, "erro": "Título não encontrado."}
            return {"ok": False, "erro": f"Título já está '{estado[0]}'."}

        tipo_lanc = Tipo.DESPESA if t[0] == "pagar" else Tipo.RECEITA
        quem = f" — {t[2]}" if t[2] else ""
        lanc = Lancamento(tipo=tipo_lanc, valor_centavos=int(t[3]),
                          categoria=t[4] or (CAT_FORNECEDORES if t[0] == "pagar"
                                             else CAT_VENDAS),
                          descricao=f"{t[1]}{quem}", data=data_pagto,
                          origem="titulo", natureza="empresa")
        # mesma conexão c: o lançamento é atômico com a baixa do título
        salvo = LivroCaixa(pool, conta_id, membro_id).adicionar(
            lanc, forcar=True, conn=c)

        c.execute(
            """update titulos set lancamento_id=%s
                where id=%s and conta_id=%s""",
            (salvo.id, titulo_id, conta_id),
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


def editar_descricao_titulo(pool, conta_id: int, titulo_id: int,
                            nova_descricao: str) -> bool:
    """Atalho: edita só a descrição. Mantido por compatibilidade."""
    return editar_titulo(pool, conta_id, titulo_id, descricao=nova_descricao)


def editar_titulo(pool, conta_id: int, titulo_id: int,
                  descricao: str | None = None,
                  valor_centavos: int | None = None) -> bool:
    """Corrige descrição e/ou valor de um título (o dono digitou errado). NÃO mexe
    em vencimento nem tipo. Multi-tenant: só o título DESTA conta. Passa só o que
    quer mudar; campo None é ignorado. Descrição vazia é ignorada (não apaga);
    valor negativo é rejeitado. Retorna True se algo mudou."""
    sets, args = [], []
    if descricao is not None:
        desc = descricao.strip()[:200]
        if desc:
            sets.append("descricao=%s"); args.append(desc)
    if valor_centavos is not None and int(valor_centavos) >= 0:
        sets.append("valor_centavos=%s"); args.append(int(valor_centavos))
    if not sets:
        return False
    with pool.connection() as c:
        cur = c.execute(
            f"update titulos set {', '.join(sets)} where id=%s and conta_id=%s",
            (*args, titulo_id, conta_id),
        )
        c.commit()
        return cur.rowcount > 0


def apagar_titulo(pool, conta_id: int, titulo_id: int) -> bool:
    """Apaga de vez um titulo em ABERTO (lancado por engano). So' mexe em titulo
    'aberto' DESTA conta - um titulo ja' PAGO gerou lancamento no caixa e NAO pode
    sumir por aqui (o dinheiro e' real; apagar so' o titulo deixaria o caixa
    orfao). Pra titulo pago, o caminho e' apagar o lancamento no financeiro.
    Retorna True se apagou."""
    with pool.connection() as c:
        cur = c.execute(
            "delete from titulos where id=%s and conta_id=%s and status='aberto'",
            (titulo_id, conta_id),
        )
        c.commit()
        return cur.rowcount > 0


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
                      pro_labore: bool = False, vale_transporte: bool = False,
                      admitido_em: date | None = None) -> dict:
    with pool.connection() as c:
        r = c.execute(
            """insert into funcionarios
                 (conta_id, nome, cargo, salario_centavos, dia_pagamento,
                  pro_labore, vale_transporte, admitido_em)
               values (%s,%s,%s,%s,%s,%s,%s,%s) returning id""",
            (conta_id, (nome or "").strip(), (cargo or "").strip(),
             int(salario_centavos), int(dia_pagamento), bool(pro_labore),
             bool(vale_transporte), admitido_em),
        ).fetchone()
        c.commit()
    return {"id": r[0], "nome": nome, "salario_centavos": int(salario_centavos)}


def atualizar_funcionario(pool, conta_id: int, funcionario_id: int, *,
                          nome: str | None = None, cargo: str | None = None,
                          salario_centavos: int | None = None,
                          dia_pagamento: int | None = None,
                          vale_transporte: bool | None = None) -> bool:
    sets, args = [], []
    if nome is not None:
        sets.append("nome=%s"); args.append(nome.strip())
    if cargo is not None:
        sets.append("cargo=%s"); args.append(cargo.strip())
    if salario_centavos is not None:
        sets.append("salario_centavos=%s"); args.append(int(salario_centavos))
    if dia_pagamento is not None:
        sets.append("dia_pagamento=%s"); args.append(int(dia_pagamento))
    if vale_transporte is not None:
        sets.append("vale_transporte=%s"); args.append(bool(vale_transporte))
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
                       pro_labore, ativo, admitido_em, vale_transporte
                  from funcionarios where {cond}
                 order by pro_labore, nome""",
            (conta_id,),
        ).fetchall()
    return [{
        "id": r[0], "nome": r[1], "cargo": r[2],
        "salario_centavos": int(r[3] or 0), "dia_pagamento": int(r[4] or 5),
        "pro_labore": bool(r[5]), "ativo": bool(r[6]), "admitido_em": r[7],
        "vale_transporte": bool(r[8]),
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
                          origem="folha", natureza="empresa")
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
        # Descontos legais do líquido do funcionário:
        # - INSS progressivo sobre a REMUNERAÇÃO (salário + extras/horas-extra;
        #   vale/desconto não entram na base). Só CLT — pró-labore do sócio não
        #   usa esta tabela.
        # - Vale-transporte: 6% do SALÁRIO base (por lei incide sobre o salário,
        #   não sobre extras), pra quem optou.
        base_inss = f["salario_centavos"] + extras
        inss = 0 if f["pro_labore"] else inss_desconto_centavos(base_inss)
        vt = vale_transporte_desconto_centavos(
            f["salario_centavos"], f.get("vale_transporte", False))
        liquido = f["salario_centavos"] + extras - vales - descontos - inss - vt
        a_pagar = max(liquido - pago, 0)
        total_a_pagar += a_pagar
        total_pago += pago
        total_custo += custo_real_centavos(
            f["salario_centavos"] + extras, f["pro_labore"])
        itens.append({**f, "vales_centavos": vales, "extras_centavos": extras,
                      "descontos_centavos": descontos, "pago_centavos": pago,
                      "inss_centavos": inss, "vt_centavos": vt,
                      "liquido_centavos": liquido,
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
    registra o evento 'pagamento' e lança a despesa 'Pessoal' no caixa.

    ATÔMICO: ledger (lançamentos) + folha_eventos de todos os itens entram numa
    ÚNICA transação — ou paga a folha inteira, ou nada (nunca fica meio paga)."""
    folha = folha_do_mes(pool, conta_id, ano, mes)
    comp = folha["competencia"]
    livro = LivroCaixa(pool, conta_id, membro_id)
    pagos, total = [], 0
    with pool.connection() as c:
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
                              origem="folha", natureza="empresa")
            # mesma conexão/transação do loop: ledger + folha_eventos juntos
            salvo = livro.adicionar(lanc, forcar=True, conn=c)
            c.execute(
                """insert into folha_eventos
                     (conta_id, funcionario_id, tipo, valor_centavos,
                      competencia, descricao, lancamento_id)
                   values (%s,%s,'pagamento',%s,%s,%s,%s)""",
                (conta_id, item["id"], valor, comp,
                 f"{rotulo} {mes:02d}/{ano}", salvo.id),
            )
            pagos.append({"funcionario_id": item["id"], "nome": item["nome"],
                          "valor_centavos": valor, "lancamento_id": salvo.id})
            total += valor
        c.commit()   # um único commit: ou paga a folha toda, ou nada
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
    livro-caixa, que jogaria 'Pessoal'/'Fornecedores' em 'Outros').

    Só entra o que está classificado como empresa (natureza='empresa'); os
    lançamentos "a definir" (natureza null) ficam FORA do resultado — podem ser
    gasto pessoal. Pra o número nunca sair silenciosamente magro, devolvemos
    a_definir_n/a_definir_centavos: quanto ficou de fora, pra tela avisar."""
    ini = date(ano, mes, 1)
    fim = _mes_seguinte(ini)
    with pool.connection() as c:
        rows = c.execute(
            """select tipo, categoria, sum(valor_centavos)
                 from lancamentos
                where conta_id=%s and data >= %s and data < %s
                  and natureza='empresa'
                group by tipo, categoria""",
            (conta_id, ini, fim),
        ).fetchall()
        # o que ficou de fora do DRE por ainda não estar classificado
        adef = c.execute(
            """select count(*), coalesce(sum(valor_centavos),0)
                 from lancamentos
                where conta_id=%s and data >= %s and data < %s
                  and natureza is null""",
            (conta_id, ini, fim),
        ).fetchone()
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
            "top_receitas": receitas[:top], "top_despesas": despesas[:top],
            "a_definir_n": int(adef[0] or 0),
            "a_definir_centavos": int(adef[1] or 0)}


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
            """select data, tipo, categoria, descricao, valor_centavos, origem,
                      natureza
                 from lancamentos
                where conta_id=%s and data >= %s and data < %s
                order by data, id""",
            (conta_id, ini, fim),
        ).fetchall()
    linhas = ["data;tipo;categoria;descricao;valor;origem;natureza"]
    for d, t, cat, desc, v, orig, nat in lanc:
        desc = (desc or "").replace(";", ",").replace("\n", " ")
        linhas.append(
            f"{d};{t};{cat};{desc};{brl(int(v or 0))};{orig};{nat or 'a definir'}")

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


# ---------------------------------------------------------------------------
# Dados cadastrais da empresa (cadastro pós-upgrade PF->PJ)
# ---------------------------------------------------------------------------
def o_que_vende(pool, conta_id: int) -> dict:
    """O que a conta vende: {produto: bool, servico: bool}.

    A verdade é o par de colunas contas.vende_produto/vende_servico (decisão do
    cliente, parte A+). Enquanto NULL (não definido ainda), cai no padrão do
    nicho da conta. Nunca devolve os dois False.
    """
    from . import nichos as _n
    with pool.connection() as c:
        r = c.execute(
            """select ct.vende_produto, ct.vende_servico, n.slug
                 from contas ct
                 left join nichos n on n.id = ct.nicho_id
                where ct.id=%s""",
            (conta_id,),
        ).fetchone()
    slug = r[2] if r else None
    prod = r[0] if r and r[0] is not None else _n.vende_produto(slug)
    serv = r[1] if r and r[1] is not None else _n.vende_servico(slug)
    if not prod and not serv:  # segurança: nunca deixa os dois desligados
        prod = True
    return {"produto": bool(prod), "servico": bool(serv)}


def definir_o_que_vende(pool, conta_id: int, *, produto=None, servico=None) -> dict:
    """Liga/desliga produto e/ou servico pra conta (parte A+: destravar a aba).

    Passe só o que quer mudar (produto=True liga a aba Produtos, etc.). O que vier
    None mantém o valor atual. NUNCA deixa os dois desligados (ignora um desligar
    que zeraria tudo). Devolve o estado final {produto, servico}.
    """
    atual = o_que_vende(pool, conta_id)
    novo_prod = atual["produto"] if produto is None else bool(produto)
    novo_serv = atual["servico"] if servico is None else bool(servico)
    if not novo_prod and not novo_serv:  # não deixa a conta sem nada pra vender
        return atual
    with pool.connection() as c:
        c.execute(
            "update contas set vende_produto=%s, vende_servico=%s where id=%s",
            (novo_prod, novo_serv, conta_id),
        )
        c.commit()
    return {"produto": novo_prod, "servico": novo_serv}


def dados_empresa_completos(pool, conta_id: int) -> bool:
    """True se a empresa já tem os dados mínimos preenchidos (CNPJ + razão).

    Usado como gate da aba Empresa: enquanto False, a aba mostra só a tela de
    preenchimento. O mínimo é documento (CNPJ) com 14 dígitos e razao_social.
    """
    with pool.connection() as c:
        r = c.execute(
            "select documento, razao_social from contas where id=%s",
            (conta_id,),
        ).fetchone()
    if not r:
        return False
    doc = "".join(ch for ch in (r[0] or "") if ch.isdigit())
    razao = (r[1] or "").strip()
    return len(doc) == 14 and bool(razao)


def cadastro_pj_ok(pool, conta_id: int) -> bool:
    """Gate das abas PJ (Produtos/Servicos): dados da empresa completos OU ja e'
    fornecedor com razao/cnpj preenchidos (que agora vivem em contas)."""
    return dados_empresa_completos(pool, conta_id)


def obter_dados_empresa(pool, conta_id: int) -> dict:
    """Devolve os dados cadastrais atuais da empresa (pra pré-preencher a tela)."""
    with pool.connection() as c:
        r = c.execute(
            """select coalesce(ct.documento,''), coalesce(ct.razao_social,''),
                      coalesce(ct.nome_fantasia,''), coalesce(ct.endereco,''),
                      coalesce(ct.bairro,''), coalesce(ct.cep,''),
                      coalesce(ct.cidade,''), coalesce(ct.uf,''),
                      coalesce(ct.email_empresa,''), coalesce(ct.telefone,''),
                      coalesce(n.slug,''), coalesce(ct.cnae,'')
                 from contas ct
                 left join nichos n on n.id = ct.nicho_id
                where ct.id=%s""",
            (conta_id,),
        ).fetchone()
    if not r:
        return {"documento": "", "razao_social": "", "nome_fantasia": "",
                "endereco": "", "bairro": "", "cep": "", "cidade": "", "uf": "",
                "email_empresa": "", "telefone": "", "nicho": "", "cnae": ""}
    return {"documento": r[0], "razao_social": r[1], "nome_fantasia": r[2],
            "endereco": r[3], "bairro": r[4], "cep": r[5],
            "cidade": r[6], "uf": r[7], "email_empresa": r[8], "telefone": r[9],
            "nicho": r[10], "cnae": r[11]}


def salvar_dados_empresa(pool, conta_id: int, *, documento: str = "",
                         razao_social: str = "", nome_fantasia: str = "",
                         endereco: str = "", bairro: str = "", cep: str = "",
                         cidade: str = "", uf: str = "", email_empresa: str = "",
                         telefone: str = "", nicho: str = "",
                         cnae: str = "") -> tuple[bool, str]:
    """Grava os dados da empresa na conta. Valida CNPJ (14 dígitos) e razão.

    Retorna (ok, mensagem). Multi-tenant: grava só na própria conta.
    """
    doc = "".join(ch for ch in (documento or "") if ch.isdigit())
    razao = (razao_social or "").strip()
    if len(doc) != 14:
        return False, "CNPJ inválido — informe os 14 dígitos."
    if not razao:
        return False, "Informe a razão social."
    fantasia = (nome_fantasia or "").strip() or None
    end = (endereco or "").strip() or None
    bai = (bairro or "").strip() or None
    _cep = "".join(ch for ch in (cep or "") if ch.isdigit()) or None
    cid = (cidade or "").strip() or None
    est = (uf or "").strip().upper()[:2] or None
    mail = (email_empresa or "").strip().lower() or None
    tel = (telefone or "").strip() or None
    nicho_slug = (nicho or "").strip().lower() or None
    cnae_val = (cnae or "").strip()[:180] or None
    with pool.connection() as c:
        # CNAE: guarda a atividade crua da Receita (não sobrescreve com vazio).
        if cnae_val:
            c.execute("update contas set cnae=%s where id=%s", (cnae_val, conta_id))
        # resolve o slug do nicho -> nicho_id (FK). Se nao achar, deixa como esta'.
        nicho_id = None
        if nicho_slug:
            nr = c.execute("select id from nichos where slug=%s and ativo",
                           (nicho_slug,)).fetchone()
            nicho_id = nr[0] if nr else None
        if nicho_id is not None:
            c.execute(
                """update contas
                      set documento=%s, razao_social=%s, nome_fantasia=%s,
                          endereco=%s, bairro=%s, cep=%s, cidade=%s, uf=%s,
                          email_empresa=%s, telefone=%s, nicho_id=%s
                    where id=%s""",
                (doc, razao, fantasia, end, bai, _cep, cid, est, mail, tel,
                 nicho_id, conta_id),
            )
            # inicializa o que a conta vende pelo padrão do nicho, SEM sobrescrever
            # uma escolha já feita pelo cliente (coalesce mantém o valor atual).
            from .nichos import vende_produto as _vp, vende_servico as _vs
            c.execute(
                """update contas
                      set vende_produto = coalesce(vende_produto, %s),
                          vende_servico = coalesce(vende_servico, %s)
                    where id=%s""",
                (_vp(nicho_slug), _vs(nicho_slug), conta_id),
            )
        else:
            c.execute(
                """update contas
                      set documento=%s, razao_social=%s, nome_fantasia=%s,
                          endereco=%s, bairro=%s, cep=%s, cidade=%s, uf=%s,
                          email_empresa=%s, telefone=%s
                    where id=%s""",
                (doc, razao, fantasia, end, bai, _cep, cid, est, mail, tel, conta_id),
            )
        c.commit()
    return True, "Dados da empresa salvos."
