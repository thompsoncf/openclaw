"""A caixa de entrada precisa dizer DE QUEM é a conversa — e por qual chip ela sai.

Dois buracos que o mesmo chamado expôs:

1. A lista mostrava o nome de quem falou por último e nada sobre o dono do lead. Os
   dois se confundem justamente quando o vendedor responde por último, e o dono da
   conta não tinha como varrer a caixa atrás dos leads sem responsável.
2. O número do WhatsApp que envia existe no banco (a credencial do QR guarda o
   aparelho conectado) e não aparecia em tela nenhuma. Quem dispara campanha não
   sabia de qual número ia sair.

Banco descartável com o schema mínimo das duas funções — mesmo padrão dos vizinhos.
"""
import os

import pytest
from psycopg_pool import ConnectionPool

from web import painel_prospeccao as pp

CONTA = 7

_SQL = """
create table prospeccao (id bigserial primary key, conta_id bigint, vendedor_id bigint,
  empresa text, cidade text, uf text, estagio text default 'lead');
create table conversas (id bigserial primary key, conta_id bigint, prospeccao_id bigint,
  canal text default 'whatsapp', contato_ref text, contato_nome text,
  status text default 'aberta', ultima_msg_em timestamptz default now());
create table mensagens (id bigserial primary key, conversa_id bigint, canal text,
  direcao text, autor text, texto text, membro_id bigint,
  criado_em timestamptz default now());
create table membros (id bigserial primary key, conta_id bigint, nome text, email text,
  papel text, ativo boolean default true);
create table canais_config (conta_id bigint, canal text, provedor text,
  identificador text, ativo boolean default true);
create table wa_qr_auth (conta_id bigint, arquivo text, conteudo text);
"""


@pytest.fixture()
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_inbox_dono_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=3, open=True, kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_SQL)
        c.commit()
    yield p
    p.close()


def _semear(c):
    """Os três estados que a caixa tem de verdade."""
    vend = c.execute("insert into membros (conta_id,nome,papel) values (%s,'Rafael','vendedor') "
                     "returning id", (CONTA,)).fetchone()[0]
    outro = c.execute("insert into membros (conta_id,nome,papel) values (%s,'Bianca','vendedor') "
                      "returning id", (CONTA,)).fetchone()[0]

    def conversa(nome, *, lead, dono=None, ult_de=None):
        pid = None
        if lead:
            pid = c.execute("insert into prospeccao (conta_id,vendedor_id,empresa) "
                            "values (%s,%s,%s) returning id", (CONTA, dono, nome)).fetchone()[0]
        cid = c.execute("""insert into conversas (conta_id,prospeccao_id,contato_nome,contato_ref)
                           values (%s,%s,%s,'5586988887777') returning id""",
                        (CONTA, pid, nome)).fetchone()[0]
        c.execute("""insert into mensagens (conversa_id,canal,direcao,autor,texto,membro_id)
                     values (%s,'whatsapp','out','humano','oi',%s)""", (cid, ult_de))
        return cid

    return {
        "vend": vend, "outro": outro,
        # o vendedor respondeu por último: é aqui que "quem falou" e "de quem é" se confundem
        "com_dono": conversa("Padaria Trigo Real", lead=True, dono=vend, ult_de=vend),
        "sem_dono": conversa("Auto Center Gama", lead=True, dono=None, ult_de=vend),
        "sem_lead": conversa("Fulano do zap", lead=False, ult_de=vend),
    }


def _por_id(linhas):
    return {l["id"]: l for l in linhas}


# ------------------------------------------------------------------ o dono na lista

def test_a_lista_diz_de_quem_e_cada_conversa(pool):
    with pool.connection() as c:
        ids = _semear(c)
        c.commit()
        linhas = _por_id(_conversas(c))
    com = linhas[ids["com_dono"]]
    assert (com["eh_lead"], com["dono"], com["dono_id"]) == (True, "Rafael", ids["vend"])


def test_lead_sem_responsavel_e_marcado(pool):
    """É o que o dono quer caçar na caixa."""
    with pool.connection() as c:
        ids = _semear(c)
        c.commit()
        linhas = _por_id(_conversas(c))
    sem = linhas[ids["sem_dono"]]
    assert sem["eh_lead"] is True
    assert sem["dono"] == "" and sem["dono_id"] is None


def test_conversa_que_nao_e_lead_nao_ganha_marcador(pool):
    """A maior parte da caixa é contato que ainda não virou lead. Marcar todas de
    'sem responsável' seria alarme falso na maioria das linhas — é o que decidiu o
    desenho da tela."""
    with pool.connection() as c:
        ids = _semear(c)
        c.commit()
        linhas = _por_id(_conversas(c))
    fora = linhas[ids["sem_lead"]]
    assert fora["eh_lead"] is False
    assert fora["lead_id"] is None and fora["dono"] == ""


def test_o_dono_do_lead_nao_e_quem_falou_por_ultimo(pool):
    """O defeito de leitura que originou o pedido: quem responde por último aparecia
    como se fosse o responsável. São campos diferentes e podem divergir."""
    with pool.connection() as c:
        ids = _semear(c)
        # a Bianca responde por último num lead que é do Rafael. O criado_em explícito
        # não é decoração: dentro da mesma transação now() devolve o MESMO instante
        # pras duas mensagens, e "a última" viraria sorteio do banco.
        c.execute("""insert into mensagens (conversa_id,canal,direcao,autor,texto,membro_id,
                       criado_em)
                     values (%s,'whatsapp','out','humano','respondi',%s, now() + interval '1 min')""",
                  (ids["com_dono"], ids["outro"]))
        c.commit()
        linha = _por_id(_conversas(c))[ids["com_dono"]]
    assert linha["quem"] == "Bianca"      # quem falou
    assert linha["dono"] == "Rafael"      # de quem é


def _conversas(c):
    return pp._conversas_list(c, CONTA, True, None, escopo="msg")


# ------------------------------------------------------------------ o chip que envia

def test_o_numero_do_chip_sai_da_credencial_do_qr(pool, monkeypatch):
    """O identificador do canal guarda só 'qr:35'; o número real está no me.id da
    credencial, no formato '558698392961:14@s.whatsapp.net'."""
    monkeypatch.setattr(pp, "get_pool", lambda: pool)
    monkeypatch.setattr(pp, "_wa_qr_sincronizando", lambda cid: False)
    from finance import whatsapp_qr as qr
    monkeypatch.setattr(qr, "configurado", lambda: True)
    monkeypatch.setattr(qr, "status", lambda cid: {"status": "conectado"})
    with pool.connection() as c:
        c.execute("""insert into canais_config (conta_id,canal,provedor,identificador,ativo)
                     values (%s,'whatsapp','qr','qr:7',true)""", (CONTA,))
        c.execute("""insert into wa_qr_auth (conta_id,arquivo,conteudo) values (%s,'creds',%s)""",
                  (CONTA, '{"me":{"name":"Confeitaria doce mell",'
                          '"id":"558698392961:14@s.whatsapp.net"}}'))
        c.commit()
    pp._WA_CHIP_CACHE.clear()
    chip = pp._wa_chip(CONTA)
    assert chip["numero"] == "+55 86 9839-2961"     # sem o ':14@s.whatsapp.net'
    assert chip["nome"] == "Confeitaria doce mell"
    assert chip["estado"] == "conectado"


def test_chip_caido_nao_e_anunciado_como_conectado(pool, monkeypatch):
    """A sessão por QR cai. Dizer 'conectado' quando não está é pior que não dizer."""
    monkeypatch.setattr(pp, "get_pool", lambda: pool)
    monkeypatch.setattr(pp, "_wa_qr_sincronizando", lambda cid: False)
    from finance import whatsapp_qr as qr
    monkeypatch.setattr(qr, "configurado", lambda: True)
    monkeypatch.setattr(qr, "status", lambda cid: {"status": "desconectado"})
    with pool.connection() as c:
        c.execute("""insert into canais_config (conta_id,canal,provedor,identificador,ativo)
                     values (%s,'whatsapp','qr','qr:7',true)""", (CONTA,))
        c.execute("""insert into wa_qr_auth (conta_id,arquivo,conteudo) values (%s,'creds',%s)""",
                  (CONTA, '{"me":{"name":"X","id":"558698392961:14@s.whatsapp.net"}}'))
        c.commit()
    pp._WA_CHIP_CACHE.clear()
    assert pp._wa_chip(CONTA)["estado"] == "caido"


def test_sem_canal_ativo_o_cabecalho_diz_que_nao_tem_chip(pool, monkeypatch):
    monkeypatch.setattr(pp, "get_pool", lambda: pool)
    pp._WA_CHIP_CACHE.clear()
    chip = pp._wa_chip(CONTA)
    assert chip["estado"] == "sem_chip" and chip["numero"] == ""
