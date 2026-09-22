"""O CHIP PODE MORAR EM OUTRA CONTA — e o ENVIO tem que achar ele.

O caso (dono, 22/09/2026): "a conta 38 está usando o chip da cp liberal, está
conectado mas não consigo mandar mensagem pelo canal de comunicação".

A Liberal é a conta 37 e NÃO tem linha em `canais_config`. O chip dela é a conta
38 ("cp liberal"), ligada por `contas.chip_de = 37`, com `qr:38` ativo e a sessão
conectada. `whatsapp_out._row` procurava só por `conta_id = 37`, não achava nada,
e TODO envio devolvia `sem_numero_empresa`.

O #781 consertou essa mesma pergunta na TELA — o status do chip em
`painel_prospeccao._wa_chip` — e não aqui. Por isso o painel dizia "conectado"
enquanto o envio dizia "sem número da empresa": duas respostas pra mesma pergunta,
em dois lugares.

O eixo que não pode mudar: empresa com chip PRÓPRIO sai pelo dela, como sempre.
"""
import os

import pytest
from psycopg_pool import ConnectionPool

from finance import whatsapp_out as wo

EMPRESA, CHIP, SOZINHA = 37, 38, 34

_SQL = """
create table contas (id bigint primary key, nome text, chip_de bigint);
create table canais_config (id bigserial primary key, conta_id bigint, canal text,
  identificador text, ativo boolean not null default true, token text,
  provedor text not null default 'twilio', wa_phone_id text);
insert into contas values (37,'Liberal Neto',null), (38,'cp liberal',37),
                          (34,'Prime Eventos',null);
-- a Liberal NÃO tem canal; o chip dela é a conta 38
insert into canais_config (conta_id, canal, identificador, provedor)
     values (38,'whatsapp','qr:38','qr'),
            (34,'whatsapp','qr:34','qr');
"""


@pytest.fixture()
def conn():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_envio_chip_filha"
    with admin.connection() as c:
        c.autocommit = True
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    pool = ConnectionPool(url, min_size=1, max_size=2, open=True,
                          kwargs={"prepare_threshold": None})
    with pool.connection() as c:
        c.execute(_SQL)
        c.commit()
    with pool.connection() as c:
        yield c
    pool.close()


# ───────────────────── o defeito que este arquivo existe pra impedir ─────────────────────

def test_a_empresa_sem_canal_proprio_acha_o_chip_da_filha(conn):
    d = wo.preparar(conn, EMPRESA)
    assert d is not None, "era isto: `None` virava sem_numero_empresa em todo envio"
    assert d["provedor"] == "qr"


def test_o_envio_sai_pelo_chip_e_nao_pela_empresa(conn):
    """No 'qr' o `conta_id` do destino É o chip por onde a mensagem sai. Mandar 37
    faria o serviço procurar uma sessão que não existe."""
    d = wo.preparar(conn, EMPRESA)
    assert d["conta_id"] == CHIP
    assert d["empresa_id"] == EMPRESA


def test_a_empresa_passa_a_poder_enviar(conn, monkeypatch):
    """No 'qr' quem diz se dá pra enviar é o SERVIÇO (as variáveis dele), não o
    banco — por isso ele é fingido aqui. O que se testa é que a pergunta CHEGA
    nessa checagem em vez de morrer antes, em "esta conta não tem canal"."""
    from finance import whatsapp_qr as _qr
    monkeypatch.setattr(_qr, "configurado", lambda: True)
    assert wo.configurado_conta(conn, EMPRESA) is True


def test_o_provedor_da_empresa_para_de_vir_vazio(conn):
    """String vazia é o que diz "não tem canal" pra quem pergunta — e era ela que
    fazia a tela oferecer template da API oficial numa conta de QR."""
    assert wo.provedor_da_conta(conn, EMPRESA) == "qr"


# ───────────────────── o que NÃO pode mudar ─────────────────────

def test_empresa_com_chip_proprio_continua_igual(conn):
    d = wo.preparar(conn, SOZINHA)
    assert d["conta_id"] == SOZINHA and d["identificador"] == "qr:34"


def test_o_canal_proprio_vence_o_da_filha(conn):
    """Empresa com chip próprio E conta-chip filha sai pelo DELA."""
    conn.execute("update contas set chip_de=%s where id=%s", (SOZINHA, CHIP))
    conn.execute("insert into canais_config (conta_id, canal, identificador, provedor)"
                 " values (%s,'whatsapp','qr:34b','qr')", (SOZINHA,))
    d = wo.preparar(conn, SOZINHA)
    assert d["conta_id"] == SOZINHA, "o da filha não pode passar na frente"


def test_conta_sem_canal_nenhum_continua_sem(conn):
    """Nem tudo que não acha canal é chip escondido: quem não tem, não tem."""
    conn.execute("insert into contas values (99,'Sem nada',null)")
    assert wo.preparar(conn, 99) is None
    assert wo.configurado_conta(conn, 99) is False
    assert wo.provedor_da_conta(conn, 99) == ""


def test_canal_desligado_da_filha_nao_conta(conn):
    conn.execute("update canais_config set ativo=false where conta_id=%s", (CHIP,))
    assert wo.preparar(conn, EMPRESA) is None


def test_a_filha_de_outra_empresa_nao_serve(conn):
    """O cerco: `chip_de` é o que liga, e ele é por empresa."""
    conn.execute("update contas set chip_de=%s where id=%s", (SOZINHA, CHIP))
    assert wo.preparar(conn, EMPRESA) is None
