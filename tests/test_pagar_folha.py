"""Regressão do fix #4: pagar_folha atômico (ledger + folha_eventos numa transação).

Cenário do bug (antes do fix): `pagar_folha` fazia, por funcionário, um insert no
ledger (conexão/commit próprios do LivroCaixa) e DEPOIS um insert em folha_eventos
(outra conexão/commit). Se falhasse no meio, a folha ficava MEIO PAGA — parte no
caixa, parte não.

O fix roda tudo numa conexão/transação só (LivroCaixa.adicionar aceita conn=c) e
faz UM commit no fim. Estes testes travam a garantia:
  1. happy path: 2 funcionários → 2 lançamentos 'folha' + 2 eventos 'pagamento';
  2. atomicidade: falha no 2º funcionário → rollback TOTAL (0 e 0).

Roda isolado: a fixture aplica schema + migrações no TEST_DATABASE_URL (a trava do
conftest garante que nunca é o banco de produção).
"""
import os
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from db.conexao import init_schema
from finance import empresa as emp
from finance.livro_caixa import LivroCaixa

# Só as migrações que este teste precisa. 018 = coluna chave em lancamentos;
# 053 = titulos/funcionarios/folha_eventos; 057 = coluna natureza em lancamentos.
_MIGRACOES = ["018_chave_nfce_lancamentos.sql", "053_modulo_pj.sql",
              "057_natureza_lancamento.sql", "092_funcionario_cbo.sql",
              "093_folha_beneficios_e_org.sql", "094_funcionario_demissao.sql",
              "095_funcionario_cpf.sql"]


@pytest.fixture(scope="module")
def pool():
    url = os.environ["TEST_DATABASE_URL"]  # conftest já garante que existe e não é prod
    p = ConnectionPool(url, min_size=2, max_size=8, open=True)
    init_schema(p)
    mig_dir = Path(__file__).resolve().parents[1] / "db" / "migracoes"
    with p.connection() as c:
        for nome in _MIGRACOES:
            c.execute((mig_dir / nome).read_text(encoding="utf-8"))
        c.commit()
    yield p
    p.close()


@pytest.fixture()
def conta_id(pool):
    """Conta PJ limpa; devolve o id e limpa o que ela deixar (multi-tenant safe)."""
    with pool.connection() as c:
        cid = c.execute(
            "insert into contas (tipo, nome, status) values ('pj','TESTE-FOLHA','trial') returning id"
        ).fetchone()[0]
        c.commit()
    yield cid
    with pool.connection() as c:
        c.execute("delete from folha_eventos where conta_id=%s", (cid,))
        c.execute("delete from funcionarios where conta_id=%s", (cid,))
        c.execute("delete from lancamentos where conta_id=%s", (cid,))
        c.execute("delete from contas where id=%s", (cid,))
        c.commit()


def _n_lancamentos_folha(pool, conta_id: int) -> int:
    with pool.connection() as c:
        return c.execute(
            "select count(*) from lancamentos where conta_id=%s and origem='folha'",
            (conta_id,),
        ).fetchone()[0]


def _n_eventos_pagamento(pool, conta_id: int, ano: int, mes: int) -> int:
    from datetime import date
    with pool.connection() as c:
        return c.execute(
            """select count(*) from folha_eventos
                where conta_id=%s and competencia=%s and tipo='pagamento'""",
            (conta_id, date(ano, mes, 1)),
        ).fetchone()[0]


def test_pagar_folha_happy_path(pool, conta_id):
    """2 funcionários com salário > 0 → paga tudo: 2 lançamentos 'folha', 2 eventos
    'pagamento', total = soma dos LÍQUIDOS (salário − INSS), e o saldo do caixa
    cai pelo total."""
    from datetime import date
    emp.criar_funcionario(pool, conta_id, "Alice", salario_centavos=200000)
    emp.criar_funcionario(pool, conta_id, "Bruno", salario_centavos=300000)
    hoje = date.today()

    # líquido = salário − INSS (sem VT aqui, ninguém optou). Calculado pela própria
    # função pra o teste seguir correto se a tabela mudar.
    total = ((200000 - emp.inss_desconto_centavos(200000))
             + (300000 - emp.inss_desconto_centavos(300000)))

    saldo_antes = LivroCaixa(pool, conta_id).saldo_centavos()
    r = emp.pagar_folha(pool, conta_id, hoje.year, hoje.month)

    assert r["ok"] is True
    assert r["total_centavos"] == total, r
    assert len(r["pagos"]) == 2
    assert _n_lancamentos_folha(pool, conta_id) == 2
    assert _n_eventos_pagamento(pool, conta_id, hoje.year, hoje.month) == 2

    saldo_depois = LivroCaixa(pool, conta_id).saldo_centavos()
    assert saldo_antes - saldo_depois == total, (saldo_antes, saldo_depois)


def test_pagar_folha_atomico_rollback(pool, conta_id, monkeypatch):
    """Falha no meio (2º funcionário) → rollback TOTAL: NADA gravado (0 lançamentos
    'folha' e 0 eventos 'pagamento'). Sem o fix, o 1º teria sido pago."""
    from datetime import date
    emp.criar_funcionario(pool, conta_id, "Alice", salario_centavos=200000)
    emp.criar_funcionario(pool, conta_id, "Bruno", salario_centavos=300000)
    hoje = date.today()

    # Faz o 2º insert no ledger explodir ANTES de gravar — simula falha no meio.
    orig = LivroCaixa.adicionar
    chamadas = {"n": 0}

    def boom(self, lanc, *a, **kw):
        chamadas["n"] += 1
        if chamadas["n"] == 2:
            raise RuntimeError("falha simulada no 2o funcionario")
        return orig(self, lanc, *a, **kw)

    monkeypatch.setattr(LivroCaixa, "adicionar", boom)

    with pytest.raises(RuntimeError):
        emp.pagar_folha(pool, conta_id, hoje.year, hoje.month)

    # a transação inteira caiu: nem o 1º funcionário ficou gravado
    assert _n_lancamentos_folha(pool, conta_id) == 0, "ledger nao fez rollback — folha meio paga!"
    assert _n_eventos_pagamento(pool, conta_id, hoje.year, hoje.month) == 0, "folha_eventos nao fez rollback!"
