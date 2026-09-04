"""O card lê a conversa (migração 198 + finance/evento_leitor.py).

POR QUE. Prime Eventos, 04/09/2026: o cliente escreve "casamento, data 13 de
fevereiro" e "média de 70 pessoas", e o card diz "sem data · perguntar". Dos 246
leads sem data, 121 já tinham dito a data na conversa. Decisões do dono: o que o
leitor achou já vale, com o selo; sem IA por agora; quem muda o que já está lá é
o vendedor (a conversa vira pista).
"""
import os
from datetime import date, datetime, timezone

import pytest
from psycopg_pool import ConnectionPool

from finance import evento_leitor as L

HOJE = date(2026, 9, 4)


# ------------------------------------------------------------------ ler_texto
@pytest.mark.parametrize("frase, esperado", [
    ("seria para casamento, data 13 de fevereiro",
     {"data": date(2027, 2, 13), "tipo": "Casamento"}),
    ("Média de 70 pessoas", {"convidados": 70}),
    ("14/11/2026 15 anos 120 convidados",
     {"data": date(2026, 11, 14), "tipo": "15 anos", "convidados": 120}),
    ("31 de outubro Inicio às 20:00 Aniversário de 40 anos 80 pessoas",
     {"data": date(2026, 10, 31), "tipo": "Aniversário", "convidados": 80}),   # 80, não 40
    ("o mini casamento que pretendo realizar no dia 22/05/2027",
     {"data": date(2027, 5, 22), "tipo": "Casamento"}),
    ("bom dia | 19/05/2028 | festa de 15 anos | 21:00horas | 150 pessoas",
     {"data": date(2028, 5, 19), "tipo": "15 anos", "convidados": 150}),
    ("disponibilidade para o dia 17 ou 18 de dezembro 2026? Confraternização do escritório",
     {"data": date(2026, 12, 18), "tipo": "Confraternização", "alternativa": "17 ou 18 de dezembro 2026"}),
    ("é uma cerimônia do jaleco. a data seria para até mais ou menos março",
     {"mes_solto": "março", "tipo": "Formatura"}),
    ("Oi! Vi o anúncio e quero mais informações sobre o local.", {}),
    ("🎤 Áudio (0:12) Oi, tudo bem", {}),
])
def test_o_que_o_leitor_entende(frase, esperado):
    r = L.ler_texto(frase, HOJE)
    for k, v in esperado.items():
        assert r.get(k) == v, (k, r)
    for k in ("data", "tipo", "convidados", "mes_solto"):
        if k not in esperado:
            assert k not in r, (k, r)


def test_ano_sem_dizer_e_o_proximo_em_que_a_data_nao_passou():
    assert L.ler_texto("dia 13 de fevereiro", HOJE)["data"] == date(2027, 2, 13)
    assert L.ler_texto("dia 31 de outubro", HOJE)["data"] == date(2026, 10, 31)
    assert L.ler_texto("dia 04/09", HOJE)["data"] == date(2026, 9, 4)           # hoje ainda vale
    assert L.ler_texto("dia 03/09", HOJE)["data"] == date(2027, 9, 3)           # ontem: ano que vem
    assert L.ler_texto("foi em 14/11/2025", HOJE) .get("data") is None          # passado explícito
    assert L.ler_texto("dia 14/11/2031", HOJE).get("data") is None              # longe demais
    assert L.ler_texto("dia 22/05/27", HOJE)["data"] == date(2027, 5, 22)       # ano curto


def test_horario_e_numeros_soltos_nao_viram_data_nem_convidados():
    r = L.ler_texto("começa 20:00 e vai até 2:30, uns 40 anos de casados", HOJE)
    assert "data" not in r and "convidados" not in r
    assert L.ler_texto("31/02/2027", HOJE).get("data") is None                  # dia que não existe


def test_a_ultima_data_da_mensagem_vale():
    r = L.ler_texto("mudei de 10/01 pra 17/01/2027", HOJE)
    assert r["data"] == date(2027, 1, 17)


def test_o_trecho_e_a_prova_em_volta_do_que_foi_lido():
    r = L.ler_texto("Boa tarde!! Gostaria de saber o valor da locação, como funciona.. "
                    "seria para casamento, data 13 de fevereiro", HOJE)
    assert "casamento, data 13 de fevereiro" in r["trecho"] and r["trecho"].startswith("…")


def test_ler_mensagens_junta_as_falas_e_a_mais_recente_vale():
    msgs = [("seria para casamento, data 13 de fevereiro", 1),
            ("Média de 70 pessoas", 2),
            ("na verdade vai ser dia 20 de fevereiro", 3)]
    r = L.ler_mensagens(msgs, HOJE)
    assert r["data"] == date(2027, 2, 20) and r["quando"] == 3
    assert r["tipo"] == "Casamento" and r["convidados"] == 70
    assert "13 de fevereiro" in r["trecho"] and "70 pessoas" in r["trecho"]
    assert L.ler_mensagens([], HOJE) == {}


def test_mes_solto_some_quando_outra_mensagem_traz_a_data():
    r = L.ler_mensagens([("mais ou menos março", 1), ("fechamos 14/03/2027", 2)], HOJE)
    assert r["data"] == date(2027, 3, 14) and "mes_solto" not in r


# ------------------------------------------------------------------ banco
_SQL = """
create table prospeccao (id bigserial primary key, conta_id bigint, empresa text,
  estagio text default 'lead', status text default 'contatado',
  evento_em date, evento_tipo text, evento_convidados int,
  evento_origem text, evento_trecho text, evento_pista text, evento_lido_em timestamptz,
  atualizado_em timestamptz default now());
create table conversas (id bigserial primary key, conta_id bigint, prospeccao_id bigint, canal text);
create table mensagens (id bigserial primary key, conversa_id bigint, direcao text, texto text,
  criado_em timestamptz default now());
"""
CONTA = 34


@pytest.fixture()
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True,
                           kwargs={"autocommit": True, "prepare_threshold": None})
    dbname = "zaq_evento_leitor_test"
    with admin.connection() as c:
        c.execute("select pg_terminate_backend(pid) from pg_stat_activity "
                  "where datname=%s and pid <> pg_backend_pid()", (dbname,))
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=2, open=True, kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_SQL)
        c.commit()
    yield p
    p.close()


def _lead(pool, **kw):
    cols = ["conta_id", "empresa"] + list(kw)
    vals = [CONTA, "Larissa"] + list(kw.values())
    with pool.connection() as c:
        lid = c.execute(f"insert into prospeccao ({', '.join(cols)}) values ({', '.join(['%s'] * len(vals))}) "
                        "returning id", vals).fetchone()[0]
        c.execute("insert into conversas (conta_id, prospeccao_id, canal) values (%s,%s,'whatsapp')", (CONTA, lid))
        c.commit()
    return lid


def _msg(pool, lid, texto, direcao="in", ha_min=0):
    with pool.connection() as c:
        c.execute("""insert into mensagens (conversa_id, direcao, texto, criado_em)
                     select id, %s, %s, now() - %s * interval '1 minute' from conversas where prospeccao_id=%s""",
                  (direcao, texto, ha_min, lid))
        c.commit()


def _le(pool, lid):
    with pool.connection() as c:
        return c.execute("select evento_em, evento_tipo, evento_convidados, evento_origem, evento_trecho, "
                         "evento_pista, evento_lido_em from prospeccao where id=%s", (lid,)).fetchone()


def test_ler_conversa_preenche_o_card_com_origem_e_trecho(pool):
    lid = _lead(pool)
    _msg(pool, lid, "seria para casamento, data 13 de fevereiro", ha_min=5)
    _msg(pool, lid, "Média de 70 pessoas", ha_min=4)
    _msg(pool, lid, "Boa tarde! Que legal, vou te passar os pacotes", direcao="out", ha_min=3)
    agora = datetime(2026, 9, 4, 20, 0, tzinfo=timezone.utc)
    r = L.ler_conversa(pool, CONTA, lid, agora)
    assert r == {"preencheu": ["convidados", "data", "tipo"], "pista": None}
    em, tipo, conv, origem, trecho, pista, lido = _le(pool, lid)
    assert (em, tipo, conv, origem, pista) == (date(2027, 2, 13), "Casamento", 70, "conversa", None)
    assert "13 de fevereiro" in trecho and "70 pessoas" in trecho
    assert lido == agora


def test_ler_conversa_so_preenche_o_vazio_e_a_data_diferente_vira_pista(pool):
    lid = _lead(pool, evento_em=date(2027, 2, 13), evento_tipo="Casamento", evento_origem="mao")
    _msg(pool, lid, "na verdade vai ser dia 20 de fevereiro, uns 90 convidados")
    r = L.ler_conversa(pool, CONTA, lid)
    assert r["preencheu"] == ["convidados"] and r["pista"] == "falou de 20 fev 27"
    em, tipo, conv, origem, trecho, pista, _ = _le(pool, lid)
    assert em == date(2027, 2, 13) and tipo == "Casamento" and conv == 90     # data não mudou
    assert origem == "conversa" and pista == "falou de 20 fev 27"


def test_so_o_mes_vira_pista_e_nao_inventa_dia(pool):
    lid = _lead(pool)
    _msg(pool, lid, "é uma cerimônia do jaleco, a data seria mais ou menos março")
    r = L.ler_conversa(pool, CONTA, lid)
    assert r == {"preencheu": ["tipo"], "pista": "falou de março"}
    em, tipo, conv, origem, _, pista, _ = _le(pool, lid)
    assert em is None and tipo == "Formatura" and origem == "conversa" and pista == "falou de março"


def test_dezessete_ou_dezoito_grava_o_ultimo_e_deixa_a_duvida_na_pista(pool):
    lid = _lead(pool)
    _msg(pool, lid, "tem disponibilidade para o dia 17 ou 18 de dezembro 2026?")
    r = L.ler_conversa(pool, CONTA, lid)
    assert r["pista"] == "disse 17 ou 18 de dezembro 2026"
    assert _le(pool, lid)[0] == date(2026, 12, 18)


def test_sem_nada_na_conversa_nao_toca_no_card_mas_marca_a_leitura(pool):
    lid = _lead(pool)
    _msg(pool, lid, "Oi! Vi o anúncio e quero mais informações")
    assert L.ler_conversa(pool, CONTA, lid) == {"preencheu": [], "pista": None}
    em, tipo, conv, origem, trecho, pista, lido = _le(pool, lid)
    assert (em, tipo, conv, origem, trecho, pista) == (None, None, None, None, None, None)
    assert lido is not None


def test_so_le_o_que_o_cliente_disse_nao_o_vendedor(pool):
    lid = _lead(pool)
    _msg(pool, lid, "temos data em 14/11/2026, quer?", direcao="out")
    L.ler_conversa(pool, CONTA, lid)
    assert _le(pool, lid)[0] is None


def test_leads_por_ler_e_o_acervo_e_o_botao_le_todos(pool):
    a = _lead(pool); _msg(pool, a, "14/11/2026 15 anos 120 convidados")
    b = _lead(pool); _msg(pool, b, "sem nada")
    c_ = _lead(pool, evento_em=date(2027, 1, 1), evento_tipo="Casamento", evento_convidados=10)
    _msg(pool, c_, "13 de fevereiro")                         # já está completo: não entra
    perdido = _lead(pool, status="perdido"); _msg(pool, perdido, "13 de fevereiro")
    with pool.connection() as c:
        assert L.leads_por_ler(c, CONTA) == [a, b]
    tot = L.ler_acervo(pool, CONTA, [a, b])
    assert tot["lidos"] == 2 and tot["data"] == 1 and tot["tipo"] == 1 and tot["convidados"] == 1
    with pool.connection() as c:
        assert L.leads_por_ler(c, CONTA) == []                # lidos: nada mais pra ler
    _msg(pool, b, "vai ser 20 de março, 50 pessoas")           # mensagem nova: volta pra fila
    with pool.connection() as c:
        assert L.leads_por_ler(c, CONTA) == [b]


def test_ler_conversa_bg_so_roda_em_conta_que_vende_data(pool, monkeypatch):
    import finance.vendas as v
    lid = _lead(pool); _msg(pool, lid, "14/11/2026 15 anos 120 convidados")
    with pool.connection() as c:
        cid = c.execute("select id from conversas where prospeccao_id=%s", (lid,)).fetchone()[0]
    monkeypatch.setattr(v, "vende_data", lambda pool, conta_id: False)
    L.ler_conversa_bg(pool, CONTA, cid)
    assert _le(pool, lid)[0] is None
    monkeypatch.setattr(v, "vende_data", lambda pool, conta_id: True)
    L.ler_conversa_bg(pool, CONTA, cid)
    assert _le(pool, lid)[0] == date(2026, 11, 14)


def test_os_tres_webhooks_chamam_o_leitor_em_toda_mensagem_nova():
    """Twilio, Cloud API e QR: o leitor roda antes do agente e não depende de o
    agente estar ligado — é a diferença que faz o leitor existir."""
    import inspect
    from web import painel_prospeccao as pp
    fonte = inspect.getsource(pp)
    assert fonte.count("background_tasks.add_task(_leitor.ler_conversa_bg, get_pool()") == 3
