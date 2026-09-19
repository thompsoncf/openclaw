"""A esteira da cobrança (finance/esteira): 10 por dia, 3 cobranças, dia 7 fecha.

O que estes testes protegem, em uma frase cada:
  * o relógio é a ENTRADA na esteira, não o prazo vencido da etapa — foi o defeito
    que fez a Layane receber os quatro degraus no mesmo minuto;
  * entram no máximo `por_dia` por vendedor, e rodar de novo no mesmo dia não
    duplica ninguém;
  * cliente esperando resposta NUNCA entra na esteira;
  * a mensagem que saiu do WhatsApp Web (sem membro_id) conta como ação — é como a
    Prime trabalha, 1.191 de 1.505 mensagens em setembro;
  * o cliente voltar a falar resolve, e não vira cobrança do vendedor;
  * cobra no dia 1, 3 e 7 — e só neles;
  * o dia 7 marca `ultimo_dia`, e o fechamento só acontece DEPOIS da janela;
  * 'observando' entra e cobra, mas não fecha ninguém;
  * quem mexeu no lead ganha da esteira.
"""
import os
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from finance import esteira as es

MIG = Path(__file__).resolve().parent.parent / "db" / "migracoes"
CONTA = 13
VEND, OUTRO = 71, 72
#: 10h em Brasília — dentro da janela (08:00–19:00)
AGORA = datetime(2026, 9, 19, 13, 0, tzinfo=timezone.utc)

_SQL = """
create table prospeccao (id bigserial primary key, conta_id bigint, empresa text, contato text,
  status text default 'novo', estagio text default 'lead', vendedor_id bigint,
  evento_em date, proximo_contato_em timestamptz, atualizado_em timestamptz default now(),
  criado_em timestamptz default now());
create table membros (id bigserial primary key, conta_id bigint, nome text, email text,
  papel text default 'vendedor', ativo boolean default true);
create table conversas (id bigserial primary key, conta_id bigint, prospeccao_id bigint,
  canal text default 'whatsapp');
create table mensagens (id bigserial primary key, conversa_id bigint, direcao text,
  autor text default 'humano', membro_id bigint, texto text default '',
  criado_em timestamptz default now());
create table funil_movimentos (id bigserial primary key, conta_id bigint, prospeccao_id bigint,
  de text, para text, motivo text, membro_id bigint, criado_em timestamptz default now());
create table funil_etapas (id bigserial primary key, semeado_de text, conta_id bigint, chave text,
  rotulo text, ordem int default 0, fixa boolean default false, fase text default 'venda',
  prazo_min integer, gatilho text, gatilho_ativo boolean default false);
create table funil_regua (conta_id bigint primary key,
  gatilhos_modo text not null default 'off', cobranca_modo text not null default 'off',
  janela_dias text, janela_abre time, janela_fecha time,
  sem_resposta_min int, bola_nossa_min int, bola_cliente_min int,
  escala_min int, teto_avisos_dia int,
  follow_up_modo text not null default 'off', fu_proposta_dias int, fu_toques_dias text,
  fu_zap boolean not null default false, fu_festa_dias int, fu_teto_dia int,
  atualizado_em timestamptz not null default now());
"""


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_esteira_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=3, open=True, kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_SQL)
        for nome in ("209_raio_x_dono.sql", "213_perda_motivo_por_perfil.sql",
                     "230_funil_teto_da_etapa.sql", "235_motivos_de_perda_da_conta.sql",
                     "238_etapa_sai_do_quadro.sql", "254_funil_semeado_de.sql",
                     "292_esteira_da_cobranca.sql"):
            c.execute((MIG / nome).read_text(encoding="utf-8"))
        for ch, o in (("novo", 0), ("contatado", 10), ("proposta", 30), ("perdido", 910)):
            c.execute("insert into funil_etapas (conta_id, chave, rotulo, ordem) values (%s,%s,%s,%s)",
                      (CONTA, ch, ch.capitalize(), o))
        c.execute("update funil_etapas set teto_dias=7 where conta_id=%s and chave in ('contatado','proposta')",
                  (CONTA,))
        for mid, nome in ((VEND, "Vendedor"), (OUTRO, "Outro")):
            c.execute("insert into membros (id, conta_id, nome) values (%s,%s,%s)", (mid, CONTA, nome))
        c.commit()
    yield p
    p.close()


@pytest.fixture()
def c(pool):
    with pool.connection() as con:
        for t in ("follow_up_esteira", "funil_movimentos", "mensagens", "conversas",
                  "prospeccao", "funil_regua"):
            con.execute(f"delete from {t}")
        con.execute("""insert into funil_regua (conta_id, esteira_modo, fu_teto_dia, fu_toques_dias,
                                                janela_dias, janela_abre, janela_fecha)
                       values (%s,'ligado',10,'1,3,7','1,2,3,4,5,6','08:00','19:00')""", (CONTA,))
        con.commit()
        yield con
        con.rollback()


# ------------------------------------------------------------------ ajudantes

def _lead(c, *, vend=VEND, etapa="contatado", dias=30, nome="Lead"):
    lid = c.execute("""insert into prospeccao (conta_id, contato, status, estagio, vendedor_id, criado_em)
                       values (%s,%s,%s,'lead',%s,%s) returning id""",
                    (CONTA, nome, etapa, vend, AGORA - timedelta(days=dias))).fetchone()[0]
    c.execute("""insert into funil_movimentos (conta_id, prospeccao_id, de, para, criado_em)
                 values (%s,%s,'novo',%s,%s)""", (CONTA, lid, etapa, AGORA - timedelta(days=dias)))
    return lid


def _msg(c, lead, direcao, *, membro=None, quando=None):
    conv = c.execute("select id from conversas where prospeccao_id=%s", (lead,)).fetchone()
    if not conv:
        conv = c.execute("""insert into conversas (conta_id, prospeccao_id) values (%s,%s) returning id""",
                         (CONTA, lead)).fetchone()
    c.execute("insert into mensagens (conversa_id, direcao, membro_id, criado_em) values (%s,%s,%s,%s)",
              (conv[0], direcao, membro, quando or (AGORA - timedelta(days=20))))


def _com_bola_nossa(c, **kw):
    """Um lead onde NÓS falamos por último — o estado normal de quem está parado."""
    lid = _lead(c, **kw)
    _msg(c, lid, "in", quando=AGORA - timedelta(days=21))
    _msg(c, lid, "out", quando=AGORA - timedelta(days=20))
    return lid


def _entrou_ha(c, lead, dias):
    c.execute("update follow_up_esteira set entrou_em=%s where prospeccao_id=%s",
              (AGORA - timedelta(days=dias), lead))


# ------------------------------------------------------------------ quem entra

def test_entram_no_maximo_dez_por_vendedor(c):
    for i in range(14):
        _com_bola_nossa(c, nome=f"L{i}")
    assert len(es.entrar(c, CONTA, AGORA)) == 10


def test_rodar_de_novo_no_mesmo_dia_nao_duplica(c):
    for i in range(12):
        _com_bola_nossa(c, nome=f"L{i}")
    assert len(es.entrar(c, CONTA, AGORA)) == 10
    assert es.entrar(c, CONTA, AGORA) == []
    assert c.execute("select count(*) from follow_up_esteira").fetchone()[0] == 10


def test_cada_vendedor_tem_a_propria_cota(c):
    for i in range(12):
        _com_bola_nossa(c, nome=f"A{i}", vend=VEND)
    for i in range(12):
        _com_bola_nossa(c, nome=f"B{i}", vend=OUTRO)
    novos = es.entrar(c, CONTA, AGORA)
    assert len(novos) == 20
    assert sum(1 for n in novos if n["membro_id"] == VEND) == 10


def test_cliente_esperando_nunca_entra(c):
    lid = _lead(c, nome="Esperando")
    _msg(c, lid, "out", quando=AGORA - timedelta(days=21))
    _msg(c, lid, "in", quando=AGORA - timedelta(days=20))   # ele falou por último
    assert es.entrar(c, CONTA, AGORA) == []


def test_etapa_sem_prazo_nao_entra(c):
    _com_bola_nossa(c, etapa="novo")
    assert es.entrar(c, CONTA, AGORA) == []


def test_o_mais_antigo_entra_primeiro(c):
    velho = _com_bola_nossa(c, nome="Velho", dias=60)
    for i in range(12):
        _com_bola_nossa(c, nome=f"N{i}", dias=10)
    assert velho in [n["id"] for n in es.entrar(c, CONTA, AGORA)]


# ------------------------------------------------------------------ quem já agiu

def test_mensagem_do_whatsapp_web_conta_como_acao(c):
    """membro_id nulo é o eco do WhatsApp Web — é assim que a Prime trabalha."""
    lid = _com_bola_nossa(c)
    es.entrar(c, CONTA, AGORA)
    _msg(c, lid, "out", membro=None, quando=AGORA + timedelta(hours=1))
    assert es.resolver(c, CONTA, AGORA + timedelta(hours=2)) == 1
    assert c.execute("select resolucao from follow_up_esteira where prospeccao_id=%s",
                     (lid,)).fetchone()[0] == "falou"


def test_cliente_voltar_a_falar_resolve(c):
    lid = _com_bola_nossa(c)
    es.entrar(c, CONTA, AGORA)
    _msg(c, lid, "in", quando=AGORA + timedelta(hours=1))
    es.resolver(c, CONTA, AGORA + timedelta(hours=2))
    assert c.execute("select resolucao from follow_up_esteira where prospeccao_id=%s",
                     (lid,)).fetchone()[0] == "cliente_voltou"


def test_mover_o_card_resolve(c):
    lid = _com_bola_nossa(c)
    es.entrar(c, CONTA, AGORA)
    c.execute("""insert into funil_movimentos (conta_id, prospeccao_id, de, para, motivo, criado_em)
                 values (%s,%s,'contatado','proposta','manual',%s)""",
              (CONTA, lid, AGORA + timedelta(hours=1)))
    es.resolver(c, CONTA, AGORA + timedelta(hours=2))
    assert c.execute("select resolucao from follow_up_esteira where prospeccao_id=%s",
                     (lid,)).fetchone()[0] == "moveu"


def test_a_temperatura_nao_conta_como_acao(c):
    """O motor da temperatura mexe no card sozinho; isso não é o vendedor agindo."""
    lid = _com_bola_nossa(c)
    es.entrar(c, CONTA, AGORA)
    c.execute("""insert into funil_movimentos (conta_id, prospeccao_id, de, para, motivo, criado_em)
                 values (%s,%s,'quente','morno','temperatura',%s)""",
              (CONTA, lid, AGORA + timedelta(hours=1)))
    assert es.resolver(c, CONTA, AGORA + timedelta(hours=2)) == 0


# ------------------------------------------------------------------ as cobranças

def test_cobra_no_dia_1_3_e_7_e_so_neles(c):
    lid = _com_bola_nossa(c)
    es.entrar(c, CONTA, AGORA)
    vistos = []
    for d in range(0, 9):
        _entrou_ha(c, lid, d)
        vistos.append(bool(es.cobrancas(c, CONTA, AGORA)))
    assert [i for i, v in enumerate(vistos) if v] == [1, 3, 7]


def test_o_dia_7_e_o_ultimo(c):
    lid = _com_bola_nossa(c)
    es.entrar(c, CONTA, AGORA)
    _entrou_ha(c, lid, 3)
    assert es.cobrancas(c, CONTA, AGORA)[0]["ultimo_dia"] is False
    _entrou_ha(c, lid, 7)
    assert es.cobrancas(c, CONTA, AGORA)[0]["ultimo_dia"] is True


# ------------------------------------------------------------------ o dia 7

def _fim_do_dia(dias_depois=0):
    """19:30 em Brasília — depois da janela."""
    return AGORA.replace(hour=22, minute=30) + timedelta(days=dias_depois)


def test_fecha_no_dia_7_depois_da_janela(c):
    lid = _com_bola_nossa(c)
    es.entrar(c, CONTA, AGORA)
    _entrou_ha(c, lid, 7)
    fechados = es.fechar_vencidos(c, CONTA, _fim_do_dia())
    assert len(fechados) == 1
    assert c.execute("select status from prospeccao where id=%s", (lid,)).fetchone()[0] == "perdido"
    de, para, motivo = c.execute(
        "select de, para, motivo from funil_movimentos where prospeccao_id=%s and para='perdido'",
        (lid,)).fetchone()
    assert (de, para, motivo) == ("contatado", "perdido", es.MOTIVO_MOV)
    # o motivo na ficha NÃO culpa o cliente
    mot, desc = c.execute("select perda_motivo, perda_descricao from prospeccao where id=%s",
                          (lid,)).fetchone()
    assert mot == es.MOTIVO_PERDA and "sem tratativa" in (desc or "")


def test_nao_fecha_antes_da_janela_terminar(c):
    """O aviso diz 'resolva ou fecha hoje às 19h'. Fechar às 13h seria mentira."""
    lid = _com_bola_nossa(c)
    es.entrar(c, CONTA, AGORA)
    _entrou_ha(c, lid, 7)
    assert es.fechar_vencidos(c, CONTA, AGORA) == []
    assert c.execute("select status from prospeccao where id=%s", (lid,)).fetchone()[0] == "contatado"


def test_nao_fecha_antes_do_dia_7(c):
    lid = _com_bola_nossa(c)
    es.entrar(c, CONTA, AGORA)
    _entrou_ha(c, lid, 6)
    assert es.fechar_vencidos(c, CONTA, _fim_do_dia()) == []


def test_observando_cobra_mas_nao_fecha(c):
    c.execute("update funil_regua set esteira_modo='observando' where conta_id=%s", (CONTA,))
    lid = _com_bola_nossa(c)
    es.entrar(c, CONTA, AGORA)
    _entrou_ha(c, lid, 7)
    assert es.cobrancas(c, CONTA, AGORA)[0]["ultimo_dia"] is True
    assert es.fechar_vencidos(c, CONTA, _fim_do_dia()) == []
    assert c.execute("select status from prospeccao where id=%s", (lid,)).fetchone()[0] == "contatado"


def test_quem_agiu_no_ultimo_dia_nao_fecha(c):
    lid = _com_bola_nossa(c)
    es.entrar(c, CONTA, AGORA)
    _entrou_ha(c, lid, 7)
    _msg(c, lid, "out", membro=None, quando=_fim_do_dia() - timedelta(hours=2))
    es.resolver(c, CONTA, _fim_do_dia())
    assert es.fechar_vencidos(c, CONTA, _fim_do_dia()) == []
    assert c.execute("select status from prospeccao where id=%s", (lid,)).fetchone()[0] == "contatado"


def test_pessoa_ganha_da_esteira(c):
    """Alguém moveu o card entre a leitura e o fechamento: a esteira se cala."""
    lid = _com_bola_nossa(c)
    es.entrar(c, CONTA, AGORA)
    _entrou_ha(c, lid, 7)
    c.execute("update prospeccao set status='proposta' where id=%s", (lid,))
    assert es.fechar_vencidos(c, CONTA, _fim_do_dia()) == []


# ------------------------------------------------------------------ o resumo

def test_resumo_conta_o_que_fez_e_o_que_ficou(c):
    feito = _com_bola_nossa(c, nome="Feito")
    _com_bola_nossa(c, nome="Parado")
    es.entrar(c, CONTA, AGORA)
    _msg(c, feito, "out", membro=None, quando=AGORA + timedelta(hours=1))
    es.resolver(c, CONTA, AGORA + timedelta(hours=2))
    r = es.resumo(c, CONTA, VEND, AGORA + timedelta(hours=2))
    assert r["tratou"] == 1 and r["falou"] == 1 and r["na_esteira"] == 1


def test_modo_off_nao_faz_nada(c):
    c.execute("update funil_regua set esteira_modo='off' where conta_id=%s", (CONTA,))
    _com_bola_nossa(c)
    r = es.avaliar(c, CONTA, AGORA)
    assert r["entraram"] == 0 and r["cobrancas"] == []
    assert c.execute("select count(*) from follow_up_esteira").fetchone()[0] == 0
