"""Os números da tela de Origens: o que entrou, por código, e o que virou.

A tela é lida por gente de fora da empresa (a agência de tráfego), e é isso que
sobe o custo de cada número errado aqui: quem confere não tem como abrir o banco
pra checar. Então o que estes testes fixam é, em ordem:

1. **Duas faixas, não três.** "sem código" mistura orgânico com quem apagou o
   texto do anúncio, e o banco não sabe separar — inventar a distinção seria o
   jeito mais rápido de perder a confiança de quem audita.
2. **A cobertura anda junto da taxa.** Compromisso que já passou e ninguém
   respondeu não conta como falta; sem dizer quantos são, a taxa parece firme.
3. **Nada de dinheiro de anúncio.** Investimento, CPL, CAC e ROAS não saem daqui.
"""
import os
from datetime import date, datetime, timedelta, timezone

import pytest
from psycopg_pool import ConnectionPool

from finance import origens

CONTA = 7
HOJE = date.today()
INI, FIM = HOJE - timedelta(days=6), HOJE

_SQL = """
create table prospeccao (id bigserial primary key, conta_id bigint, empresa text,
  contato text, whatsapp text, origem text, origem_codigo text, orcamento_id bigint,
  status text default 'novo', estagio text default 'lead',
  criado_em timestamptz default now());
create table conversas (id bigserial primary key, conta_id bigint, prospeccao_id bigint,
  canal text default 'whatsapp', contato_ref text, criado_em timestamptz default now());
create table mensagens (id bigserial primary key, conversa_id bigint, canal text,
  direcao text, autor text, texto text, criado_em timestamptz default now());
create table eventos_agenda (id bigserial primary key, conta_id bigint, membro_id bigint,
  titulo text, inicio timestamptz, fim timestamptz, tipo text, desfecho text,
  prospeccao_id bigint, tipo_evento text, criado_em timestamptz default now());
create table orcamentos (id bigserial primary key, conta_id bigint,
  primeiro_ano_centavos bigint default 0, sinal_pago_em timestamptz,
  status text, criado_em timestamptz default now());
"""


@pytest.fixture()
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_origens_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute(f"drop database if exists {dbname} with (force)")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=3, open=True,
                       kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_SQL)
        c.commit()
    yield p
    p.close()


def _agora(minutos_atras=0):
    return datetime.now(timezone.utc) - timedelta(minutes=minutos_atras)


def _lead(c, codigo=None, *, quando=None, respondida_em_min=None,
          marcou=False, desfecho=None, no_passado=True, sinal=None, valor=0,
          tipo_evento=None):
    """Um lead com conversa, e o que aconteceu com ele. Devolve o id do lead."""
    orc = None
    if sinal is not None or valor:
        orc = c.execute(
            "insert into orcamentos (conta_id, primeiro_ano_centavos, sinal_pago_em) "
            "values (%s,%s,%s) returning id", (CONTA, valor, sinal)).fetchone()[0]
    lid = c.execute(
        "insert into prospeccao (conta_id, empresa, origem_codigo, orcamento_id) "
        "values (%s,'Cliente',%s,%s) returning id", (CONTA, codigo, orc)).fetchone()[0]
    cid = c.execute(
        "insert into conversas (conta_id, prospeccao_id, criado_em) "
        "values (%s,%s,%s) returning id",
        (CONTA, lid, quando or _agora(60))).fetchone()[0]
    entrada = quando or _agora(60)
    c.execute("insert into mensagens (conversa_id, canal, direcao, criado_em) "
              "values (%s,'whatsapp','in',%s)", (cid, entrada))
    if respondida_em_min is not None:
        c.execute("insert into mensagens (conversa_id, canal, direcao, criado_em) "
                  "values (%s,'whatsapp','out',%s)",
                  (cid, entrada + timedelta(minutes=respondida_em_min)))
    if marcou:
        inicio = _agora(120) if no_passado else _agora(-60 * 24)
        c.execute("""insert into eventos_agenda (conta_id, titulo, inicio, prospeccao_id,
                       desfecho, tipo_evento) values (%s,'Visita — Cliente',%s,%s,%s,%s)""",
                  (CONTA, inicio, lid, desfecho, tipo_evento))
    return lid


def _dados(pool):
    return origens.dados_origens(pool, CONTA, INI, FIM)


# ------------------------------------------------------------------ duas faixas

def test_separa_quem_tem_codigo_de_quem_nao_tem(pool):
    with pool.connection() as c:
        _lead(c, "A3")
        _lead(c, "A3")
        _lead(c, "V2")
        _lead(c, None)
        _lead(c, None)
        c.commit()
    d = _dados(pool)
    assert d["resumo"] == {"com_codigo": 3, "sem_codigo": 2, "total": 5}
    assert [l["codigo"] for l in d["linhas"]] == ["A3", "V2"]
    assert d["sem_codigo"]["conversas"] == 2


def test_codigo_so_de_espacos_conta_como_sem_codigo(pool):
    """Um lead com `origem_codigo=' '` é lead sem origem — senão a tela ganharia
    uma linha fantasma com nome vazio."""
    with pool.connection() as c:
        _lead(c, "   ")
        c.commit()
    d = _dados(pool)
    assert d["resumo"]["com_codigo"] == 0
    assert d["resumo"]["sem_codigo"] == 1


def test_periodo_vazio_devolve_a_mesma_forma(pool):
    """Tela que muda de formato quando não houve nada quebra na segunda de manhã."""
    d = _dados(pool)
    assert d["resumo"] == {"com_codigo": 0, "sem_codigo": 0, "total": 0}
    assert d["linhas"] == [] and d["sem_codigo"] is None
    assert [f["chave"] for f in d["funil"]] == \
        ["conversas", "atendidas", "marcaram", "compareceram", "vendas"]


def test_conversa_fora_do_periodo_nao_entra(pool):
    with pool.connection() as c:
        _lead(c, "A3", quando=_agora(60 * 24 * 30))
        _lead(c, "A3")
        c.commit()
    assert _dados(pool)["resumo"]["com_codigo"] == 1


# ------------------------------------------------------------------ atendimento

def test_atendida_e_ter_resposta_da_casa(pool):
    with pool.connection() as c:
        _lead(c, "A3", respondida_em_min=10)
        _lead(c, "A3")            # ninguém respondeu
        c.commit()
    d = _dados(pool)
    assert d["atendimento"]["atendidas"] == 1
    assert d["atendimento"]["sem_resposta"] == 1


def test_mediana_do_conjunto_nao_e_mediana_de_medianas(pool):
    """Com uma faixa grande e uma pequena, mediana de medianas dá o número errado.
    Aqui: nove respostas de 10 min num código e uma de 600 no outro — a mediana do
    conjunto é 10, não a média das duas medianas."""
    with pool.connection() as c:
        for _ in range(9):
            _lead(c, "A3", respondida_em_min=10)
        _lead(c, "V2", respondida_em_min=600)
        c.commit()
    assert _dados(pool)["atendimento"]["mediana_min"] == 10


def test_conta_quantas_foram_atendidas_em_ate_uma_hora(pool):
    with pool.connection() as c:
        _lead(c, "A3", respondida_em_min=5)
        _lead(c, "A3", respondida_em_min=59)
        _lead(c, "A3", respondida_em_min=61)
        c.commit()
    assert _dados(pool)["atendimento"]["ate_1h"] == 2


# ----------------------------------------------------------------- o compromisso

def test_marcar_compromisso_nao_conta_a_festa_do_cliente(pool):
    """`tipo_evento` preenchido é a FESTA do cliente, não o compromisso dele com a
    casa. Contar as duas coisas juntas inflaria a taxa de oportunidade."""
    with pool.connection() as c:
        _lead(c, "A3", marcou=True)                          # visita
        _lead(c, "A3", marcou=True, tipo_evento="Casamento")  # a festa
        c.commit()
    assert _dados(pool)["funil"][2]["n"] == 1


def test_compareceu_sai_do_desfecho(pool):
    with pool.connection() as c:
        _lead(c, "A3", marcou=True, desfecho="realizado")
        _lead(c, "A3", marcou=True, desfecho="nao_realizado")
        c.commit()
    d = _dados(pool)
    assert d["funil"][2]["n"] == 2 and d["funil"][3]["n"] == 1


def test_compromisso_passado_sem_resposta_entra_na_cobertura(pool):
    """Ninguém respondeu não é 'não apareceu'. Sem a cobertura, a taxa de
    comparecimento sai de uma amostra menor do que parece."""
    with pool.connection() as c:
        _lead(c, "A3", marcou=True, desfecho="realizado")
        _lead(c, "A3", marcou=True, desfecho=None)   # já passou, sem resposta
        c.commit()
    d = _dados(pool)
    assert d["cobertura"]["sem_desfecho"] == 1
    assert d["funil"][3]["taxa"]["cobertura"] < 1.0


def test_compromisso_no_futuro_nao_e_falta_de_resposta(pool):
    with pool.connection() as c:
        _lead(c, "A3", marcou=True, desfecho=None, no_passado=False)
        c.commit()
    assert _dados(pool)["cobertura"]["sem_desfecho"] == 0


# ----------------------------------------------------------------------- a venda

def test_venda_e_orcamento_com_sinal_pago(pool):
    with pool.connection() as c:
        _lead(c, "A3", sinal=_agora(30), valor=750000)
        _lead(c, "A3", sinal=None, valor=900000)   # orçamento sem sinal: não é venda
        c.commit()
    d = _dados(pool)
    assert d["funil"][4]["n"] == 1
    assert d["faturamento_centavos"] == 750000


def test_faturamento_soma_so_o_que_tem_sinal(pool):
    with pool.connection() as c:
        _lead(c, "A3", sinal=_agora(30), valor=750000)
        _lead(c, "V2", sinal=_agora(30), valor=800000)
        c.commit()
    d = _dados(pool)
    assert d["faturamento_centavos"] == 1550000
    por_codigo = {l["codigo"]: l["faturamento_centavos"] for l in d["linhas"]}
    assert por_codigo == {"A3": 750000, "V2": 800000}


def test_venda_de_lead_sem_codigo_nao_entra_no_total_de_anuncio(pool):
    with pool.connection() as c:
        _lead(c, None, sinal=_agora(30), valor=900000)
        c.commit()
    d = _dados(pool)
    assert d["funil"][4]["n"] == 0
    assert d["faturamento_centavos"] == 0


# --------------------------------------------------------------- a fronteira

def test_nao_devolve_nada_de_dinheiro_de_anuncio(pool):
    """Investimento, CPL, CAC, ROAS e ROI são medida da agência. Se um dia
    aparecerem aqui, é porque alguém cruzou a fronteira sem perceber."""
    d = _dados(pool)
    plano = repr(d).lower()
    for proibido in ("investimento", "cpl", "cac", "roas", "roi", "impress"):
        assert proibido not in plano, f"{proibido} não é medida do Zaq"


def test_nao_devolve_texto_de_conversa(pool):
    """A agência vê números e tempos, nunca o que o cliente escreveu."""
    with pool.connection() as c:
        cid = _lead(c, "A3", respondida_em_min=5)
        c.execute("update mensagens set texto='quero valores pra 150 pessoas' "
                  "where conversa_id in (select id from conversas where prospeccao_id=%s)",
                  (cid,))
        c.commit()
    assert "150 pessoas" not in repr(_dados(pool))


def test_vocabulario_do_compromisso_vem_de_fora(pool):
    """Regra 6: 'visita' é palavra de quem vende festa. Quem vende serviço marca
    reunião, e o rótulo tem que acompanhar."""
    d = origens.dados_origens(pool, CONTA, INI, FIM, compromisso="reunião")
    assert d["funil"][2]["rotulo"] == "Marcaram reunião"


def test_outra_conta_nao_vaza(pool):
    with pool.connection() as c:
        _lead(c, "A3")
        c.execute("update prospeccao set conta_id=999")
        c.execute("update conversas set conta_id=999")
        c.commit()
    assert _dados(pool)["resumo"]["total"] == 0
