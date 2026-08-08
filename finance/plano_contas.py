"""Plano de Contas (GLOBAL) e Centros de Custo (por conta) — Fase 2.

O plano de contas é ÚNICO e do sistema: a MESMA árvore contábil pra todas as
contas (seed na migração 132). Ninguém cria nem edita contas — cada conta só
LIGA ou DESLIGA quais usa (plano_conta_habilitada). A ausência de linha ali
significa HABILITADA (default-on), então nenhuma conta precisa de backfill.

Os centros de custo são de CADA conta (unidade, filial, projeto, sócio…). Plano
e centro entram OPCIONAIS no lançamento (lancamentos.plano_conta_id /
centro_custo_id) — quem não usa, ignora, e a categoria livre segue valendo.

Multi-tenant sagrado: tudo que é por conta é escopado por conta_id.
"""
from __future__ import annotations

# ── Grupos da DRE (sintéticos: totalizam, não recebem lançamento) ─────────────
# grupo → nome exibido + papel na estrutura da DRE. A árvore ANALÍTICA vem do
# banco (plano_contas.grupo); os nomes e a ordem dos grupos vivem aqui.
GRUPOS_DRE = {
    1: {"nome": "Receita Operacional Bruta",        "papel": "receita"},
    2: {"nome": "Deduções da Receita",              "papel": "deducao"},
    3: {"nome": "Custos (CMV / CSP)",               "papel": "custo"},
    4: {"nome": "Despesas com Pessoal",             "papel": "despesa"},
    5: {"nome": "Despesas Operacionais",            "papel": "despesa"},
    6: {"nome": "Despesas Financeiras",             "papel": "despesa"},
    7: {"nome": "Não Operacional / Investimentos",  "papel": "despesa"},
}


# ── Plano de contas (leitura da árvore global + estado por conta) ─────────────
def listar_plano(pool) -> list[dict]:
    """Árvore contábil GLOBAL, ordenada (igual pra todas as contas)."""
    with pool.connection() as c:
        rows = c.execute(
            "select id, codigo, nome, grupo, natureza, ordem "
            "from plano_contas order by ordem, codigo").fetchall()
    return [{"id": r[0], "codigo": r[1], "nome": r[2], "grupo": int(r[3]),
             "natureza": r[4], "ordem": int(r[5])} for r in rows]


def habilitadas(pool, conta_id: int) -> list[dict]:
    """O plano inteiro COM o estado de liga/desliga DESTA conta. Ausência de linha
    em plano_conta_habilitada = habilitada (default-on) → LEFT JOIN + coalesce."""
    with pool.connection() as c:
        rows = c.execute(
            """select p.id, p.codigo, p.nome, p.grupo, p.natureza, p.ordem,
                      coalesce(h.habilitada, true)
                 from plano_contas p
                 left join plano_conta_habilitada h
                        on h.plano_conta_id = p.id and h.conta_id = %s
                order by p.ordem, p.codigo""",
            (conta_id,)).fetchall()
    return [{"id": r[0], "codigo": r[1], "nome": r[2], "grupo": int(r[3]),
             "natureza": r[4], "ordem": int(r[5]), "habilitada": bool(r[6])}
            for r in rows]


def arvore_habilitada(pool, conta_id: int) -> list[dict]:
    """Igual habilitadas(), mas agrupado por grupo da DRE — pra renderizar a
    árvore (com contador n_ativas/n_total por grupo)."""
    contas = habilitadas(pool, conta_id)
    grupos = []
    for g in sorted(GRUPOS_DRE):
        itens = [ct for ct in contas if ct["grupo"] == g]
        if not itens:
            continue
        meta = GRUPOS_DRE[g]
        grupos.append({"grupo": g, "nome": meta["nome"], "papel": meta["papel"],
                       "contas": itens,
                       "n_ativas": sum(1 for ct in itens if ct["habilitada"]),
                       "n_total": len(itens)})
    return grupos


def opcoes_lancamento(pool, conta_id: int) -> list[dict]:
    """Só as contas HABILITADAS, agrupadas por grupo — pro <select> do lançamento
    (optgroup por grupo). Grupo sem nenhuma habilitada some da lista."""
    grupos = []
    for g in arvore_habilitada(pool, conta_id):
        ativas = [ct for ct in g["contas"] if ct["habilitada"]]
        if ativas:
            grupos.append({"grupo": g["grupo"], "nome": g["nome"], "contas": ativas})
    return grupos


def habilitar(pool, conta_id: int, plano_conta_id: int, ativa: bool) -> None:
    """Liga/desliga uma conta do plano PRA ESTA conta (upsert). O default é ON,
    mas gravamos explícito — mais simples e previsível do que apagar a linha."""
    with pool.connection() as c:
        c.execute(
            """insert into plano_conta_habilitada (conta_id, plano_conta_id, habilitada)
                    values (%s, %s, %s)
               on conflict (conta_id, plano_conta_id)
                 do update set habilitada = excluded.habilitada""",
            (conta_id, int(plano_conta_id), bool(ativa)))
        c.commit()


def plano_conta_valido(pool, conta_id: int, plano_conta_id) -> int | None:
    """Defesa no POST do lançamento: valida que o id existe E está habilitado
    pra conta. Retorna o id (int) ou None (vazio/ inválido/ desabilitado)."""
    if not plano_conta_id:
        return None
    try:
        pid = int(plano_conta_id)
    except (TypeError, ValueError):
        return None
    with pool.connection() as c:
        r = c.execute(
            """select coalesce(h.habilitada, true)
                 from plano_contas p
                 left join plano_conta_habilitada h
                        on h.plano_conta_id = p.id and h.conta_id = %s
                where p.id = %s""",
            (conta_id, pid)).fetchone()
    return pid if (r and bool(r[0])) else None


def id_por_codigo(pool, conta_id: int, codigo) -> int | None:
    """Código do plano ('5.1.03') -> id, se existir E estiver habilitado pra esta
    conta. Serve pro bot classificar pelo código que ele escolheu. Tolera espaços
    e um nome colado ('5.1.03 Marketing' → pega o '5.1.03')."""
    if not codigo:
        return None
    cod = str(codigo).strip().split()[0] if str(codigo).strip() else ""
    if not cod:
        return None
    with pool.connection() as c:
        r = c.execute("select id from plano_contas where codigo=%s", (cod,)).fetchone()
    return plano_conta_valido(pool, conta_id, r[0]) if r else None


# ── Centros de custo (por conta) ──────────────────────────────────────────────
def listar_centros(pool, conta_id: int, incluir_inativos: bool = False) -> list[dict]:
    """Centros da conta. Por padrão só os ativos (os inativos ficam fora do
    lançamento, mas seguem no histórico dos lançamentos antigos)."""
    with pool.connection() as c:
        sql = ("select id, nome, descricao, cor, ativo, ordem "
               "from centros_custo where conta_id=%s")
        if not incluir_inativos:
            sql += " and ativo=true"
        sql += " order by ordem, nome"
        rows = c.execute(sql, (conta_id,)).fetchall()
    return [{"id": r[0], "nome": r[1], "descricao": r[2], "cor": r[3],
             "ativo": bool(r[4]), "ordem": int(r[5])} for r in rows]


def centro_por_nome(pool, conta_id: int, nome) -> int | None:
    """Nome do centro -> id (ativo, desta conta), casando sem caixa/espaços.
    Serve pro bot pegar o centro pelo nome que a pessoa falou."""
    if not nome:
        return None
    alvo = str(nome).strip().lower()
    if not alvo:
        return None
    for c in listar_centros(pool, conta_id):
        if c["nome"].strip().lower() == alvo:
            return c["id"]
    return None


def criar_centro(pool, conta_id: int, nome: str, descricao: str = "",
                 cor: str = "") -> dict:
    """Cria um centro de custo pra conta. nome é obrigatório."""
    nome = (nome or "").strip()
    if not nome:
        raise ValueError("nome do centro de custo é obrigatório")
    with pool.connection() as c:
        r = c.execute(
            """insert into centros_custo (conta_id, nome, descricao, cor)
                    values (%s, %s, %s, %s) returning id""",
            (conta_id, nome, (descricao or "").strip(), (cor or "").strip()),
        ).fetchone()
        c.commit()
    return {"id": r[0], "nome": nome, "descricao": (descricao or "").strip(),
            "cor": (cor or "").strip(), "ativo": True, "ordem": 0}


def editar_centro(pool, conta_id: int, centro_id: int, nome: str,
                  descricao: str = "", cor: str = "") -> bool:
    """Edita nome/descrição/cor de um centro da conta. Retorna False se o centro
    não é da conta (defesa multi-tenant)."""
    nome = (nome or "").strip()
    if not nome:
        raise ValueError("nome do centro de custo é obrigatório")
    with pool.connection() as c:
        r = c.execute(
            """update centros_custo set nome=%s, descricao=%s, cor=%s
                where id=%s and conta_id=%s returning id""",
            (nome, (descricao or "").strip(), (cor or "").strip(),
             int(centro_id), conta_id)).fetchone()
        c.commit()
    return r is not None


def desativar_centro(pool, conta_id: int, centro_id: int, ativo: bool = False) -> bool:
    """Desativa (ou reativa) um centro — NÃO apaga, pra preservar o histórico dos
    lançamentos que já apontam pra ele. Retorna False se não for da conta."""
    with pool.connection() as c:
        r = c.execute(
            "update centros_custo set ativo=%s where id=%s and conta_id=%s returning id",
            (bool(ativo), int(centro_id), conta_id)).fetchone()
        c.commit()
    return r is not None


def centro_custo_valido(pool, conta_id: int, centro_custo_id) -> int | None:
    """Defesa no POST do lançamento: valida que o centro é da conta e está ativo.
    Retorna o id (int) ou None."""
    if not centro_custo_id:
        return None
    try:
        cid = int(centro_custo_id)
    except (TypeError, ValueError):
        return None
    with pool.connection() as c:
        r = c.execute(
            "select 1 from centros_custo where id=%s and conta_id=%s and ativo=true",
            (cid, conta_id)).fetchone()
    return cid if r else None
