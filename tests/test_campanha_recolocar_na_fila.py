"""Botão "Colocar na fila": devolve alvos parados pro disparo, sem furar o teto.

A tentação era zerar `wa_tentativas` — o alvo voltaria "novinho". Só que ele já
recebeu uma mensagem: zerando, um prospect que nunca respondeu poderia levar 4 no
total, quando a regra promete 3. Preservando o contador, "3 tentativas por lead"
vale de verdade e o botão nunca vira uma porta dos fundos pro teto.

`wa_tentados` também nunca é limpo: o número que já falhou não volta.
"""
import asyncio
import os

import pytest
from psycopg_pool import ConnectionPool

from finance import campanhas_motor as cm
from web import painel_prospeccao as pp

_BASE_SQL = """
create table contas (id bigserial primary key, tipo text, nome text, chip_de bigint);
create table prospeccao (id bigserial primary key, conta_id bigint, empresa text,
  whatsapp text, telefone text, decisor_telefones jsonb);
create table campanhas (id bigserial primary key, conta_id bigint, nome text,
  status text default 'ativa', responsavel_id bigint);
create table campanha_alvos (id bigserial primary key, campanha_id bigint, prospeccao_id bigint,
  status text default 'fila', wa_status text, wa_em timestamptz,
  wa_erro_codigo text, wa_erro_msg text, wa_sid text, wa_numero text, alvo_telefone text,
  wa_tentados jsonb not null default '[]'::jsonb, wa_tentativas int not null default 0);
"""


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_recolocar_fila_test"
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


class _FakeRequest:
    def __init__(self):
        self.session = {}


def _cenario(pool, nome, tentativas):
    """Uma conta com uma campanha e um alvo parado com `tentativas` já gastas."""
    with pool.connection() as c:
        conta = c.execute("insert into contas (tipo, nome) values ('pj',%s) returning id",
                          (nome,)).fetchone()[0]
        camp = c.execute("insert into campanhas (conta_id, nome) values (%s,'C') returning id",
                         (conta,)).fetchone()[0]
        pid = c.execute("""insert into prospeccao (conta_id, empresa) values (%s,%s)
                            returning id""", (conta, nome)).fetchone()[0]
        aid = c.execute(
            """insert into campanha_alvos (campanha_id, prospeccao_id, wa_status,
                                           wa_erro_codigo, wa_erro_msg, wa_tentados, wa_tentativas)
               values (%s,%s,'erro','63024','sem whatsapp','["86999990000"]'::jsonb,%s)
               returning id""", (camp, pid, tentativas)).fetchone()[0]
        c.commit()
    return conta, camp, aid


def _alvo(pool, aid):
    with pool.connection() as c:
        return c.execute("""select wa_status, wa_tentativas, wa_tentados, wa_erro_codigo
                              from campanha_alvos where id=%s""", (aid,)).fetchone()


def _chamar(pool, monkeypatch, conta, camp, aids):
    monkeypatch.setattr(pp, "get_pool", lambda: pool)
    monkeypatch.setattr(pp, "_acesso", lambda req: ({"conta_id": conta, "gerencia": True}, None))
    monkeypatch.setattr(pp, "_pode_campanha", lambda ctx, cid: True)
    monkeypatch.setattr(pp, "_campanha_dona", lambda c, conta_id, cid: True)
    return pp.prospeccao_campanha_recolocar_na_fila(
        _FakeRequest(), camp, ids=[str(a) for a in aids])


def test_volta_pra_fila_preservando_o_contador(pool, monkeypatch):
    conta, camp, aid = _cenario(pool, "Uma tentativa", tentativas=1)
    resp = _chamar(pool, monkeypatch, conta, camp, [aid])
    assert resp.status_code == 200
    status, tentativas, tentados, cod = _alvo(pool, aid)
    assert status is None, "volta pra fila (a fila é wa_status is null)"
    assert tentativas == 1, "o contador NÃO zera — senão o teto de 3 vira 4"
    assert tentados == ["86999990000"], "o número que falhou não volta nunca"
    assert cod is None, "o erro antigo sai da tela"


def test_alvo_no_teto_nao_volta(pool, monkeypatch):
    """O botão não pode ser porta dos fundos pro teto."""
    conta, camp, aid = _cenario(pool, "No teto", tentativas=cm._WA_TENTATIVAS)
    _chamar(pool, monkeypatch, conta, camp, [aid])
    status, tentativas, _t, _c = _alvo(pool, aid)
    assert status == "erro" and tentativas == cm._WA_TENTATIVAS


def test_destrava_o_numero_escolhido_pelo_dono(pool, monkeypatch):
    """Se o número que ELE travou falhou e ele está apertando este botão, é isso
    que ele está pedindo: tente os outros. Sem destravar, 16 leads em produção
    ficavam com 87 telefones guardados e um botão que não fazia nada."""
    conta, camp, aid = _cenario(pool, "Travado", tentativas=1)
    with pool.connection() as c:
        c.execute("update campanha_alvos set alvo_telefone='(86) 97777-7777' where id=%s", (aid,))
        c.commit()
    _chamar(pool, monkeypatch, conta, camp, [aid])
    with pool.connection() as c:
        status, travado, tentados = c.execute(
            """select wa_status, alvo_telefone, wa_tentados from campanha_alvos where id=%s""",
            (aid,)).fetchone()
    assert status is None and travado is None
    assert tentados == ["86999990000"], "o que já falhou continua riscado"


def test_recolocar_limpa_o_sid_da_tentativa_encerrada(pool, monkeypatch):
    """Senão um callback atrasado do SID antigo marcaria 'enviado' e tiraria da
    fila quem acabou de voltar."""
    conta, camp, aid = _cenario(pool, "Sid antigo", tentativas=1)
    with pool.connection() as c:
        c.execute("update campanha_alvos set wa_sid='SMvelho', wa_numero='(86) 9x' where id=%s",
                  (aid,))
        c.commit()
    _chamar(pool, monkeypatch, conta, camp, [aid])
    with pool.connection() as c:
        assert c.execute("select wa_sid, wa_numero from campanha_alvos where id=%s",
                         (aid,)).fetchone() == (None, None)


@pytest.mark.parametrize("status_email", ["respondeu", "descadastrou", "erro"])
def test_nao_recoloca_quem_nao_pode_receber(pool, monkeypatch, status_email):
    """O botão não pode ser um atalho pra mandar frio pra quem já respondeu ou
    pediu pra sair — `descadastrou` é LGPD."""
    conta, camp, aid = _cenario(pool, f"Fora {status_email}", tentativas=1)
    with pool.connection() as c:
        c.execute("update campanha_alvos set status=%s where id=%s", (status_email, aid))
        c.commit()
    _chamar(pool, monkeypatch, conta, camp, [aid])
    assert _alvo(pool, aid)[0] == "erro", "não pode voltar pra fila"


def test_recoloca_quem_terminou_a_regua_de_email(pool, monkeypatch):
    """'concluido' é elegível: a régua de e-mail ter acabado não diz nada sobre o
    WhatsApp, e o lead nunca recebeu uma mensagem que chegou."""
    conta, camp, aid = _cenario(pool, "Regua concluida", tentativas=1)
    with pool.connection() as c:
        c.execute("update campanha_alvos set status='concluido' where id=%s", (aid,))
        c.commit()
    _chamar(pool, monkeypatch, conta, camp, [aid])
    assert _alvo(pool, aid)[0] is None


def test_so_mexe_em_quem_esta_parado(pool, monkeypatch):
    conta, camp, aid = _cenario(pool, "Enviado", tentativas=1)
    with pool.connection() as c:
        c.execute("update campanha_alvos set wa_status='enviado' where id=%s", (aid,))
        c.commit()
    _chamar(pool, monkeypatch, conta, camp, [aid])
    assert _alvo(pool, aid)[0] == "enviado"


def test_sem_ids_e_400(pool, monkeypatch):
    conta, camp, _aid = _cenario(pool, "Sem ids", tentativas=1)
    assert _chamar(pool, monkeypatch, conta, camp, []).status_code == 400


def test_campanha_de_outra_conta_e_403(pool, monkeypatch):
    conta, camp, aid = _cenario(pool, "Escopo", tentativas=1)
    monkeypatch.setattr(pp, "get_pool", lambda: pool)
    monkeypatch.setattr(pp, "_acesso", lambda req: ({"conta_id": conta, "gerencia": True}, None))
    monkeypatch.setattr(pp, "_pode_campanha", lambda ctx, cid: True)
    monkeypatch.setattr(pp, "_campanha_dona", lambda c, conta_id, cid: False)   # dona é outra
    resp = pp.prospeccao_campanha_recolocar_na_fila(_FakeRequest(), camp, ids=[str(aid)])
    assert resp.status_code == 403
    assert _alvo(pool, aid)[0] == "erro"
