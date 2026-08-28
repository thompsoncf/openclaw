"""Abrir a conversa conta como VER — e ver não é responder.

O PEDIDO (28/08/2026, com print junto)
"E por que o lead não marca como visto, ficou como 'sua vez' sendo que abri até
pra fazer o print."

Ele tinha razão, e o defeito era de desenho, não de código quebrado. Até aqui o
Cockpit sabia responder UMA pergunta — "tem mensagem do cliente esperando
resposta?" — e usava a resposta dela pra três coisas: a bolinha vermelha do card,
o selo "sua vez" e a bolinha do ícone do app. Abrir a conversa, ler tudo e sair
pra pensar deixava o card EXATAMENTE como estava antes de abrir.

E aí o selo para de informar. Um indicador que não muda com o que você faz vira
enfeite: o vendedor deixa de olhar, e no dia em que ele importa ninguém vê.

AS DUAS PERGUNTAS, que este arquivo mantém separadas
  esperando  — o cliente falou e ninguém respondeu ainda. Não muda ao abrir.
  novas      — ...e disto, o que o vendedor ainda NÃO VIU. Zera ao abrir.

Daí os três selos: "sua vez" (novas > 0), "aberto" (viu, não respondeu) e
"respondido" (respondeu). A conversa só sai da fila quando é respondida — abrir
baixa a cobrança, não some com o compromisso.

POR QUE O CORTE É POR ID E NÃO POR TEMPO
Mesma armadilha que o `n_pend` já documentava: `now()` no Postgres é o início da
TRANSAÇÃO, então duas mensagens gravadas juntas nascem com o mesmo instante e um
corte por tempo leva as duas ou nenhuma. `visto_ate_id` guarda o ID da última
mensagem vista (migração 188); id é serial e não empata.
"""
import os

import pytest
from psycopg_pool import ConnectionPool

from finance import cockpit as ck

# O schema vem do test_cockpit: é o mesmo motor sendo exercitado, e duas cópias
# do DDL divergem na primeira coluna que alguém adiciona num arquivo só — que foi
# exatamente como este trabalho começou (a coluna nova quebrou 33 fixtures).
from tests.test_cockpit import _BASE_SQL as _SQL


@pytest.fixture()
def pool():
    dbname = "zaq_cockpit_visto"
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1,
                           open=True, kwargs={"autocommit": True, "prepare_threshold": None})
    with admin.connection() as c:
        c.execute("select pg_terminate_backend(pid) from pg_stat_activity "
                  "where datname=%s and pid <> pg_backend_pid()", (dbname,))
        c.execute(f"drop database if exists {dbname}")
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


@pytest.fixture()
def cena(pool):
    """Uma conversa com o bot pausado e duas mensagens do cliente sem resposta."""
    with pool.connection() as c:
        conta = c.execute("insert into contas (nome) values ('Emp') returning id").fetchone()[0]
        vend = c.execute("insert into membros (conta_id, nome) values (%s,'Rob') returning id",
                         (conta,)).fetchone()[0]
        lead = c.execute("""insert into prospeccao (conta_id, vendedor_id, empresa, whatsapp)
                            values (%s,%s,'Padaria','5586999990000') returning id""",
                         (conta, vend)).fetchone()[0]
        conv = c.execute("""insert into conversas (conta_id, prospeccao_id, canal, agente_ativo)
                            values (%s,%s,'whatsapp',false) returning id""",
                         (conta, lead)).fetchone()[0]
        c.commit()
    _entra(pool, conv, "Oi")
    _entra(pool, conv, "Tem para sábado?")
    return {"pool": pool, "conta": conta, "vend": vend, "lead": lead, "conv": conv}


def _entra(pool, conv, texto):
    with pool.connection() as c:
        c.execute("""insert into mensagens (conversa_id, canal, direcao, autor, texto)
                     values (%s,'whatsapp','in','lead',%s)""", (conv, texto))
        c.commit()


def _sai(pool, conv, texto="respondendo"):
    with pool.connection() as c:
        c.execute("""insert into mensagens (conversa_id, canal, direcao, autor, texto)
                     values (%s,'whatsapp','out','humano',%s)""", (conv, texto))
        c.commit()


def _abrir(cena):
    """O que a TELA do lead faz — é o `pos_visto=True` que carimba o visto."""
    return ck.lead_do_vendedor(cena["pool"], cena["conta"], cena["vend"], cena["lead"],
                               pos_visto=True)


def _card(cena):
    fila = ck.leads_do_vendedor(cena["pool"], cena["conta"], cena["vend"])
    return next(l for l in fila if l["id"] == cena["lead"])


# ------------------------------------------------------- antes de abrir, nada muda

def test_antes_de_abrir_e_sua_vez(cena):
    c = _card(cena)
    assert c["sua_vez"] is True
    assert c["pend"] == 2, "as duas do cliente contam como novas"
    assert c["esperando"] == 2
    assert c["aberto"] is False and c["respondido"] is False


def test_a_bolinha_do_icone_conta_as_duas(cena):
    assert ck.total_pendentes(cena["pool"], cena["conta"], cena["vend"]) == 2


# --------------------------------------------------------------- abrir é o ato

def test_abrir_baixa_a_bolinha_mas_nao_some_com_a_conversa(cena):
    """O ponto todo. A cobrança cai; o compromisso fica."""
    _abrir(cena)
    c = _card(cena)
    assert c["pend"] == 0, "ele viu"
    assert c["esperando"] == 2, "mas o cliente continua esperando"
    assert c["sua_vez"] is False
    assert c["aberto"] is True, "o selo do meio, que antes não existia"
    assert c["respondido"] is False, "ainda não respondeu — dizer isso seria mentir"


def test_abrir_zera_a_bolinha_do_icone_do_app(cena):
    """Ícone marcado depois de o vendedor abrir e ler tudo vira ruído — e ícone que
    é ruído deixa de ser olhado, justamente antes de importar."""
    _abrir(cena)
    assert ck.total_pendentes(cena["pool"], cena["conta"], cena["vend"]) == 0


def test_mensagem_nova_depois_de_aberta_volta_a_cobrar(cena):
    """Sem isto o 'visto' viraria um silenciador: bastava abrir uma vez pra conversa
    nunca mais acender."""
    _abrir(cena)
    _entra(cena["pool"], cena["conv"], "e o preço?")
    c = _card(cena)
    assert c["pend"] == 1 and c["sua_vez"] is True
    assert c["esperando"] == 3


def test_responder_e_o_que_encerra(cena):
    _abrir(cena)
    _sai(cena["pool"], cena["conv"])
    c = _card(cena)
    assert c["esperando"] == 0 and c["pend"] == 0
    assert c["sua_vez"] is False and c["aberto"] is False
    assert c["respondido"] is True


def test_responder_sem_ter_aberto_tambem_encerra(cena):
    """Quem responde pelo celular não passa pela tela — e nem por isso pode ficar
    com o card cobrando pra sempre."""
    _sai(cena["pool"], cena["conv"])
    c = _card(cena)
    assert c["respondido"] is True and c["sua_vez"] is False and c["aberto"] is False


# --------------------------------------------------------- o que não pode andar pra trás

def test_abrir_duas_vezes_nao_ressuscita_a_bolinha(cena):
    """Dois toques quase juntos podem chegar fora de ordem. Sem o `greatest`, o
    segundo — com um max lido antes da mensagem nova — baixaria o marcador."""
    _abrir(cena)
    with cena["pool"].connection() as c:
        alto = c.execute("select visto_ate_id from conversas where id=%s",
                         (cena["conv"],)).fetchone()[0]
        c.execute("update conversas set visto_ate_id=%s where id=%s", (alto, cena["conv"]))
        c.commit()
    _abrir(cena)
    with cena["pool"].connection() as c:
        assert c.execute("select visto_ate_id from conversas where id=%s",
                         (cena["conv"],)).fetchone()[0] == alto


def test_o_polling_nao_marca_visto(cena):
    """Celular esquecido com a tela aberta no bolso marcaria como vista a mensagem
    que chegou sem ninguém olhar — some a bolinha exatamente no caso em que ela
    era necessária. Abrir é um ato; ficar aberto não é."""
    ck.lead_do_vendedor(cena["pool"], cena["conta"], cena["vend"], cena["lead"])
    assert _card(cena)["sua_vez"] is True
    with cena["pool"].connection() as c:
        assert c.execute("select visto_ate_id from conversas where id=%s",
                         (cena["conv"],)).fetchone()[0] is None


def test_conversa_nunca_aberta_se_comporta_como_antes(cena):
    """`visto_ate_id` nulo é o estado de todas as conversas que já existiam. A
    migração não pode ter reescrito o passado de ninguém."""
    with cena["pool"].connection() as c:
        assert c.execute("select visto_ate_id from conversas where id=%s",
                         (cena["conv"],)).fetchone()[0] is None
    c = _card(cena)
    assert c["pend"] == c["esperando"] == 2


# ------------------------------------------------------------------ a fila recarrega

def test_o_sinal_da_fila_muda_ao_abrir(cena):
    """Abrir MUDA a fila (a bolinha baixa, o selo vira 'aberto') sem criar mensagem
    nenhuma. Se o sinal não mudasse, a lista mostraria o estado velho até a próxima
    mensagem chegar — e o vendedor veria o card que acabou de ler ainda cobrando."""
    antes = ck.sinal_fila(cena["pool"], cena["conta"], cena["vend"])
    _abrir(cena)
    assert ck.sinal_fila(cena["pool"], cena["conta"], cena["vend"]) != antes


# ------------------------------------------------------------------- o agente na frente

def test_com_o_agente_ligado_o_selo_continua_sendo_IA(cena):
    """Nada disto vale enquanto o bot atende: quem está em campo é ele."""
    with cena["pool"].connection() as c:
        c.execute("update conversas set agente_ativo=true where id=%s", (cena["conv"],))
        c.commit()
    card = _card(cena)
    assert card["sua_vez"] is False and card["aberto"] is False and card["respondido"] is False
