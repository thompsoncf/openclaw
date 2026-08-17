"""O teto de gasto tem que enxergar a retentativa.

`campanhas.teto_wa` é conferido como `sum(wa_custo)` dos alvos, e o disparo gravava
o custo com `coalesce(wa_custo, <estimativa>)`. O coalesce protegia a correção do
webhook da Meta (preço real por cima da estimativa) — mas a fila de números manda
ATÉ 3 mensagens pro mesmo alvo, uma por telefone. Da segunda em diante o coalesce
achava um valor gravado e não somava nada: a mensagem saía, era cobrada, e o teto
não via.

Visto em produção: 17 mensagens numa tarde, R$ 0,00 somados ao gasto da campanha.
Com 3 tentativas por alvo, uma campanha podia gastar ~3x o teto que promete.

Agora `wa_custo` é o ACUMULADO do alvo e `wa_custo_msg` é a parcela da última
mensagem — a única que o webhook tem direito de trocar.
"""
import os

import pytest
from psycopg_pool import ConnectionPool

from finance import campanhas_motor as cm

_BASE_SQL = """
create table campanha_alvos (id bigserial primary key, campanha_id bigint,
  wa_status text, wa_em timestamptz, wa_sid text, wa_numero text,
  wa_categoria text, wa_cobravel boolean,
  wa_custo numeric(10,4), wa_custo_msg numeric(10,4));
"""

_MKT = 0.3217   # tarifa de marketing BR, a mesma que o motor estima


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_campanha_custo_retentativa_test"
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


def _novo(pool):
    with pool.connection() as c:
        aid = c.execute("insert into campanha_alvos (campanha_id) values (1) returning id").fetchone()[0]
        c.commit()
    return aid


def _custo(pool, aid):
    with pool.connection() as c:
        r = c.execute("select wa_custo, wa_custo_msg from campanha_alvos where id=%s",
                      (aid,)).fetchone()
    return (float(r[0]) if r[0] is not None else None,
            float(r[1]) if r[1] is not None else None)


def _gasto_da_campanha(pool, camp=1):
    """Exatamente como o motor confere o teto, em _disparar_wa_campanha."""
    with pool.connection() as c:
        return float(c.execute(
            "select coalesce(sum(wa_custo),0) from campanha_alvos where campanha_id=%s",
            (camp,)).fetchone()[0])


def _corrigir_pelo_webhook(pool, sid, custo_real):
    """O mesmo UPDATE do webhook da Meta (painel_prospeccao)."""
    with pool.connection() as c:
        c.execute("""update campanha_alvos
                       set wa_categoria='marketing', wa_cobravel=true,
                           wa_custo=greatest(coalesce(wa_custo,0)
                                             - coalesce(wa_custo_msg,0) + %s, 0),
                           wa_custo_msg=%s
                     where wa_sid=%s""", (custo_real, custo_real, sid))
        c.commit()


# ------------------------------------------------------------------ acumular

def test_tres_mensagens_pro_mesmo_alvo_custam_tres(pool):
    """O caso que estourava o teto: a fila esgota os 3 números do lead."""
    aid = _novo(pool)
    for i in range(3):
        cm._wa_marca(pool, aid, "enviado", wa_sid=f"SM{aid}-{i}",
                     categoria="marketing", custo=_MKT, numero=f"869900000{i}")
    total, ultima = _custo(pool, aid)
    assert round(total, 4) == round(3 * _MKT, 4)
    assert round(ultima, 4) == _MKT


def test_o_teto_da_campanha_ve_a_retentativa(pool):
    """A régua que importa é a do motor: sum(wa_custo) por campanha."""
    a1, a2 = _novo(pool), _novo(pool)
    antes = _gasto_da_campanha(pool)
    cm._wa_marca(pool, a1, "enviado", wa_sid="SMx1", categoria="marketing", custo=_MKT)
    cm._wa_marca(pool, a1, "enviado", wa_sid="SMx2", categoria="marketing", custo=_MKT)
    cm._wa_marca(pool, a2, "enviado", wa_sid="SMx3", categoria="marketing", custo=_MKT)
    assert round(_gasto_da_campanha(pool) - antes, 4) == round(3 * _MKT, 4)


# ------------------------------------------------- o webhook troca só a parcela

def test_webhook_corrige_a_ultima_sem_derrubar_o_acumulado(pool):
    """Duas mensagens saíram; a Meta diz que a SEGUNDA foi grátis (FEP).
    O total tem que virar 1 mensagem, não zero."""
    aid = _novo(pool)
    cm._wa_marca(pool, aid, "enviado", wa_sid="SMa", categoria="marketing", custo=_MKT)
    cm._wa_marca(pool, aid, "enviado", wa_sid="SMb", categoria="marketing", custo=_MKT)
    _corrigir_pelo_webhook(pool, "SMb", 0.0)
    total, ultima = _custo(pool, aid)
    assert round(total, 4) == round(_MKT, 4)   # sobrou a 1ª, que foi cobrada
    assert ultima == 0.0


def test_webhook_repetido_nao_muda_nada(pool):
    """Status duplicado é comum; a correção precisa ser reentrante."""
    aid = _novo(pool)
    cm._wa_marca(pool, aid, "enviado", wa_sid="SMc", categoria="marketing", custo=_MKT)
    _corrigir_pelo_webhook(pool, "SMc", 0.25)
    depois_1a, _ = _custo(pool, aid)
    _corrigir_pelo_webhook(pool, "SMc", 0.25)
    depois_2a, _ = _custo(pool, aid)
    assert round(depois_1a, 4) == 0.25 and round(depois_2a, 4) == 0.25


def test_correcao_nunca_deixa_o_custo_negativo(pool):
    """Guarda do greatest(...,0): um webhook fora de ordem não pode gerar crédito."""
    aid = _novo(pool)
    with pool.connection() as c:
        c.execute("""update campanha_alvos set wa_sid='SMd', wa_custo=0.10,
                       wa_custo_msg=0.90 where id=%s""", (aid,))
        c.commit()
    _corrigir_pelo_webhook(pool, "SMd", 0.0)
    total, _ = _custo(pool, aid)
    assert total == 0.0
