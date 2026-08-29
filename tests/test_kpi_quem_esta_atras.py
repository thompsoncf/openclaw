"""Os KPIs de "Gastos das campanhas" respondiam QUANTOS. Agora respondem QUEM.

Um contador sem lista é métrica de vaidade: nesta base havia lead perguntando
"qual exatamente é o produto que você oferece?" e ninguém sabia que ele existia,
porque o número 52 não tinha nome dentro.

Os três contadores antigos (Agora não / Quero te conhecer / Quero o material) ficam
com o filtro de canal INTACTO — mudá-los mudaria o significado de um número que o
dono acompanha há semanas. Os quatro novos entram ao lado, cobrindo o que eles não
pegam: o CTA do e-mail, o material aberto, quem escreveu no chat, e — o que vira
dinheiro — quantos desses nunca receberam resposta de gente.
"""
import os

import pytest
from psycopg_pool import ConnectionPool

from web import painel_prospeccao as pp

_SQL = """
create table contas (id bigint primary key, telefone text, email text, chip_de bigint);
create table membros (id bigserial primary key, conta_id bigint, nome text, email text);
create table prospeccao (id bigserial primary key, conta_id bigint, vendedor_id bigint,
  empresa text, segmento text, cidade text, uf text, telefone text, whatsapp text,
  email text, temperatura text);
create table campanhas (id bigserial primary key, conta_id bigint, nome text);
create table campanha_alvos (id bigserial primary key, campanha_id bigint, prospeccao_id bigint);
create table campanha_eventos (id bigserial primary key, campanha_id bigint, prospeccao_id bigint,
  canal text, evento text, detalhe text, quando timestamptz default now());
create table conversas (id bigserial primary key, conta_id bigint, prospeccao_id bigint, chip_id bigint, visto_ate_id bigint);
create table mensagens (id bigserial primary key, conversa_id bigint, direcao text, autor text,
  texto text, criado_em timestamptz default now(),
  midia_ref jsonb, midia_tipo text, midia_meta jsonb, midia_arquivo text, midia_guardada_em timestamptz, midia_guardada_por bigint);
"""


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_kpi_quem_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=3, open=True, kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_SQL)
        # a conta usa o telefone 86981885930 / dono@zaq.com — é por eles que o
        # "teste da casa" é reconhecido
        c.execute("insert into contas (id, telefone, email) values (1,'86981885930','dono@zaq.com')")
        c.execute("insert into membros (id, conta_id, nome, email) values (7,1,'Vend','v@x.com')")
        c.execute("insert into campanhas (id, conta_id, nome) values (1,1,'Camp A')")
        c.commit()
    yield p
    p.close()


_SEQ = [500]


def _lead(pool, *, empresa=None, fone="86999990000", email=None, eventos=(), msgs=(), vendedor=7):
    """`eventos` = [(canal, evento, detalhe)]; `msgs` = [(direcao, autor, texto)]."""
    _SEQ[0] += 1
    pid = _SEQ[0]
    with pool.connection() as c:
        c.execute("""insert into prospeccao (id, conta_id, vendedor_id, empresa, whatsapp, email, temperatura)
                     values (%s,1,%s,%s,%s,%s,'frio')""",
                  (pid, vendedor, empresa or f"Lead {pid}", fone, email))
        c.execute("insert into campanha_alvos (campanha_id, prospeccao_id) values (1,%s)", (pid,))
        for canal, ev, det in eventos:
            c.execute("""insert into campanha_eventos (campanha_id, prospeccao_id, canal, evento, detalhe)
                         values (1,%s,%s,%s,%s)""", (pid, canal, ev, det))
        if msgs:
            cvid = c.execute("insert into conversas (conta_id, prospeccao_id) values (1,%s) returning id",
                             (pid,)).fetchone()[0]
            for direcao, autor, texto in msgs:
                c.execute("""insert into mensagens (conversa_id, direcao, autor, texto)
                             values (%s,%s,%s,%s)""", (cvid, direcao, autor, texto))
        c.commit()
    return pid


def _ids(pool, sinal, membro=None):
    with pool.connection() as c:
        return {i["id"] for i in pp._kpi_leads(c, 1, membro, sinal)}


def _um(pool, sinal, pid):
    with pool.connection() as c:
        for i in pp._kpi_leads(c, 1, None, sinal):
            if i["id"] == pid:
                return i
    return None


# ------------------------------------------- os três antigos, com o filtro intacto

def test_agora_nao_so_pega_whatsapp():
    """O filtro de canal dos três antigos é deliberado. Se alguém 'consertar' isso,
    o número que o dono acompanha muda de significado da noite pro dia."""
    assert pp._KPI_SINAIS["sem_interesse"][0] == "whatsapp"
    assert pp._KPI_SINAIS["quer_conhecer"][0] == "whatsapp"
    assert pp._KPI_SINAIS["quer_material"][0] == "whatsapp"


def test_quem_clicou_agora_nao_aparece(pool):
    pid = _lead(pool, eventos=[("whatsapp", "clicou", "Agora não")])
    assert pid in _ids(pool, "sem_interesse")
    assert pid not in _ids(pool, "quer_conhecer")


def test_interesse_por_email_nao_entra_no_kpi_de_whatsapp(pool):
    """Este é o buraco que motivou os KPIs novos: quem clicou no e-mail não aparecia
    em lugar nenhum."""
    pid = _lead(pool, eventos=[("email", "respondeu", "Tenho interesse")])
    assert pid not in _ids(pool, "quer_conhecer")     # canal errado, de propósito
    assert pid in _ids(pool, "interesse_email")       # mas agora tem onde aparecer


def test_baixou_material_conta_em_qualquer_canal(pool):
    """`baixou` não tinha contador nenhum, e vale nos dois canais."""
    a = _lead(pool, eventos=[("email", "baixou", "")])
    b = _lead(pool, eventos=[("whatsapp", "baixou", "")])
    assert {a, b} <= _ids(pool, "baixou_material")


# ------------------------------------------------- quem escreveu, e quem foi ouvido

def test_quem_escreveu_aparece_mesmo_sem_clicar_botao(pool):
    pid = _lead(pool, msgs=[("in", "lead", "Qual exatamente é o produto?")])
    assert pid in _ids(pool, "conversou")


def test_so_mensagem_nossa_nao_conta_como_conversa(pool):
    """Conversa é o lead falando. Só a gente falando é monólogo."""
    pid = _lead(pool, msgs=[("out", "bot", "oi"), ("out", "humano", "tudo bem?")])
    assert pid not in _ids(pool, "conversou")


def test_sem_resposta_humana_ignora_o_bot(pool):
    """O bot responder não é atendimento — é o que faz o lead achar que falou com
    alguém enquanto ninguém viu."""
    so_bot = _lead(pool, msgs=[("in", "lead", "tenho interesse"), ("out", "bot", "olá!")])
    atendido = _lead(pool, msgs=[("in", "lead", "tenho interesse"), ("out", "humano", "opa!")])
    mudos = _ids(pool, "sem_humano")
    assert so_bot in mudos
    assert atendido not in mudos
    assert {so_bot, atendido} <= _ids(pool, "conversou")


def test_conta_msgs_e_respostas(pool):
    pid = _lead(pool, msgs=[("in", "lead", "oi"), ("in", "lead", "?"), ("out", "humano", "opa")])
    it = _um(pool, "conversou", pid)
    assert it["msgs_lead"] == 2 and it["resp_humana"] == 1


# --------------------------------------------------- teste da casa e autoresposta

def test_lead_com_o_telefone_da_conta_e_marcado_como_teste(pool):
    """O dono testando o próprio bot vira 'lead' e infla o KPI. Nesta base havia
    dois assim, um deles com 42 mensagens — parecia o lead mais quente da carteira."""
    pid = _lead(pool, fone="86981885930", msgs=[("in", "lead", "oi")])
    assert _um(pool, "conversou", pid)["eh_teste"] is True


def test_lead_com_o_email_da_conta_tambem(pool):
    pid = _lead(pool, fone="86911112222", email="dono@zaq.com", msgs=[("in", "lead", "oi")])
    assert _um(pool, "conversou", pid)["eh_teste"] is True


def test_lead_de_verdade_nao_e_marcado_como_teste(pool):
    pid = _lead(pool, fone="86933334444", email="cliente@empresa.com", msgs=[("in", "lead", "oi")])
    assert _um(pool, "conversou", pid)["eh_teste"] is False


@pytest.mark.parametrize("txt", [
    "Agradecemos seu contato. Como podemos ajudar?",
    "Olá! Seja bem-vindo(a) à Clínica X",
    "Não estamos disponíveis no momento, nosso horário de funcionamento é das 8h",
    "Retornaremos assim que possível",
])
def test_autoresposta_do_estabelecimento_e_marcada(pool, txt):
    assert pp._parece_autoresposta(txt) is True


@pytest.mark.parametrize("txt", [
    "Qual exatamente é o produto que você oferece?",
    "quero entender melhor",
    "Como faço pra fazer um orçamento",
    "Quero automatizar uma campanha de trade marketing",
])
def test_pessoa_de_verdade_nao_vira_ruido(pool, txt):
    """Errar aqui esconde justamente o lead que vale a ligação."""
    assert pp._parece_autoresposta(txt) is False


def test_ruido_e_marcado_mas_nunca_sumido(pool):
    """A regra é heurística: some com um lead de verdade se errar. Marca, não filtra."""
    pid = _lead(pool, msgs=[("in", "lead", "Agradecemos seu contato. Como podemos ajudar?")])
    it = _um(pool, "conversou", pid)
    assert it is not None and it["eh_ruido"] is True


# --------------------------------------------------------------- escopo e contato

def test_vendedor_so_ve_a_carteira_dele(pool):
    meu = _lead(pool, vendedor=7, msgs=[("in", "lead", "oi")])
    outro = _lead(pool, vendedor=None, msgs=[("in", "lead", "oi")])
    vistos = _ids(pool, "conversou", membro=7)
    assert meu in vistos and outro not in vistos


def test_link_de_whatsapp_sai_pronto_pra_clicar(pool):
    pid = _lead(pool, fone="(86) 99400-8350", eventos=[("whatsapp", "clicou", "Agora não")])
    assert _um(pool, "sem_interesse", pid)["wa_link"] == "https://wa.me/5586994008350"
