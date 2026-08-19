"""A tela da Régua: salvar a config e ligar UM gatilho de cada vez.

O que estes testes protegem:
  * o prazo é regulável em minutos/horas/dias e vira uma unidade só no banco;
  * "sem prazo" existe de verdade (campo vazio não vira zero, que cobraria sempre);
  * ligar o gatilho sem escolher evento não liga nada — senão a etapa ficaria
    "ativa" apontando pro vazio, e o motor rodaria em falso;
  * etapa de resultado (ganho/perdido) não aceita prazo: "está em Perdido há 30
    dias" não é cobrança, é o fim da história;
  * vendedor não configura a régua da empresa.
"""
import asyncio
import os
from types import SimpleNamespace

import pytest
from psycopg_pool import ConnectionPool

from web import painel_prospeccao as pp

CONTA = 11

_SQL = """
create table prospeccao (id bigserial primary key, conta_id bigint, status text default 'novo',
  estagio text default 'lead');
create table funil_etapas (id bigserial primary key, conta_id bigint, chave text, rotulo text,
  ordem int default 0, fixa boolean default false, fase text default 'venda',
  prazo_min integer, gatilho text, gatilho_ativo boolean default false,
  criado_em timestamptz default now(), constraint uq_fe unique (conta_id, chave));
create table funil_regua (conta_id bigint primary key,
  gatilhos_modo text default 'off', cobranca_modo text default 'off',
  janela_dias text default '1,2,3,4,5,6', janela_abre time default '08:00',
  janela_fecha time default '19:00', sem_resposta_min int default 120,
  bola_nossa_min int default 240, bola_cliente_min int default 4320,
  escala_min int default 240, teto_avisos_dia int default 5,
  atualizado_em timestamptz default now());
create table funil_movimentos (id bigserial primary key, conta_id bigint, prospeccao_id bigint,
  de text, para text, motivo text, membro_id bigint, criado_em timestamptz default now());
"""


@pytest.fixture()
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_regua_tela_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=3, open=True, kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_SQL)
        for chave, rot, ordem, fixa, fase in [("novo", "Novo", 0, True, "venda"),
                                              ("contatado", "Contatado", 10, False, "venda"),
                                              ("ganho", "Sinal Pago", 900, True, "fechamento"),
                                              ("perdido", "Perdido", 910, True, "fechamento")]:
            c.execute("""insert into funil_etapas (conta_id, chave, rotulo, ordem, fixa, fase)
                         values (%s,%s,%s,%s,%s,%s)""", (CONTA, chave, rot, ordem, fixa, fase))
        c.commit()
    yield p
    p.close()


class _Form(dict):
    def getlist(self, k):
        v = self.get(k)
        return v if isinstance(v, list) else ([v] if v is not None else [])


class _Req:
    def __init__(self, campos):
        self._f = _Form(campos)
        self.session = {}

    async def form(self):
        return self._f


def _logado(monkeypatch, pool, gerencia=True):
    monkeypatch.setattr(pp, "get_pool", lambda: pool)
    monkeypatch.setattr(pp, "_acesso", lambda req: (
        {"conta_id": CONTA, "membro_id": 1, "gerencia": gerencia, "pode_atribuir": gerencia,
         "conta": None, "papel": "dono" if gerencia else "vendedor"}, None))


def _eid(pool, chave):
    with pool.connection() as c:
        return c.execute("select id from funil_etapas where conta_id=%s and chave=%s",
                         (CONTA, chave)).fetchone()[0]


def _etapa(pool, chave):
    with pool.connection() as c:
        return c.execute("""select prazo_min, gatilho, gatilho_ativo, rotulo
                              from funil_etapas where conta_id=%s and chave=%s""",
                         (CONTA, chave)).fetchone()


# ----------------------------------------------------------------- unidades

def test_prazo_vai_e_volta_na_maior_unidade_que_serve():
    assert pp._par_min("4", "h") == 240
    assert pp._min_par(240) == (4, "h")
    assert pp._min_par(4320) == (3, "d")
    assert pp._min_par(90) == (90, "min")     # 1h30 não é hora inteira nem dia


def test_campo_vazio_e_sem_prazo_nao_e_zero():
    """Zero significaria 'vence na hora' — a etapa cobraria todo lead o tempo todo."""
    assert pp._par_min("", "h") is None
    assert pp._par_min("0", "h") is None
    assert pp._par_min("-3", "d") is None
    assert pp._min_par(None) == ("", "h")


# ----------------------------------------------------------------- config

def test_salvar_config_grava_modos_janela_e_prazos(monkeypatch, pool):
    _logado(monkeypatch, pool)
    req = _Req({"gatilhos_modo": "observando", "cobranca_modo": "off",
                "dias": ["1", "2", "3", "4", "5", "6"], "abre": "07:30", "fecha": "20:00",
                "sem_resposta_n": "90", "sem_resposta_u": "min",
                "bola_nossa_n": "4", "bola_nossa_u": "h",
                "bola_cliente_n": "5", "bola_cliente_u": "d",
                "escala_n": "6", "escala_u": "h", "teto": "3"})
    asyncio.run(pp.regua_config(req))
    with pool.connection() as c:
        r = c.execute("""select gatilhos_modo, cobranca_modo, janela_dias, sem_resposta_min,
                                bola_nossa_min, bola_cliente_min, escala_min, teto_avisos_dia
                           from funil_regua where conta_id=%s""", (CONTA,)).fetchone()
    assert r == ("observando", "off", "1,2,3,4,5,6", 90, 240, 7200, 360, 3)


def test_modo_invalido_cai_pra_desligado(monkeypatch, pool):
    """Ninguém liga a régua por acidente de digitação num POST."""
    _logado(monkeypatch, pool)
    asyncio.run(pp.regua_config(_Req({"gatilhos_modo": "LIGADO!!", "cobranca_modo": "sim"})))
    with pool.connection() as c:
        r = c.execute("select gatilhos_modo, cobranca_modo from funil_regua where conta_id=%s",
                      (CONTA,)).fetchone()
    assert r == ("off", "off")


def test_teto_nunca_fica_zero(monkeypatch, pool):
    _logado(monkeypatch, pool)
    asyncio.run(pp.regua_config(_Req({"teto": "0"})))
    with pool.connection() as c:
        assert c.execute("select teto_avisos_dia from funil_regua where conta_id=%s",
                         (CONTA,)).fetchone()[0] == 5   # coalesce mantém o que havia


# ----------------------------------------------------------------- etapa

def test_liga_um_gatilho_de_cada_vez(monkeypatch, pool):
    _logado(monkeypatch, pool)
    r = asyncio.run(pp.regua_etapa(_Req({"rotulo": "Contatado", "gatilho": "resposta_nossa",
                                         "gatilho_ativo": "1", "prazo_n": "5", "prazo_u": "d"}),
                                   _eid(pool, "contatado")))
    assert r.status_code == 200
    assert _etapa(pool, "contatado") == (7200, "resposta_nossa", True, "Contatado")
    # e a etapa vizinha continua intocada — é uma linha por vez, não um salvar-tudo
    assert _etapa(pool, "novo")[2] is False


def test_marcar_ativo_sem_escolher_evento_nao_liga_nada(monkeypatch, pool):
    """Etapa 'ativa' apontando pro vazio faria o motor rodar em falso todo ciclo."""
    _logado(monkeypatch, pool)
    asyncio.run(pp.regua_etapa(_Req({"gatilho": "", "gatilho_ativo": "1"}), _eid(pool, "contatado")))
    prazo, gat, ativo, _rot = _etapa(pool, "contatado")
    assert (gat, ativo) == (None, False)


def test_evento_desconhecido_e_recusado(monkeypatch, pool):
    _logado(monkeypatch, pool)
    asyncio.run(pp.regua_etapa(_Req({"gatilho": "lua_cheia", "gatilho_ativo": "1"}),
                               _eid(pool, "contatado")))
    assert _etapa(pool, "contatado")[1] is None


def test_etapa_de_resultado_nao_aceita_prazo(monkeypatch, pool):
    _logado(monkeypatch, pool)
    asyncio.run(pp.regua_etapa(_Req({"prazo_n": "3", "prazo_u": "d"}), _eid(pool, "perdido")))
    assert _etapa(pool, "perdido")[0] is None


def test_rotulo_vazio_nao_apaga_o_nome_da_etapa(monkeypatch, pool):
    _logado(monkeypatch, pool)
    asyncio.run(pp.regua_etapa(_Req({"rotulo": "  ", "gatilho": "sinal_pago"}), _eid(pool, "ganho")))
    assert _etapa(pool, "ganho")[3] == "Sinal Pago"


def test_vendedor_nao_configura_a_regua_da_empresa(monkeypatch, pool):
    _logado(monkeypatch, pool, gerencia=False)
    r = asyncio.run(pp.regua_etapa(_Req({"gatilho": "sinal_pago", "gatilho_ativo": "1"}),
                                   _eid(pool, "ganho")))
    assert r.status_code == 403
    assert _etapa(pool, "ganho")[2] is False
