"""Excluir funcionário — só pra quem NUNCA movimentou a folha.

A trava existe porque `folha_eventos` referencia o funcionário: apagar quem já teve
lançamento deixaria os eventos órfãos, e o holerite e o relatório daquele período
ficariam sem dono. Quem saiu da empresa é caso de DAR BAIXA (`demitido_em`), que
preserva tudo. Excluir é pro cadastro duplicado ou digitado errado.

A checagem mora dentro de `excluir_funcionario()`, não só na rota — é isso que este
arquivo trava, chamando a função direto.
"""
import os
from datetime import date
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from db.conexao import init_schema
from finance import empresa as emp

_MIGRACOES = ("018_chave_nfce_lancamentos.sql", "053_modulo_pj.sql",
              "057_natureza_lancamento.sql", "088_forma_pagamento_parcelas.sql",
              "089_funcionario_vale_transporte.sql", "092_funcionario_cbo.sql",
              "093_folha_beneficios_e_org.sql", "094_funcionario_demissao.sql",
              "095_funcionario_cpf.sql", "150_funcionario_salario_vigencia.sql")


@pytest.fixture(scope="module")
def pool():
    p = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=4,
                       open=True, kwargs={"prepare_threshold": None})
    init_schema(p)
    base = Path(__file__).resolve().parent.parent / "db" / "migracoes"
    for m in _MIGRACOES:
        with p.connection() as c:
            c.execute((base / m).read_text(encoding="utf-8"))
            c.commit()
    yield p
    p.close()


@pytest.fixture()
def conta_id(pool):
    with pool.connection() as c:
        cid = c.execute(
            "insert into contas (tipo, nome) values ('pj','Empresa Excluir') returning id"
        ).fetchone()[0]
        c.commit()
    return cid


def _existe(pool, func_id):
    with pool.connection() as c:
        return c.execute("select 1 from funcionarios where id=%s",
                         (func_id,)).fetchone() is not None


# ── libera quem nunca movimentou ───────────────────────────────────────────
def test_exclui_quem_nunca_teve_lancamento(pool, conta_id):
    f = emp.criar_funcionario(pool, conta_id, "Duplicado", salario_centavos=100000,
                              admitido_em=date(2026, 8, 1))
    situacao = emp.pode_excluir_funcionario(pool, conta_id, f["id"])
    assert situacao["pode"] is True
    assert situacao["lancamentos"] == 0

    r = emp.excluir_funcionario(pool, conta_id, f["id"])
    assert r["excluido"] is True
    assert not _existe(pool, f["id"])
    assert f["id"] not in [x["id"] for x in emp.listar_funcionarios(pool, conta_id)]


def test_excluir_leva_junto_as_vigencias_de_salario(pool, conta_id):
    """`on delete cascade` da migração 150 — senão sobra lixo apontando pra um
    funcionário que não existe mais."""
    f = emp.criar_funcionario(pool, conta_id, "ComVigencia", salario_centavos=100000,
                              admitido_em=date(2026, 1, 1))
    emp.definir_salario(pool, conta_id, f["id"], 120000, date(2026, 6, 1))
    emp.excluir_funcionario(pool, conta_id, f["id"])
    with pool.connection() as c:
        n = c.execute("select count(*) from funcionario_salarios where funcionario_id=%s",
                      (f["id"],)).fetchone()[0]
    assert n == 0


# ── bloqueia quem tem histórico ────────────────────────────────────────────
def test_bloqueia_quem_tem_lancamento_e_nao_apaga_nada(pool, conta_id):
    f = emp.criar_funcionario(pool, conta_id, "ComHistorico", salario_centavos=200000,
                              admitido_em=date(2026, 1, 1))
    emp.registrar_evento_folha(pool, conta_id, f["id"], "vale", 5000,
                               competencia=date(2026, 8, 1))

    situacao = emp.pode_excluir_funcionario(pool, conta_id, f["id"])
    assert situacao["pode"] is False
    assert situacao["lancamentos"] >= 1

    r = emp.excluir_funcionario(pool, conta_id, f["id"])
    assert r["excluido"] is False
    assert _existe(pool, f["id"]), "recusar tem que deixar o cadastro intacto"


def test_a_recusa_vale_chamando_a_funcao_direto(pool, conta_id):
    """A trava não pode morar só na rota: se ela estiver lá em cima, qualquer
    chamador novo do backend apaga sem checar."""
    f = emp.criar_funcionario(pool, conta_id, "Protegido", salario_centavos=200000,
                              admitido_em=date(2026, 1, 1))
    emp.registrar_evento_folha(pool, conta_id, f["id"], "extra", 1000,
                               competencia=date(2026, 8, 1))
    assert emp.excluir_funcionario(pool, conta_id, f["id"])["excluido"] is False
    assert _existe(pool, f["id"])


def test_conta_os_meses_pagos_pra_tela_explicar(pool, conta_id):
    """A tela mostra o motivo ("N lançamentos, M meses pagos"), não um 'não pode'
    seco — então as contagens precisam vir certas."""
    f = emp.criar_funcionario(pool, conta_id, "Pago", salario_centavos=200000,
                              admitido_em=date(2026, 1, 1))
    # 'pagamento' é gravado pelo pagar_folha, não pelo registrar_evento_folha (que
    # só aceita vale/beneficio/extra/desconto). Aqui o alvo é a CONTAGEM, então o
    # caminho honesto é inserir a linha como ela existe no banco.
    with pool.connection() as c:
        for mes in (6, 7, 8):
            c.execute(
                """insert into folha_eventos
                     (conta_id, funcionario_id, tipo, valor_centavos, competencia)
                   values (%s,%s,'pagamento',%s,%s)""",
                (conta_id, f["id"], 200000, date(2026, mes, 1)))
        c.commit()
    s = emp.pode_excluir_funcionario(pool, conta_id, f["id"])
    assert s["pode"] is False
    assert s["meses_pagos"] == 3
    assert s["lancamentos"] == 3


# ── isolamento entre contas ────────────────────────────────────────────────
def test_nao_exclui_funcionario_de_outra_conta(pool):
    with pool.connection() as c:
        a = c.execute("insert into contas (tipo,nome) values ('pj','Dona') returning id").fetchone()[0]
        b = c.execute("insert into contas (tipo,nome) values ('pj','Intrusa') returning id").fetchone()[0]
        c.commit()
    f = emp.criar_funcionario(pool, a, "Da Dona", salario_centavos=100000,
                              admitido_em=date(2026, 1, 1))

    situacao = emp.pode_excluir_funcionario(pool, b, f["id"])
    assert situacao["existe"] is False
    assert situacao["pode"] is False

    r = emp.excluir_funcionario(pool, b, f["id"])
    assert r["excluido"] is False
    assert _existe(pool, f["id"]), "a conta vizinha não pode apagar"


def test_funcionario_inexistente_nao_estoura(pool, conta_id):
    r = emp.excluir_funcionario(pool, conta_id, 99999999)
    assert r["excluido"] is False
    assert r["existe"] is False


# ── dar baixa continua sendo o caminho de quem saiu ────────────────────────
def test_dar_baixa_preserva_o_cadastro_e_tira_da_folha_no_mes_seguinte(pool, conta_id):
    """O caminho que o excluir bloqueado oferece como saída. Regra que já existia
    (finance/empresa.py, folha_do_mes): aparece no mês da demissão, some depois."""
    f = emp.criar_funcionario(pool, conta_id, "Saiu", salario_centavos=200000,
                              admitido_em=date(2025, 1, 1))
    emp.atualizar_funcionario(pool, conta_id, f["id"], demitido_em=date(2026, 8, 22))

    na_folha = lambda ano, mes: [i["id"] for i in
                                 emp.folha_do_mes(pool, conta_id, ano, mes)["itens"]]
    assert f["id"] in na_folha(2026, 8), "no mês da baixa ainda aparece, pra acertar"
    assert f["id"] not in na_folha(2026, 9), "no mês seguinte sai da folha"
    assert _existe(pool, f["id"]), "dar baixa NÃO apaga o cadastro"
