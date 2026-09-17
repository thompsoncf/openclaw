"""A JANELA DO COMPROMISSO NA AGENDA: começa, encerra, e quantas horas.

Pedido do dono em 16/09/2026, olhando a caixa do dia: "veja a possibilidade de
trazer o horário de encerramento e quantidade de horas".

O QUE A MEDIÇÃO ACHOU, e por isso este arquivo tem duas metades: mostrar era a
parte fácil (a coluna `fim` existe há tempo). O problema é que não havia o que
mostrar — das 24 festas futuras da Prime, ZERO tinham encerramento, porque a porta
do funil criava o compromisso com 19h de palpite e sem fim nenhum. Então aqui se
testa a TELA (a janela formatada) e a PONTE (o horário vindo do orçamento).
"""
from datetime import date, datetime, timedelta, timezone

import pytest

from finance import agenda as ag
from finance import funil_agenda as fa
from web.painel_agenda import janela_do_evento


def _dt(h, m=0, dia=1):
    return datetime(2026, 10, dia, h, m, tzinfo=ag.BRT)


# ------------------------------------------------------------------ a janela
def test_a_janela_mostra_comeco_fim_e_duracao():
    j = janela_do_evento(_dt(18), _dt(23))
    assert (j["ini"], j["fim"], j["dur"]) == ("18:00", "23:00", "5h")
    assert not j["vira"]


def test_a_festa_que_vira_a_noite_e_marcada():
    """19h→02h é a regra do ramo, não a exceção. Sem o `vira`, a caixa mostraria
    '20:00 → 02:00' e quem lê rápido entende que acabou seis horas ANTES."""
    j = janela_do_evento(_dt(20), _dt(2, 0, dia=2))
    assert (j["ini"], j["fim"], j["dur"]) == ("20:00", "02:00", "6h")
    assert j["vira"] is True


def test_duracao_quebrada_sai_legivel():
    """"6h30", nunca "6.5h". E meia hora é "30min", não "0h30" — relógio digital
    escreve assim, gente não."""
    assert janela_do_evento(_dt(19), _dt(1, 30, dia=2))["dur"] == "6h30"
    assert janela_do_evento(_dt(10), _dt(10, 30))["dur"] == "30min"
    assert janela_do_evento(_dt(11), _dt(12))["dur"] == "1h"


def test_sem_encerramento_a_janela_fica_vazia_e_nao_inventa_duracao():
    """O caso de 24 das 24 festas da Prime. A tela mostra o começo e oferece o
    botão; o sistema NÃO sugere 6h (decisão do dono em 17/09)."""
    j = janela_do_evento(_dt(19), None)
    assert j["ini"] == "19:00"
    assert j["fim"] == "" and j["dur"] == ""


def test_fim_antes_do_inicio_nao_vira_duracao_negativa():
    """Dado torto não pode virar "-1h" na tela de quem está conferindo a festa."""
    j = janela_do_evento(_dt(20), _dt(19))
    assert j["fim"] == "" and j["dur"] == ""


def test_sem_inicio_nao_explode():
    assert janela_do_evento(None, None)["ini"] == ""


# ------------------------------------------------------- a ponte funil → agenda
def test_a_ponte_usa_o_horario_do_orcamento():
    """O conserto: com horário no orçamento, a agenda recebe a janela inteira."""
    ini, fim = ag.janela_evento(date(2026, 10, 1), "20h", "01h")
    assert ini is not None and fim is not None
    assert (fim - ini) == timedelta(hours=5), "20h→01h é 5h no dia seguinte"


@pytest.mark.parametrize("ini,fim,horas", [
    ("18h", "23h", 5), ("21", "02", 5), ("19:00", "00:00", 5),
    ("20h30", "22h30", 2), ("17", "22", 5), ("20h", "01h", 5),
])
def test_os_formatos_de_hora_que_existem_na_producao(ini, fim, horas):
    """As grafias são as do banco da conta 34 — "20h30", "12h", "17", "19:00",
    "00:00". Um parser que engasgue numa delas deixa a festa sem horário de novo."""
    i, f = ag.janela_evento(date(2026, 10, 1), ini, fim)
    assert i is not None and f is not None
    assert (f - i) == timedelta(hours=horas)


class _FakeCursor:
    def __init__(self, linhas): self._l = linhas
    def fetchone(self): return self._l[0] if self._l else None
    def fetchall(self): return self._l


class _FakeConn:
    """Conexão de mentira: `garantir` só a usa pra procurar evento já existente no
    dia, e aqui a resposta é sempre "não existe" — o caminho que cria."""
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def execute(self, *a, **kw): return _FakeCursor([])
    def commit(self): pass


class _FakePool:
    def connection(self): return _FakeConn()


def _criar_capturado(monkeypatch):
    """Troca `agenda.criar_evento` por um espião e devolve a lista de chamadas.

    Testar o COMPORTAMENTO, e não o texto do fonte: a primeira versão destes dois
    testes lia `inspect.getsource` atrás de trechos de código — e foi assim que um
    teste meu passou verde com a tela quebrada ontem (#711). O que importa é o que
    a ponte MANDA pra agenda, não como ela está escrita.
    """
    chamadas = []

    def espiao(pool, conta_id, titulo, inicio, **kw):
        chamadas.append({"titulo": titulo, "inicio": inicio, **kw})
        return 1

    monkeypatch.setattr(ag, "criar_evento", espiao)
    return chamadas


def test_a_ponte_manda_a_janela_inteira_quando_ha_orcamento(monkeypatch):
    chamadas = _criar_capturado(monkeypatch)
    fa.garantir(_FakePool(), 34, {"id": 1, "quem": "Josiany", "data": date(2026, 10, 1),
                                  "tipo": "Casamento", "convidados": 100, "membro_id": 7,
                                  "hora_inicio": "20h", "hora_fim": "01h"})
    assert len(chamadas) == 1
    c = chamadas[0]
    assert c["fim"] is not None, "com horário no orçamento, a agenda tem que receber o fim"
    assert (c["fim"] - c["inicio"]) == timedelta(hours=5)
    assert c["hora_sugerida"] is False, "não é palpite: veio do orçamento"


def test_sem_orcamento_a_ponte_chuta_19h_e_NAO_inventa_duracao(monkeypatch):
    """Compatibilidade, e a decisão do dono: sem horário a ponte não sugere nada."""
    chamadas = _criar_capturado(monkeypatch)
    fa.garantir(_FakePool(), 34, {"id": 2, "quem": "Thayla", "data": date(2026, 10, 10),
                                  "tipo": "Locação", "convidados": None, "membro_id": 7,
                                  "hora_inicio": None, "hora_fim": None})
    c = chamadas[0]
    assert c["fim"] is None, "sem orçamento a ponte não pode inventar duração"
    assert c["hora_sugerida"] is True, "o palpite tem que continuar marcado como palpite"
    assert c["inicio"].astimezone(ag.BRT).strftime("%H:%M") == "19:00"


def test_orcamento_so_com_hora_de_inicio_nao_inventa_o_fim(monkeypatch):
    """Meio-caminho real: 21 orçamentos da conta 34 têm início e 20 têm fim."""
    chamadas = _criar_capturado(monkeypatch)
    fa.garantir(_FakePool(), 34, {"id": 3, "quem": "Alguém", "data": date(2026, 10, 5),
                                  "tipo": "Buffet", "convidados": None, "membro_id": None,
                                  "hora_inicio": "18h", "hora_fim": None})
    c = chamadas[0]
    assert c["inicio"].astimezone(ag.BRT).strftime("%H:%M") == "18:00", "a hora dele vale"
    assert c["fim"] is None, "mas o fim não se inventa"
    assert c["hora_sugerida"] is False


def test_a_ponte_so_aceita_orcamento_da_MESMA_data():
    """A trava que o dado de produção exigiu: a Josiany tem festa em 01/10 e o
    orçamento dela é de 19/12. Sem comparar a data, a agenda receberia o horário de
    OUTRA festa — pior que ficar sem horário.

    A comparação mora no SQL de `pendentes`. Aqui ela é exercida como SQL de
    verdade, num Postgres descartável, e não lida como texto.
    """
    import os

    from psycopg_pool import ConnectionPool
    url = os.environ["TEST_DATABASE_URL"]
    admin = ConnectionPool(url, min_size=1, max_size=1, open=True)
    dbname = "zaq_agenda_janela_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute("select pg_terminate_backend(pid) from pg_stat_activity where datname=%s", (dbname,))
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    p = ConnectionPool(url.rsplit("/", 1)[0] + "/" + dbname, min_size=1, max_size=2,
                       open=True, kwargs={"prepare_threshold": None})
    try:
        with p.connection() as c:
            c.execute("""
                create table prospeccao (id bigserial primary key, conta_id bigint,
                    contato text, empresa text, status text, estagio text,
                    evento_em date, evento_tipo text, evento_convidados int,
                    vendedor_id bigint, orcamento_id bigint);
                create table orcamentos (id bigserial primary key, conta_id bigint, evento jsonb);
                create table eventos_agenda (id bigserial primary key, conta_id bigint,
                    prospeccao_id bigint, status text);
                insert into orcamentos (id, conta_id, evento) values
                  (1, 34, '{"data":"2026-10-01","inicio":"20h","fim":"01h"}'),
                  (2, 34, '{"data":"2026-12-19","inicio":"17","fim":"22"}');
                insert into prospeccao (conta_id, contato, status, estagio, evento_em, orcamento_id)
                values (34, 'Mesma data',  'fechado', 'lead', date '2026-10-01', 1),
                       (34, 'Outra data',  'fechado', 'lead', date '2026-10-01', 2),
                       (34, 'Sem orcamento','fechado', 'lead', date '2026-10-01', null);
            """)
            c.commit()
            linhas = {x["quem"]: x for x in fa.pendentes(c, 34, ["fechado"])}
        assert linhas["Mesma data"]["hora_fim"] == "01h", "o orçamento da data certa vale"
        assert linhas["Outra data"]["hora_fim"] is None, (
            "orçamento de OUTRA data não pode emprestar horário — é o caso da Josiany")
        assert linhas["Sem orcamento"]["hora_fim"] is None
    finally:
        p.close()
