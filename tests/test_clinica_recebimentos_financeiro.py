"""O Receber da clínica lança de verdade no Financeiro (finance/clinica_recebimentos).

Usa o banco do livro-caixa de tests/test_comprovante_quita_conta.py (lancamentos, titulos,
clientes): é o mesmo caminho do balcão (LivroCaixa, origem 'balcao') e do título a
receber. A tabela dos recebimentos entra aqui sem as chaves estrangeiras da clínica.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from finance import clinica_agenda as ca
from finance import clinica_recebimentos as crb
from tests.test_comprovante_quita_conta import CONTA, _limpa, pool  # noqa: F401

AGORA = datetime(2026, 10, 2, 15, tzinfo=timezone.utc)
EV = {"id": 1, "lead": None, "paciente": "Maria Clara Souza", "fone": "", "categoria": "consulta"}


@pytest.fixture()
def recebimentos(pool):  # noqa: F811
    with pool.connection() as c:
        c.execute("""create table if not exists clinica_recebimentos (id bigserial primary key, conta_id bigint,
                       evento_id bigint unique, prospeccao_id bigint, valor_centavos bigint, forma text,
                       lancamento_id bigint, titulo_id bigint, criado_por bigint,
                       criado_em timestamptz default now())""")
        c.execute("delete from clinica_recebimentos")
        c.commit()
    return pool


def test_a_receita_entra_na_transacao_de_quem_chama(pool):  # noqa: F811
    with pool.connection() as c:
        lanc_id = crb._lancar_receita(pool, c, CONTA, EV, 50000, "pix", None, None)
        c.rollback()                                          # quem chama desistiu: nada fica
        assert c.execute("select count(*) from lancamentos where id=%s", (lanc_id,)).fetchone()[0] == 0
        lanc_id = crb._lancar_receita(pool, c, CONTA, EV, 50000, "pix", None, None)
        c.commit()
        r = c.execute("""select tipo, valor_centavos, pagamento, origem, descricao, categoria from lancamentos
                          where id=%s and conta_id=%s""", (lanc_id, CONTA)).fetchone()
    assert r[0] == "receita" and r[1] == 50000 and r[2] == "pix" and r[3] == "balcao"
    assert r[4] == "Atendimento · Maria (consulta)" and r[5] == "Vendas"      # nunca o procedimento


def test_categoria_fora_do_catalogo_nao_vai_pro_financeiro():
    assert crb._descricao(dict(EV, categoria="botox labial")) == "Atendimento · Maria (atendimento)"
    assert crb._descricao(dict(EV, categoria="sessao")) == "Atendimento · Maria (sessão de pacote)"


def test_fica_a_receber_vira_titulo_em_30_dias(recebimentos):
    assert crb._fica_a_receber(recebimentos, CONTA, EV, 50000, None, None, AGORA) is None
    assert crb._fica_a_receber(recebimentos, CONTA, EV, 50000, None, None, AGORA) == "Esse atendimento já foi recebido."
    with recebimentos.connection() as c:
        tid = c.execute("select titulo_id from clinica_recebimentos where evento_id=1").fetchone()[0]
        r = c.execute("""select tipo, valor_centavos, vencimento, status, descricao from titulos
                          where id=%s and conta_id=%s""", (tid, CONTA)).fetchone()
    assert r[:4] == ("receber", 50000, ca.hoje_br(AGORA) + timedelta(days=30), "aberto")
    assert r[4] == "Atendimento · Maria (consulta) — fica a receber"
