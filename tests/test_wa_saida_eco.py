"""O eco da mensagem que o vendedor manda pelo CELULAR (fora do Zaq).

O Baileys devolve como fromMe tudo que sai do aparelho pareado, e é isso que
mantém a caixa honesta: sem o eco, a conversa mostra só o que o cliente escreveu
e o vendedor não sabe o que já foi respondido.

Três buracos apareceram no mesmo chamado, todos com o mesmo desfecho — a
mensagem sumia em silêncio, e no log ficava só "sem conversa pro número":

1. **Vendedor escrevendo PRIMEIRO.** Sem conversa existente o eco era descartado
   de propósito. Só que abrir conversa é o caso normal: em produção o vendedor
   mandou um "oi" às 15:56, sumiu; às 15:57 a pessoa respondeu, e aí a resposta
   criou lead e conversa — a thread nasceu começando pela RESPOSTA, sem a
   pergunta. Agora o eco abre conversa ÓRFÃ (nunca lead: o funil é do vendedor
   pra encher), igual ao que o histórico importado já faz.

2. **Nono dígito.** O mesmo contato chega ora com 12 dígitos, ora com 13. A busca
   era por igualdade crua de `contato_ref` — as duas grafias não se encontravam.

3. **Conversa já ligada a um lead.** Ela só casava pelo `prospeccao_id` do lead
   achado pelo telefone; com DUAS fichas do mesmo número, a busca achava a ficha
   A, a conversa pendurada na ficha B, e nada era encontrado.

Schema mínimo dos caminhos exercitados; nada de migração.
"""
import os

import pytest
from psycopg_pool import ConnectionPool

from web import painel_prospeccao as pp

CONTA = 7
NUM13 = "5586998392961"      # com o nono dígito
NUM12 = "558698392961"       # o MESMO número, do jeito antigo

_SQL = """
create table prospeccao (id bigserial primary key, conta_id bigint, vendedor_id bigint,
  empresa text, contato text, tipo text default 'pj',
  telefone text, whatsapp text, email text, origem text,
  status text default 'novo', temperatura text default 'frio', estagio text default 'base',
  atualizado_em timestamptz default now(), criado_em timestamptz default now());
create table conversas (id bigserial primary key, conta_id bigint, prospeccao_id bigint,
  canal text default 'whatsapp', contato_ref text, contato_nome text,
  status text default 'aberta', agente_ativo boolean default true,
  ultima_msg_em timestamptz default now(), criado_em timestamptz default now());
create table mensagens (id bigserial primary key, conversa_id bigint, canal text,
  direcao text, autor text, texto text, membro_id bigint, provider_sid text unique,
  status text, criado_em timestamptz default now());
create table wa_contatos (conta_id bigint, numero8 text, nome text,
  da_agenda boolean default false, primary key (conta_id, numero8));
"""


@pytest.fixture()
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_saida_eco_test"
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


def _eco(c, numero, texto, *, sid=None):
    return pp._wa_saida_conversa(c, CONTA, numero, texto, sid)


def _msgs(c, conv_id):
    return c.execute(
        "select direcao, autor, texto from mensagens where conversa_id=%s order by id",
        (conv_id,)).fetchall()


def _conversa(c, conv_id):
    return c.execute(
        "select contato_ref, contato_nome, prospeccao_id, agente_ativo from conversas where id=%s",
        (conv_id,)).fetchone()


# ------------------------------------------------- 1. vendedor escreve primeiro

def test_sem_conversa_o_eco_abre_uma(pool):
    """O caso do chamado: o "oi" do vendedor não pode mais evaporar."""
    with pool.connection() as c:
        conv = _eco(c, NUM13, "oi, bom dia!", sid="s1")
        c.commit()
        assert conv
        assert _msgs(c, conv) == [("out", "humano", "oi, bom dia!")]


def test_conversa_aberta_pelo_eco_nao_vira_lead(pool):
    """Quem o vendedor procura pelo celular não entra no funil sozinho — a conversa
    nasce órfã e ele decide se vale."""
    with pool.connection() as c:
        conv = _eco(c, NUM13, "oi", sid="s1")
        c.commit()
        assert _conversa(c, conv)[2] is None
        assert c.execute("select count(*) from prospeccao").fetchone()[0] == 0


def test_conversa_aberta_pelo_eco_nasce_muda(pool):
    """Agente ligado responderia sozinho numa conversa que o VENDEDOR começou —
    ele acabou de falar com a pessoa, não é hora de o robô entrar."""
    with pool.connection() as c:
        conv = _eco(c, NUM13, "oi", sid="s1")
        c.commit()
        assert _conversa(c, conv)[3] is False


def test_conversa_nova_ja_nasce_com_o_nome_da_agenda(pool):
    """Senão ela aparece na caixa como número cru, com o nome guardado ali do lado."""
    with pool.connection() as c:
        c.execute("insert into wa_contatos (conta_id, numero8, nome, da_agenda) "
                  "values (%s,%s,'Confeitaria Doce Mell',true)", (CONTA, NUM13[-8:]))
        conv = _eco(c, NUM13, "oi", sid="s1")
        c.commit()
        assert _conversa(c, conv)[1] == "Confeitaria Doce Mell"


def test_segunda_mensagem_cai_na_mesma_conversa(pool):
    """Abrir conversa não pode virar abrir UMA POR MENSAGEM."""
    with pool.connection() as c:
        a = _eco(c, NUM13, "oi", sid="s1")
        b = _eco(c, NUM13, "consegue falar?", sid="s2")
        c.commit()
        assert a == b
        assert len(_msgs(c, a)) == 2


# ------------------------------------------------------------- 2. nono dígito

def test_acha_a_conversa_escrita_sem_o_nono_digito(pool):
    """Conversa importada do histórico com 12 dígitos, eco chegando com 13."""
    with pool.connection() as c:
        conv = c.execute(
            "insert into conversas (conta_id, contato_ref) values (%s,%s) returning id",
            (CONTA, NUM12)).fetchone()[0]
        assert _eco(c, NUM13, "oi", sid="s1") == conv
        c.commit()
        assert c.execute("select count(*) from conversas").fetchone()[0] == 1


def test_acha_a_conversa_escrita_com_o_nono_digito(pool):
    """E o contrário — o WhatsApp manda das duas formas."""
    with pool.connection() as c:
        conv = c.execute(
            "insert into conversas (conta_id, contato_ref) values (%s,%s) returning id",
            (CONTA, NUM13)).fetchone()[0]
        assert _eco(c, NUM12, "oi", sid="s1") == conv
        c.commit()


def test_nao_confunde_final_igual_de_outro_ddd(pool):
    """A trava do casamento frouxo: os últimos 8 dígitos batem, o número é outro.
    Gravar aqui seria mostrar a conversa de um cliente dentro da de outro."""
    with pool.connection() as c:
        outro = c.execute(
            "insert into conversas (conta_id, contato_ref) values (%s,'5511998392961') returning id",
            (CONTA,)).fetchone()[0]
        conv = _eco(c, NUM13, "oi", sid="s1")
        c.commit()
        assert conv != outro
        assert _msgs(c, outro) == []


# ------------------------------------------- 3. conversa já pendurada num lead

def test_conversa_de_lead_recebe_o_eco(pool):
    """O básico: conversa ligada ao lead do telefone."""
    with pool.connection() as c:
        lead = c.execute(
            """insert into prospeccao (conta_id, empresa, whatsapp) values (%s,'Doce Mell',%s)
               returning id""", (CONTA, "+" + NUM13)).fetchone()[0]
        conv = c.execute(
            """insert into conversas (conta_id, prospeccao_id, contato_ref)
               values (%s,%s,%s) returning id""", (CONTA, lead, NUM13)).fetchone()[0]
        assert _eco(c, NUM13, "te mandei o orçamento", sid="s1") == conv
        c.commit()


def test_dois_leads_com_o_mesmo_numero_nao_perdem_a_mensagem(pool):
    """A ficha achada pelo telefone é a mais recente (B), a conversa está pendurada na
    outra (A). Antes, exigir `prospeccao_id is null` pra casar por número fazia a
    mensagem cair no vazio."""
    with pool.connection() as c:
        antigo = c.execute(
            """insert into prospeccao (conta_id, empresa, whatsapp, atualizado_em)
               values (%s,'Doce Mell (ficha velha)',%s, now() - interval '5 days') returning id""",
            (CONTA, "+" + NUM13)).fetchone()[0]
        c.execute(
            """insert into prospeccao (conta_id, empresa, whatsapp, atualizado_em)
               values (%s,'Doce Mell (ficha nova)',%s, now()) returning id""",
            (CONTA, "+" + NUM13))
        conv = c.execute(
            """insert into conversas (conta_id, prospeccao_id, contato_ref)
               values (%s,%s,%s) returning id""", (CONTA, antigo, NUM13)).fetchone()[0]
        assert _eco(c, NUM13, "oi", sid="s1") == conv
        c.commit()
        assert c.execute("select count(*) from conversas").fetchone()[0] == 1


def test_prefere_a_conversa_do_lead_a_uma_orfa(pool):
    """Com as duas na mão, a mensagem vai pra conversa que já tem dono no funil —
    é a que o vendedor abre."""
    with pool.connection() as c:
        lead = c.execute(
            """insert into prospeccao (conta_id, empresa, whatsapp) values (%s,'Doce Mell',%s)
               returning id""", (CONTA, "+" + NUM13)).fetchone()[0]
        orfa = c.execute(
            """insert into conversas (conta_id, contato_ref, ultima_msg_em)
               values (%s,%s, now()) returning id""", (CONTA, NUM12)).fetchone()[0]
        do_lead = c.execute(
            """insert into conversas (conta_id, prospeccao_id, contato_ref, ultima_msg_em)
               values (%s,%s,%s, now() - interval '2 days') returning id""",
            (CONTA, lead, NUM13)).fetchone()[0]
        assert _eco(c, NUM13, "oi", sid="s1") == do_lead
        c.commit()
        assert _msgs(c, orfa) == []


# ------------------------------------------------------------------ o resto

def test_mensagem_que_ja_saiu_pelo_zaq_nao_duplica(pool):
    """O Zaq grava no envio e o Baileys ecoa depois — o mesmo provider_sid chega duas
    vezes de propósito."""
    with pool.connection() as c:
        conv = _eco(c, NUM13, "orçamento enviado", sid="MESMO")
        _eco(c, NUM13, "orçamento enviado", sid="MESMO")
        c.commit()
        assert len(_msgs(c, conv)) == 1


def test_eco_sobe_a_conversa_na_caixa(pool):
    """`ultima_msg_em` é a ordem da lista: sem atualizar, a conversa que o vendedor
    acabou de tocar continua lá embaixo."""
    with pool.connection() as c:
        conv = c.execute(
            """insert into conversas (conta_id, contato_ref, ultima_msg_em)
               values (%s,%s, now() - interval '3 days') returning id""",
            (CONTA, NUM13)).fetchone()[0]
        _eco(c, NUM13, "oi", sid="s1")
        c.commit()
        velha = c.execute("select now() - ultima_msg_em < interval '1 minute' from conversas "
                          "where id=%s", (conv,)).fetchone()[0]
        assert velha is True


def test_conta_vizinha_nao_e_tocada(pool):
    """Multi-tenant: o mesmo número existe em outra empresa, e o eco é de uma só."""
    with pool.connection() as c:
        alheia = c.execute(
            "insert into conversas (conta_id, contato_ref) values (99,%s) returning id",
            (NUM13,)).fetchone()[0]
        conv = _eco(c, NUM13, "oi", sid="s1")
        c.commit()
        assert conv != alheia
        assert _msgs(c, alheia) == []


def test_equivalentes_do_numero():
    """A regra em si: só celular BR ganha a segunda forma."""
    assert pp._wa_equivalentes(NUM13) == [NUM13, NUM12]
    assert pp._wa_equivalentes(NUM12) == [NUM12, NUM13]
    assert pp._wa_equivalentes("+55 (86) 99839-2961") == [NUM13, NUM12]
    # fixo (assinante começa em 2-5) não tem nono dígito pra inventar
    assert pp._wa_equivalentes("558632211020") == ["558632211020"]
    # fora do Brasil fica como veio
    assert pp._wa_equivalentes("14155552671") == ["14155552671"]
    assert pp._wa_equivalentes("") == []
