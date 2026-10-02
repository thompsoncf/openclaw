"""O Receber da clínica lança de verdade no Financeiro (finance/clinica_recebimentos._lancar).

Usa o banco do livro-caixa de tests/test_comprovante_quita_conta.py (lancamentos, titulos,
clientes): é o mesmo caminho do balcão (LivroCaixa, origem 'balcao') e do título a
receber. A ficha do paciente é trocada por None (ela é da clínica, testada lá).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from finance import clinica_agenda as ca
from finance import clinica_pacientes as cpa
from finance import clinica_recebimentos as crb
from tests.test_comprovante_quita_conta import CONTA, _limpa, pool  # noqa: F401

AGORA = datetime(2026, 10, 2, 15, tzinfo=timezone.utc)
EV = {"id": 1, "lead": None, "paciente": "Maria Clara Souza", "fone": "", "categoria": "consulta"}


def test_pix_vira_receita_no_livro_caixa(pool, monkeypatch):
    monkeypatch.setattr(cpa, "ficha_do_paciente", lambda *a, **k: None)
    lanc_id, titulo_id = crb._lancar(pool, CONTA, EV, 50000, "pix", None, AGORA)
    assert titulo_id is None and lanc_id
    with pool.connection() as c:
        r = c.execute("""select tipo, valor_centavos, pagamento, origem, descricao, categoria from lancamentos
                          where id=%s and conta_id=%s""", (lanc_id, CONTA)).fetchone()
    assert r[0] == "receita" and r[1] == 50000 and r[2] == "pix" and r[3] == "balcao"
    assert r[4] == "Atendimento · Maria (consulta)" and r[5] == "Vendas"      # nunca o procedimento


def test_fica_a_receber_vira_titulo_em_30_dias(pool, monkeypatch):
    monkeypatch.setattr(cpa, "ficha_do_paciente", lambda *a, **k: None)
    lanc_id, titulo_id = crb._lancar(pool, CONTA, EV, 50000, "fiado", None, AGORA)
    assert lanc_id is None and titulo_id
    with pool.connection() as c:
        r = c.execute("""select tipo, valor_centavos, vencimento, status, descricao from titulos
                          where id=%s and conta_id=%s""", (titulo_id, CONTA)).fetchone()
    assert r[:4] == ("receber", 50000, ca.hoje_br(AGORA) + timedelta(days=30), "aberto")
    assert r[4] == "Atendimento · Maria (consulta) — fica a receber"
