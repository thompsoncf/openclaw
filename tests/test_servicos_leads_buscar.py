"""A busca de "Cliente" no orçamento (aba Serviços) tem que achar quem já
existe na base, não só quem passou pelo funil.

Relatado em produção em 30/09/2026: a Crislane (conta 34, Prime Eventos)
tinha CPF certo, cadastrada em Clientes — e não aparecia na busca do card
Cliente do orçamento. Causa: `/painel/servicos/leads/buscar` só olhava
`prospeccao` (o funil de leads); quem virou cliente direto (aba Clientes/
Fornecedores, sem passar pelo funil) nunca tem linha lá.

Depois disso ficou MAIS fácil de acontecer: a aba Clientes/Fornecedores
abriu pro vendedor (29/09/2026) — ele agora cadastra cliente direto, sem
gerar lead nenhum.

Roda com banco de TESTE separado (ver tests/conftest.py):
    export TEST_DATABASE_URL="postgresql://.../banco_de_teste"
    pytest
"""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from psycopg_pool import ConnectionPool

from db.conexao import init_schema
from web import painel_servicos as ps

_MIGRACOES = ("045_orcamentos.sql",
              "064_clientes_lojista.sql",
              "066_pessoas_identidade.sql",
              "131_pessoa_cnpj.sql",
              "182_clientes_papel.sql",
              "075_modulo_prospeccao.sql",
              "078_prospeccao_site.sql")

#: conta_logada em índices: [0]=id, [12]=acesso_pj, [14]=vende_servico
#: (comentário de web/portal.py:_conta_servico e _render)
def _conta(conta_id: int):
    row = [None] * 17
    row[0] = conta_id
    row[1] = "pj"
    row[12] = True   # acesso_pj
    row[14] = True   # vende_servico
    return tuple(row)


class _Sessao(dict):
    def get(self, k, default=None):
        return super().get(k, default)


def _req(papel="dono", membro_id=None):
    return SimpleNamespace(session=_Sessao(papel=papel, membro_id=membro_id))


@pytest.fixture(scope="module")
def pool():
    url = os.environ["TEST_DATABASE_URL"]  # garantido pela trava do conftest
    p = ConnectionPool(url, min_size=1, max_size=4, open=True,
                       kwargs={"prepare_threshold": None})
    init_schema(p)  # idempotente
    base = Path(__file__).resolve().parent.parent / "db" / "migracoes"
    for m in _MIGRACOES:
        with p.connection() as c:
            c.execute((base / m).read_text(encoding="utf-8"))
            c.commit()
    yield p
    p.close()


@pytest.fixture(autouse=True)
def _pool_do_modulo(pool, monkeypatch):
    monkeypatch.setattr(ps, "get_pool", lambda: pool)


@pytest.fixture()
def conta_id(pool):
    with pool.connection() as c:
        cid = c.execute(
            "insert into contas (tipo, nome) values ('pj', 'Teste Servicos Buscar') "
            "returning id"
        ).fetchone()[0]
        c.commit()
    return cid


def _cria_cliente_direto(pool, conta_id, nome, cpf):
    with pool.connection() as c:
        pid = c.execute(
            "insert into pessoas (nome, cpf, tipo) values (%s, %s, 'pf') returning id",
            (nome, cpf)).fetchone()[0]
        cid = c.execute(
            """insert into clientes (dono_id, nome, pessoa_id, eh_cliente, ativo)
                    values (%s, %s, %s, true, true) returning id""",
            (conta_id, nome, pid)).fetchone()[0]
        c.commit()
    return cid


def _cria_lead(pool, conta_id, empresa, contato="", vendedor_id=None):
    with pool.connection() as c:
        lid = c.execute(
            """insert into prospeccao (conta_id, empresa, contato, vendedor_id)
                    values (%s, %s, %s, %s) returning id""",
            (conta_id, empresa, contato, vendedor_id)).fetchone()[0]
        c.commit()
    return lid


def _buscar(request, q):
    resp = ps.painel_servicos_leads_buscar(request, q=q)
    import json
    return json.loads(resp.body)


def test_cliente_cadastrado_direto_sem_lead_aparece_na_busca(pool, monkeypatch, conta_id):
    """O caso exato da Crislane: CPF certo, nunca passou pelo funil."""
    monkeypatch.setattr(ps, "conta_logada", lambda req: _conta(conta_id))
    _cria_cliente_direto(pool, conta_id, "Crislane K", "70022233344")
    d = _buscar(_req("dono"), "Crislane")
    assert any(it["empresa"] == "Crislane K" for it in d["itens"])


def test_o_item_de_cliente_nao_vira_lead_id(pool, monkeypatch, conta_id):
    """`id: None` — o JS faz `LEAD_ID=l.id||null`; um cliente sem lead não pode
    fingir ser um lead (o card do funil que o gatilho de 'orçamento enviado'
    tentaria mover simplesmente não existe)."""
    monkeypatch.setattr(ps, "conta_logada", lambda req: _conta(conta_id))
    _cria_cliente_direto(pool, conta_id, "Fulana De Tal", "70022233345")
    d = _buscar(_req("dono"), "Fulana")
    item = next(it for it in d["itens"] if it["empresa"] == "Fulana De Tal")
    assert item["id"] is None


def test_lead_de_verdade_continua_com_o_id(pool, monkeypatch, conta_id):
    monkeypatch.setattr(ps, "conta_logada", lambda req: _conta(conta_id))
    lid = _cria_lead(pool, conta_id, "Buffet Bom Sabor", "Joana")
    d = _buscar(_req("dono"), "Buffet Bom Sabor")
    item = next(it for it in d["itens"] if it["empresa"] == "Buffet Bom Sabor")
    assert item["id"] == lid


def test_vendedor_so_ve_os_proprios_leads_mas_ve_todos_os_clientes(pool, monkeypatch, conta_id):
    """Regra que já existia pro lado leads (vendedor só busca o dele) — o lado
    cliente não tem responsavel_id nessa busca, mesma régua da aba Clientes/
    Fornecedores (liberada pro vendedor em 29/09/2026, sem recorte por dono)."""
    monkeypatch.setattr(ps, "conta_logada", lambda req: _conta(conta_id))
    with pool.connection() as c:
        eu = c.execute("insert into membros (conta_id, nome, papel) values (%s,'Eu','membro') "
                       "returning id", (conta_id,)).fetchone()[0]
        outro = c.execute("insert into membros (conta_id, nome, papel) values (%s,'Outro','membro') "
                          "returning id", (conta_id,)).fetchone()[0]
        c.commit()
    _cria_lead(pool, conta_id, "Lead Do Outro Vendedor", vendedor_id=outro)
    _cria_cliente_direto(pool, conta_id, "Cliente Da Casa Toda", "70022233346")

    d = _buscar(_req("vendedor", membro_id=eu), "Lead Do Outro Vendedor")
    assert d["itens"] == []

    d2 = _buscar(_req("vendedor", membro_id=eu), "Cliente Da Casa Toda")
    assert any(it["empresa"] == "Cliente Da Casa Toda" for it in d2["itens"])


def test_mesmo_documento_nos_dois_lados_nao_duplica_na_lista(pool, monkeypatch, conta_id):
    """Não é o caso da Crislane (ela só tinha o lado cliente), mas se algum dia
    o mesmo CPF existir nos dois lados, a lista não deve mostrar a pessoa duas
    vezes — prioridade pro lead (tem contexto de funil que o cliente não tem)."""
    monkeypatch.setattr(ps, "conta_logada", lambda req: _conta(conta_id))
    _cria_lead(pool, conta_id, "Duplicada Nos Dois", "Duplicada", vendedor_id=None)
    with pool.connection() as c:
        c.execute("update prospeccao set cnpj=%s where empresa='Duplicada Nos Dois'",
                  ("70022233347",))
        c.commit()
    _cria_cliente_direto(pool, conta_id, "Duplicada Nos Dois", "70022233347")

    d = _buscar(_req("dono"), "Duplicada Nos Dois")
    achados = [it for it in d["itens"] if it["empresa"] == "Duplicada Nos Dois"]
    assert len(achados) == 1
    assert achados[0]["id"] is not None   # ficou o lead, não o cliente
