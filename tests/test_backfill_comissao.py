"""Backfill da comissão: a simulação não pode gravar, e o --aplicar tem que acertar.

O script devolve ao vendedor os lançamentos que a baixa antiga creditou errado
(quem clicou em "pago" em vez de quem originou o título).

O teste mais importante aqui é o da SIMULAÇÃO: rodar sem `--aplicar` tem que
deixar o banco byte a byte como estava. Um backfill que grava sem pedir é a
maneira mais fácil de estragar dado financeiro em produção.
"""
import os
import sys
from datetime import date

import pytest
from psycopg_pool import ConnectionPool

_BASE_SQL = """
create table contas (id bigserial primary key, nome text, chip_de bigint);
create table membros (id bigserial primary key, conta_id bigint, nome text, email text,
  papel text default 'vendedor', ativo boolean default true, comissao_pct numeric(5,2));
create table lancamentos (id bigserial primary key, conta_id bigint, membro_id bigint,
  tipo text not null, valor_centavos bigint not null, categoria text not null default '',
  descricao text not null default '', data date not null, origem text default 'manual',
  natureza text default 'empresa', criado_em timestamptz default now());
create table titulos (id bigserial primary key, conta_id bigint, tipo text not null,
  descricao text not null, contraparte text not null default '',
  valor_centavos int not null, vencimento date not null, status text default 'aberto',
  recorrente boolean default false, periodicidade text, valor_variavel boolean not null default false, acrescimo_centavos int not null default 0, lancamento_acrescimo_id bigint, categoria text default '', lancamento_id bigint,
  pago_em date, criado_por bigint, criado_em timestamptz default now(),
  orcamento_id bigint, parcela_idx int);   -- 162: liga o título à parcela do orçamento
"""

HOJE = date.today()


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_backfill_comissao_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=3, open=True, kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_BASE_SQL)
        c.commit()
    yield p
    p.close()


@pytest.fixture
def cen(pool, monkeypatch):
    """Quatro lançamentos: um órfão, um trocado, um já certo e um de balcão."""
    with pool.connection() as c:
        c.execute("truncate contas, membros, lancamentos, titulos restart identity")
        conta = c.execute("insert into contas (nome) values ('Vega') returning id").fetchone()[0]
        ana = c.execute("insert into membros (conta_id, nome, comissao_pct) "
                        "values (%s,'Ana',10) returning id", (conta,)).fetchone()[0]
        dono = c.execute("insert into membros (conta_id, nome, papel) "
                         "values (%s,'Dono','dono') returning id", (conta,)).fetchone()[0]

        def par(membro_lanc, criado_por, valor, desc):
            lid = c.execute(
                """insert into lancamentos (conta_id, membro_id, tipo, valor_centavos,
                     categoria, descricao, data, origem)
                   values (%s,%s,'receita',%s,'vendas',%s,%s,'titulo') returning id""",
                (conta, membro_lanc, valor, desc, HOJE)).fetchone()[0]
            c.execute(
                """insert into titulos (conta_id, tipo, descricao, valor_centavos,
                     vencimento, status, lancamento_id, criado_por)
                   values (%s,'receber',%s,%s,%s,'pago',%s,%s)""",
                (conta, desc, valor, HOJE, lid, criado_por))
            return lid

        orfao = par(None, ana, 100000, "Setup — Cliente A")       # ninguém recebia
        trocado = par(dono, ana, 50000, "Setup — Cliente B")      # dono levava a comissão
        certo = par(ana, ana, 30000, "Setup — Cliente C")         # já correto
        # venda de balcão antiga: sem título, sem como saber quem vendeu
        balcao = c.execute(
            """insert into lancamentos (conta_id, membro_id, tipo, valor_centavos,
                 categoria, descricao, data, origem)
               values (%s,null,'receita',70000,'vendas','Venda balcão',%s,'pdv')
               returning id""", (conta, HOJE)).fetchone()[0]
        c.commit()

    import db.conexao as conexao
    monkeypatch.setattr(conexao, "get_pool", lambda *a, **k: pool)
    return {"conta": conta, "ana": ana, "dono": dono,
            "orfao": orfao, "trocado": trocado, "certo": certo, "balcao": balcao}


def _donos(pool, cen):
    with pool.connection() as c:
        return dict(c.execute("select id, membro_id from lancamentos order by id").fetchall())


def _rodar(monkeypatch, capsys, *args):
    from scripts import backfill_comissao_vendedor as bf
    monkeypatch.setattr(sys, "argv", ["backfill", *args])
    bf.main()
    return capsys.readouterr().out


# ------------------------------------------------------------------ simulação
def test_simulacao_nao_grava_nada(pool, cen, monkeypatch, capsys):
    antes = _donos(pool, cen)
    saida = _rodar(monkeypatch, capsys, )
    assert _donos(pool, cen) == antes, "a simulação mexeu no banco"
    assert "SIMULAÇÃO" in saida
    assert "Nada foi gravado" in saida


def test_simulacao_lista_os_dois_casos_separados(pool, cen, monkeypatch, capsys):
    saida = _rodar(monkeypatch, capsys)
    assert "2 lançamento(s) pra reatribuir: 1 sem dono, 1 com dono errado" in saida
    assert "SEM DONO" in saida and "DONO ERRADO" in saida
    # o que muda de pessoa aparece com o nome de quem perde
    assert "Dono → Ana" in saida


def test_simulacao_mostra_o_impacto_por_pessoa(pool, cen, monkeypatch, capsys):
    saida = _rodar(monkeypatch, capsys)
    assert "Ana: +R$ 1.500,00" in saida        # 1000 do órfão + 500 do trocado
    assert "Dono: -R$ 500,00" in saida


def test_simulacao_avisa_o_que_nao_da_pra_recuperar(pool, cen, monkeypatch, capsys):
    """Venda de balcão antiga não tem título: a informação não existe."""
    saida = _rodar(monkeypatch, capsys)
    assert "AVISO" in saida and "R$ 700,00" in saida


# ------------------------------------------------------------------ aplicar
def test_aplicar_corrige_orfao_e_trocado(pool, cen, monkeypatch, capsys):
    _rodar(monkeypatch, capsys, "--aplicar")
    donos = _donos(pool, cen)
    assert donos[cen["orfao"]] == cen["ana"]
    assert donos[cen["trocado"]] == cen["ana"]


def test_aplicar_nao_toca_no_que_ja_estava_certo_nem_no_balcao(pool, cen, monkeypatch, capsys):
    _rodar(monkeypatch, capsys, "--aplicar")
    donos = _donos(pool, cen)
    assert donos[cen["certo"]] == cen["ana"]
    assert donos[cen["balcao"]] is None, "balcão não tem título; não dá pra inventar dono"


def test_rodar_duas_vezes_nao_muda_mais_nada(pool, cen, monkeypatch, capsys):
    _rodar(monkeypatch, capsys, "--aplicar")
    depois = _donos(pool, cen)
    saida = _rodar(monkeypatch, capsys, "--aplicar")
    assert _donos(pool, cen) == depois
    assert "Nada a reatribuir" in saida


def test_titulo_sem_origem_fica_como_esta(pool, cen, monkeypatch, capsys):
    """Sem `criado_por` não há o que aprender — o script não chuta um dono."""
    with pool.connection() as c:
        lid = c.execute(
            """insert into lancamentos (conta_id, membro_id, tipo, valor_centavos,
                 categoria, descricao, data, origem)
               values (%s,null,'receita',9900,'vendas','Avulso',%s,'titulo') returning id""",
            (cen["conta"], HOJE)).fetchone()[0]
        c.execute("""insert into titulos (conta_id, tipo, descricao, valor_centavos,
                     vencimento, status, lancamento_id) values (%s,'receber','Avulso',9900,%s,'pago',%s)""",
                  (cen["conta"], HOJE, lid))
        c.commit()
    _rodar(monkeypatch, capsys, "--aplicar")
    assert _donos(pool, cen)[lid] is None


def test_filtro_por_conta_nao_encosta_nas_outras(pool, cen, monkeypatch, capsys):
    with pool.connection() as c:
        outra = c.execute("insert into contas (nome) values ('Rival') returning id").fetchone()[0]
        vend2 = c.execute("insert into membros (conta_id, nome) values (%s,'Zé') returning id",
                          (outra,)).fetchone()[0]
        lid = c.execute(
            """insert into lancamentos (conta_id, membro_id, tipo, valor_centavos,
                 categoria, descricao, data, origem)
               values (%s,null,'receita',12345,'vendas','X',%s,'titulo') returning id""",
            (outra, HOJE)).fetchone()[0]
        c.execute("""insert into titulos (conta_id, tipo, descricao, valor_centavos,
                     vencimento, status, lancamento_id, criado_por)
                     values (%s,'receber','X',12345,%s,'pago',%s,%s)""",
                  (outra, HOJE, lid, vend2))
        c.commit()
    _rodar(monkeypatch, capsys, "--conta", str(cen["conta"]), "--aplicar")
    assert _donos(pool, cen)[lid] is None, "mexeu em conta fora do escopo pedido"
