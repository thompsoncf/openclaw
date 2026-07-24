"""Persistência do Módulo Doméstica — vínculos, variáveis do mês e fechamento.

Fronteira: o CÁLCULO mora em `domestica.py` (puro, autoverificável, zero import
do repo). Este arquivo é a cola com o banco e o livro-caixa — pode importar o
motor e o `LivroCaixa`, mas NÃO reimplementa regra de cálculo.

Empregador é PF: o fechamento lança no livro-caixa PESSOAL (natureza='pessoal').
Gate por `conta_modulos` (cortesia/admin) — não é automático por plano, porque a
conta doméstica é PF e não tem tipo_conta próprio. Multi-tenant: tudo por
conta_id (o id é `conta[0]` na tupla de `conta_logada`).
"""
from __future__ import annotations

import logging
from datetime import date

from .domestica import (Vinculo, Lancamentos, calcular_empregado,
                        calcular_folha, lancamentos_livro_caixa, ANO_PADRAO)
from .livro_caixa import LivroCaixa
from .models import Lancamento, Tipo

_log = logging.getLogger("openclaw.domestica")

# Categoria própria no livro-caixa pessoal (fora da lista oficial, igual o PJ faz
# com "Pessoal"): deixa o custo da casa visível como linha própria no raio-x/DRE.
CAT_DOMESTICA = "Doméstica"

_MAP_TIPO_ITEM = {
    "extra": "extras_centavos",
    "adiantamento_13": "adiantamento_13_centavos",
    "falta": "faltas_centavos",
    "desconto": "outros_descontos_centavos",
    "beneficio": "beneficios_centavos",
}


# ─────────────────────────────────────────────────────────────────────────
# Gate
# ─────────────────────────────────────────────────────────────────────────
def modulo_domestica_ativo(pool, conta_id: int) -> bool:
    """True se a conta tem o módulo Doméstica liberado (override em
    conta_modulos). Ponto único de decisão: se um dia virar plano/flag, troca-se
    só aqui."""
    with pool.connection() as c:
        r = c.execute(
            "select 1 from conta_modulos "
            "where conta_id=%s and modulo='domestica' and ativo",
            (conta_id,)).fetchone()
    return r is not None


def liberar_modulo(pool, conta_id: int, ativo: bool = True) -> None:
    """Liga/desliga o módulo pra uma conta (cortesia/admin/piloto)."""
    with pool.connection() as c:
        c.execute(
            """insert into conta_modulos (conta_id, modulo, ativo)
                 values (%s,'domestica',%s)
               on conflict (conta_id, modulo)
                 do update set ativo=excluded.ativo""",
            (conta_id, ativo))
        c.commit()


# ─────────────────────────────────────────────────────────────────────────
# Vínculos
# ─────────────────────────────────────────────────────────────────────────
def cadastrar_vinculo(pool, conta_id: int, nome: str, salario_centavos: int,
                      cargo: str = "", cpf: str = "", matricula: str = "",
                      admitido_em: date | None = None) -> int:
    """Cadastra um empregado doméstico (qualquer cargo da LC 150/2015).
    Devolve o id do vínculo."""
    with pool.connection() as c:
        r = c.execute(
            """insert into domestica_vinculo
                 (conta_id, nome, cpf, cargo, matricula, salario_centavos, admitido_em)
               values (%s,%s,%s,%s,%s,%s,%s) returning id""",
            (conta_id, nome.strip(), cpf.strip(), cargo.strip(),
             matricula.strip(), int(salario_centavos), admitido_em)).fetchone()
        c.commit()
    return r[0]


def listar_vinculos(pool, conta_id: int, so_ativos: bool = True) -> list[dict]:
    """Lista os vínculos da conta (ativos por padrão)."""
    sql = ("select id, nome, cpf, cargo, matricula, salario_centavos, "
           "admitido_em, ativo from domestica_vinculo where conta_id=%s")
    if so_ativos:
        sql += " and ativo"
    sql += " order by nome"
    with pool.connection() as c:
        rows = c.execute(sql, (conta_id,)).fetchall()
    cols = ("id", "nome", "cpf", "cargo", "matricula", "salario_centavos",
            "admitido_em", "ativo")
    return [dict(zip(cols, r)) for r in rows]


def obter_vinculo(pool, conta_id: int, vinculo_id: int) -> dict | None:
    """Um vínculo (escopado por conta) ou None."""
    vs = [v for v in listar_vinculos(pool, conta_id, so_ativos=False)
          if v["id"] == vinculo_id]
    return vs[0] if vs else None


def desativar_vinculo(pool, conta_id: int, vinculo_id: int) -> None:
    """Marca o vínculo como inativo (não apaga histórico)."""
    with pool.connection() as c:
        c.execute("update domestica_vinculo set ativo=false "
                  "where conta_id=%s and id=%s", (conta_id, vinculo_id))
        c.commit()


# ─────────────────────────────────────────────────────────────────────────
# Folha da competência (variáveis + snapshot)
# ─────────────────────────────────────────────────────────────────────────
def _competencia(d: date | None) -> date:
    d = d or date.today()
    return date(d.year, d.month, 1)


def _get_or_create_folha(conn, conta_id: int, vinculo_id: int,
                         comp: date, ano: int) -> int:
    """Garante a linha de folha (status 'prevista') pra (conta, vínculo, comp)."""
    r = conn.execute(
        "select id from domestica_folha "
        "where conta_id=%s and vinculo_id=%s and competencia=%s",
        (conta_id, vinculo_id, comp)).fetchone()
    if r:
        return r[0]
    r = conn.execute(
        """insert into domestica_folha (conta_id, vinculo_id, competencia, tabela_ano)
             values (%s,%s,%s,%s) returning id""",
        (conta_id, vinculo_id, comp, ano)).fetchone()
    return r[0]


def lancar_item(pool, conta_id: int, vinculo_id: int, tipo: str,
                valor_centavos: int, descricao: str = "",
                competencia: date | None = None, ano: int = ANO_PADRAO) -> int:
    """Lança uma variável do mês (extra/adiantamento_13/falta/desconto/beneficio)
    pro vínculo na competência. Devolve o id do item."""
    if tipo not in _MAP_TIPO_ITEM:
        raise ValueError(f"tipo inválido: {tipo}")
    comp = _competencia(competencia)
    with pool.connection() as c:
        folha_id = _get_or_create_folha(c, conta_id, vinculo_id, comp, ano)
        r = c.execute(
            """insert into domestica_folha_item
                 (conta_id, folha_id, tipo, descricao, valor_centavos)
               values (%s,%s,%s,%s,%s) returning id""",
            (conta_id, folha_id, tipo, descricao.strip(),
             int(valor_centavos))).fetchone()
        c.commit()
    return r[0]


def _lancamentos_do_vinculo(conn, conta_id: int, vinculo_id: int,
                            comp: date) -> Lancamentos:
    """Soma os itens do mês do vínculo num `Lancamentos` do motor."""
    rows = conn.execute(
        """select fi.tipo, coalesce(sum(fi.valor_centavos),0)
             from domestica_folha_item fi
             join domestica_folha f on f.id = fi.folha_id
            where f.conta_id=%s and f.vinculo_id=%s and f.competencia=%s
            group by fi.tipo""",
        (conta_id, vinculo_id, comp)).fetchall()
    kwargs = {}
    for tipo, soma in rows:
        kwargs[_MAP_TIPO_ITEM[tipo]] = int(soma or 0)
    return Lancamentos(**kwargs)


def calcular_competencia(pool, conta_id: int, competencia: date | None = None,
                         ano: int = ANO_PADRAO) -> dict:
    """Roda o motor pra TODOS os vínculos ativos na competência (sem gravar).
    É o que alimenta a DAE prevista e o preview. Devolve o dict de `calcular_folha`
    com o `vinculo_id` anexado em cada empregado."""
    comp = _competencia(competencia)
    vinc = listar_vinculos(pool, conta_id, so_ativos=True)
    itens: list[tuple[Vinculo, Lancamentos]] = []
    ids: list[int] = []
    with pool.connection() as c:
        for v in vinc:
            lanc = _lancamentos_do_vinculo(c, conta_id, v["id"], comp)
            itens.append((Vinculo(nome=v["nome"], salario_centavos=v["salario_centavos"],
                                  cpf=v["cpf"], cargo=v["cargo"],
                                  matricula=v["matricula"]), lanc))
            ids.append(v["id"])
    folha = calcular_folha(itens, ano, comp)
    for emp, vid in zip(folha["empregados"], ids):
        emp["vinculo_id"] = vid
    return folha


# ─────────────────────────────────────────────────────────────────────────
# Fechamento (snapshot + livro-caixa PESSOAL, atômico) e conferência
# ─────────────────────────────────────────────────────────────────────────
def fechar_folha(pool, conta_id: int, competencia: date | None = None,
                 ano: int = ANO_PADRAO, membro_id: int | None = None) -> dict:
    """Fecha a competência: grava o snapshot do cálculo em domestica_folha e
    lança no livro-caixa PESSOAL (líquido + adiantamento 13º + DAE) de cada
    empregado. ATÔMICO: snapshot + ledger numa única transação (ou fecha tudo,
    ou nada). Provisões NÃO viram caixa — ficam no snapshot pro DRE/raio-x."""
    comp = _competencia(competencia)
    folha = calcular_competencia(pool, conta_id, comp, ano)
    livro = LivroCaixa(pool, conta_id, membro_id)
    lancados, total_caixa = [], 0

    with pool.connection() as c:
        for emp in folha["empregados"]:
            folha_id = _get_or_create_folha(c, conta_id, emp["vinculo_id"], comp, ano)
            # 1) lançamentos de caixa (natureza pessoal) — 1 caixa por empregado,
            #    amarrado ao líquido; adiantamento e DAE entram como itens à parte.
            lanc_principal_id = None
            for item in lancamentos_livro_caixa(
                    {"competencia": comp, "empregados": [emp]}):
                lc = Lancamento(tipo=Tipo.DESPESA,
                                valor_centavos=item["valor_centavos"],
                                categoria=CAT_DOMESTICA,
                                descricao=item["descricao"],
                                origem="domestica", natureza="pessoal")
                salvo = livro.adicionar(lc, forcar=True, conn=c)
                total_caixa += item["valor_centavos"]
                if lanc_principal_id is None:
                    lanc_principal_id = salvo.id
            # 2) snapshot do cálculo na folha (status 'paga')
            c.execute(
                """update domestica_folha set
                     tabela_ano=%s, base_inss_centavos=%s, base_fgts_centavos=%s,
                     base_irrf_centavos=%s, inss_empregado_centavos=%s,
                     inss_patronal_centavos=%s, gilrat_centavos=%s,
                     fgts_mensal_centavos=%s, fgts_compensatorio_centavos=%s,
                     irrf_centavos=%s, dae_prevista_centavos=%s, liquido_centavos=%s,
                     provisoes_centavos=%s, custo_real_centavos=%s,
                     vencimento_dae=%s, status='paga', lancamento_id=%s
                   where id=%s""",
                (ano, emp["base_inss_centavos"], emp["base_fgts_centavos"],
                 emp["base_irrf_centavos"], emp["inss_empregado_centavos"],
                 emp["inss_patronal_centavos"], emp["gilrat_centavos"],
                 emp["fgts_mensal_centavos"], emp["fgts_compensatorio_centavos"],
                 emp["irrf_centavos"], emp["dae_centavos"], emp["liquido_centavos"],
                 emp["provisoes_centavos"], emp["custo_real_centavos"],
                 emp["vencimento_dae"], lanc_principal_id, folha_id))
            lancados.append({"vinculo_id": emp["vinculo_id"], "nome": emp["nome"],
                             "folha_id": folha_id})
        c.commit()   # um único commit: fecha a folha inteira ou nada

    return {"ok": True, "competencia": comp, "lancados": lancados,
            "total_caixa_centavos": total_caixa,
            "dae_total_centavos": folha["dae_total_centavos"],
            "custo_real_total_centavos": folha["custo_real_total_centavos"]}


def conferir_dae(pool, conta_id: int, vinculo_id: int,
                 dae_oficial_centavos: int, competencia: date | None = None,
                 ano: int = ANO_PADRAO) -> dict:
    """Confere a DAE oficial (valor da guia emitida no portal) contra o previsto.
    Grava `dae_conferida_centavos`, marca status 'conferida' e devolve a
    divergência em centavos (0 = bateu)."""
    comp = _competencia(competencia)
    v = obter_vinculo(pool, conta_id, vinculo_id)
    if not v:
        return {"ok": False, "erro": "vínculo não encontrado"}
    lanc = None
    with pool.connection() as c:
        lanc = _lancamentos_do_vinculo(c, conta_id, vinculo_id, comp)
    emp = calcular_empregado(
        Vinculo(nome=v["nome"], salario_centavos=v["salario_centavos"]),
        lanc, ano, comp)
    previsto = emp["dae_centavos"]
    divergencia = int(dae_oficial_centavos) - previsto
    with pool.connection() as c:
        folha_id = _get_or_create_folha(c, conta_id, vinculo_id, comp, ano)
        c.execute(
            "update domestica_folha set dae_conferida_centavos=%s, "
            "status=case when status='prevista' then 'conferida' else status end "
            "where id=%s", (int(dae_oficial_centavos), folha_id))
        c.commit()
    return {"ok": True, "previsto_centavos": previsto,
            "oficial_centavos": int(dae_oficial_centavos),
            "divergencia_centavos": divergencia, "bateu": divergencia == 0}
