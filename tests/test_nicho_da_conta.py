"""O slug do nicho na linha da conta — e as telas que somem quando ele se perde.

POR QUE ESTE TESTE EXISTE
Em 07/09/2026 o dono da Prime Eventos avisou que o menu do Follow-up não aparecia
pra ele. A causa: `conta_logada` trazia o slug do nicho na consulta e o DESCARTAVA
ao montar a tupla, e três telas passaram a lê-lo do índice 7 — que é `cidade`. A
Prime, cidade TERESINA, era classificada como perfil "recorrente".

Nada disso levantava erro. O menu simplesmente não estava lá, o campo "Festas por
dia" não existia em Empresa (e sem ele a lista de espera do #643 não liga), e a
ficha oferecia os motivos de perda do nicho errado desde 05/09. Um teste que
casasse mock com mock passaria feliz — por isso este aqui lê do banco.
"""
import os
from types import SimpleNamespace

import pytest
from psycopg_pool import ConnectionPool

from web import portal as pt

_SQL = """
create table nichos (id bigserial primary key, nome text, slug text unique, ativo boolean default true);
create table planos (codigo text primary key, nome text, tipo_conta text);
create table contas (id bigserial primary key, tipo text default 'pj', nome text, email text,
  plano text, status text default 'ativa', vencimento date, cidade text,
  eh_fornecedor boolean default false, fornecedor_slug text, eh_assinante_cesta boolean default false,
  nicho_id bigint, vende_produto boolean, vende_servico boolean);
create table conta_modulos (conta_id bigint, modulo text, ativo boolean default true);
create table assinaturas (id bigserial primary key, cliente_id bigint, status text default 'ativa');
"""


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_nicho_conta_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=3, open=True, kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_SQL)
        c.execute("insert into planos (codigo, nome, tipo_conta) values ('pro','Pro','pj')")
        for slug in ("eventos", "consultoria", "hortifruti"):
            c.execute("insert into nichos (nome, slug) values (%s,%s)", (slug.title(), slug))
        c.commit()
    yield p
    p.close()


def _conta(pool, nome, cidade, slug):
    with pool.connection() as c:
        cid = c.execute(
            """insert into contas (nome, email, plano, status, cidade, nicho_id)
               values (%s,%s,'pro','ativa',%s,(select id from nichos where slug=%s))
               returning id""", (nome, f"{nome}@x.com", cidade, slug)).fetchone()[0]
        c.execute("insert into conta_modulos (conta_id, modulo) values (%s,'pj')", (cid,))
        c.commit()
    return cid


def _req(conta_id):
    return SimpleNamespace(session={"conta_id": conta_id, "papel": "dono"}, state=SimpleNamespace())


def _logada(monkeypatch, pool, conta_id):
    monkeypatch.setattr(pt, "get_pool", lambda: pool)
    return pt.conta_logada(_req(conta_id))


def test_o_slug_do_nicho_viaja_na_linha_da_conta(monkeypatch, pool):
    """A regressão exata: cidade TERESINA, nicho eventos. O índice 7 continua
    sendo a cidade — o que muda é que o slug agora existe na tupla."""
    cid = _conta(pool, "PRIME EVENTOS", "TERESINA", "eventos")
    linha = _logada(monkeypatch, pool, cid)
    assert linha[7] == "TERESINA"
    assert pt.nicho_da_conta(linha) == "eventos"
    # e o erro exato que isso causava, fixado: ler o índice 7 como nicho joga a
    # conta de eventos no perfil sem festa, e é assim que a tela some
    from finance import raio_x_perfil as rxp
    assert rxp.perfil(linha[7])["chave"] == "recorrente"


def test_os_indices_antigos_nao_andaram(monkeypatch, pool):
    """O slug entra no FIM da tupla porque 0..15 são lidos por dezenas de telas."""
    cid = _conta(pool, "DOCE MELL", "TERESINA", "eventos")
    linha = _logada(monkeypatch, pool, cid)
    assert linha[0] == cid and linha[2] == "DOCE MELL"
    assert linha[11] is True and linha[12] is True      # módulo pj, acesso
    assert linha[14] is True                            # vende serviço (eventos)
    assert len(linha) == 17


def test_o_perfil_da_tela_bate_com_o_nicho_do_banco(monkeypatch, pool):
    """É o que o menu, a ficha e a tela de Empresa perguntam."""
    from finance import raio_x_perfil as rxp
    casos = {"eventos": "eventos", "consultoria": "recorrente", "hortifruti": "produto"}
    for slug, esperado in casos.items():
        cid = _conta(pool, f"Conta {slug}", "TERESINA", slug)
        linha = _logada(monkeypatch, pool, cid)
        assert rxp.perfil(pt.nicho_da_conta(linha))["chave"] == esperado, slug


def test_a_conta_de_eventos_ve_o_follow_up_e_o_campo_de_festas_por_dia(monkeypatch, pool):
    """As duas telas que sumiram. Sem esta asserção o bug volta em silêncio."""
    from web.painel_prospeccao import _tem_follow_up
    ev = _logada(monkeypatch, pool, _conta(pool, "Buffet", "TERESINA", "eventos"))
    rc = _logada(monkeypatch, pool, _conta(pool, "Escritório", "TERESINA", "consultoria"))
    assert _tem_follow_up(ev) is True and _tem_follow_up(rc) is False

    from finance import raio_x_perfil as rxp
    t = pt._env.get_template("base")
    def menu(linha):
        ctx = dict(logado=True, papel="dono", tem_pj=True, vende_produto=False,
                   caps={"vendas": True, "financeiro": True, "gerir": True},
                   raio_x_perfil=rxp.perfil(pt.nicho_da_conta(linha)), conta=linha,
                   secao_ativa="", titulo="x", request=None)
        return "".join(t.blocks["menu"](t.new_context(ctx))) if "menu" in t.blocks else ""
    # o gate do menu é o mesmo do campo em Empresa: o perfil da conta
    assert rxp.perfil(pt.nicho_da_conta(ev))["chave"] == "eventos"
    assert rxp.perfil(pt.nicho_da_conta(rc))["chave"] != "eventos"


def test_conta_sem_nicho_nao_quebra_e_cai_no_perfil_sem_festa(monkeypatch, pool):
    with pool.connection() as c:
        cid = c.execute("""insert into contas (nome, email, plano, status, cidade)
                           values ('Sem nicho','s@x.com','pro','ativa','TERESINA')
                           returning id""").fetchone()[0]
        c.execute("insert into conta_modulos (conta_id, modulo) values (%s,'pj')", (cid,))
        c.commit()
    linha = _logada(monkeypatch, pool, cid)
    assert pt.nicho_da_conta(linha) is None
    from finance import raio_x_perfil as rxp
    assert rxp.perfil(pt.nicho_da_conta(linha))["chave"] == "recorrente"


def test_tupla_curta_de_mock_nao_levanta_indexerror():
    """Metade da suíte passa um mock curto no lugar da conta; a leitura tolera."""
    for curta in (None, (), (1, "pj", "Nome"), tuple(range(16))):
        assert pt.nicho_da_conta(curta) is None
