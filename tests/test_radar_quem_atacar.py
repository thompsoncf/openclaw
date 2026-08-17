"""O Radar responde "pra quem eu ligo agora" — e errar o balde custa caro.

A tela de campanha diz como a campanha foi. Esta diz o que fazer em seguida, e a
diferença entre os baldes é a diferença entre ligar pra quem quer falar e ligar pra
quem já disse não. A tradução dos botões do WhatsApp mora em prospec_inbound:

    _rot_btn = {"conhecer": ..., "material": ..., "nao": "Agora não"}
    evento = "respondeu" if tipo != "nao" else "clicou"

Ou seja: **"Agora não" grava `clicou`** e os outros dois gravam `respondeu`. Quem
ler `clicou` como interesse inverte a lista inteira — foi o que aconteceu na
primeira leitura destes dados, e por isso existe teste pra isso.

Abertura de e-mail não entra em balde nenhum de propósito (o proxy do Gmail busca o
pixel sozinho: 62 das 69 aberturas desta base vieram em menos de 1 minuto).
"""
import os

import pytest
from psycopg_pool import ConnectionPool

from web import painel_prospeccao as pp

_SQL = """
create table membros (id bigserial primary key, conta_id bigint, nome text, email text);
create table prospeccao (id bigserial primary key, conta_id bigint, vendedor_id bigint,
  empresa text, segmento text, cidade text, uf text, telefone text, whatsapp text,
  email text, temperatura text, ultimo_contato_em timestamptz);
create table campanhas (id bigserial primary key, conta_id bigint, nome text,
  criado_em timestamptz default now());
create table campanha_alvos (id bigserial primary key, campanha_id bigint,
  prospeccao_id bigint, wa_status text, wa_em timestamptz, ultima_msg_em timestamptz);
create table campanha_eventos (id bigserial primary key, campanha_id bigint,
  prospeccao_id bigint, evento text, detalhe text, quando timestamptz default now());
"""


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_radar_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=3, open=True, kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_SQL)
        c.execute("insert into membros (id, conta_id, nome, email) values (7,1,'Vend','v@x.com')")
        c.execute("insert into campanhas (id, conta_id, nome) values (1,1,'Camp A'),(2,1,'Camp B')")
        c.commit()
    yield p
    p.close()


_SEQ = [100]


def _lead(pool, *, camp=1, wa_status=None, eventos=(), vendedor=7, fone="86999990000",
          empresa=None, contatado=False):
    """Um lead com os eventos que ele gerou. `eventos` = [(evento, detalhe), ...]."""
    _SEQ[0] += 1
    pid = _SEQ[0]
    with pool.connection() as c:
        c.execute("""insert into prospeccao (id, conta_id, vendedor_id, empresa, whatsapp,
                       temperatura, ultimo_contato_em)
                     values (%s,1,%s,%s,%s,'frio',%s)""",
                  (pid, vendedor, empresa or f"Lead {pid}", fone,
                   "now()" if contatado else None))
        if contatado:
            c.execute("update prospeccao set ultimo_contato_em=now() where id=%s", (pid,))
        c.execute("""insert into campanha_alvos (campanha_id, prospeccao_id, wa_status, wa_em)
                     values (%s,%s,%s,now())""", (camp, pid, wa_status))
        for ev, det in eventos:
            c.execute("""insert into campanha_eventos (campanha_id, prospeccao_id, evento, detalhe)
                         values (%s,%s,%s,%s)""", (camp, pid, ev, det))
        c.commit()
    return pid


def _radar(pool, **kw):
    with pool.connection() as c:
        return pp._radar_dados(c, 1, kw.pop("membro", None), **kw)


def _balde_de(pool, pid, **kw):
    itens, _ = _radar(pool, **kw)
    for i in itens:
        if i["pid"] == pid:
            return i
    return None


# ------------------------------------------------------------------ os baldes

def test_agora_nao_nao_e_interesse(pool):
    """O erro que inverteria a lista: 'Agora não' chega como evento='clicou'."""
    pid = _lead(pool, wa_status="lido", eventos=[("clicou", "Agora não")])
    assert _balde_de(pool, pid)["balde"] == "nao"


@pytest.mark.parametrize("detalhe", ["Quero te conhecer", "Tenho interesse"])
def test_quem_quer_falar_vai_pro_topo(pool, detalhe):
    pid = _lead(pool, wa_status="lido", eventos=[("respondeu", detalhe)])
    it = _balde_de(pool, pid)
    assert it["balde"] == "mao" and "quer falar" in it["sinal"]


def test_pedir_material_tambem_e_mao(pool):
    pid = _lead(pool, eventos=[("respondeu", "Quero o material")])
    assert _balde_de(pool, pid)["balde"] == "mao"


def test_baixar_material_e_mao(pool):
    pid = _lead(pool, eventos=[("baixou", "")])
    assert _balde_de(pool, pid)["balde"] == "mao"


def test_so_leu_fica_no_meio(pool):
    pid = _lead(pool, wa_status="lido")
    it = _balde_de(pool, pid)
    assert it["balde"] == "leu" and it["sinal"] == "leu no WhatsApp"


def test_entregue_sem_leitura_e_frio(pool):
    pid = _lead(pool, wa_status="entregue")
    it = _balde_de(pool, pid)
    assert it["balde"] == "frio" and it["sinal"] == "sem sinal"


def test_quem_recusou_e_depois_pediu_material_conta_como_mao(pool):
    """Sinal positivo ganha do 'agora não' anterior: a intenção mais nova vale, e
    perder esse lead num balde de 'não ligar' seria o pior erro possível."""
    pid = _lead(pool, wa_status="lido",
                eventos=[("clicou", "Agora não"), ("respondeu", "Quero o material")])
    it = _balde_de(pool, pid)
    assert it["balde"] == "mao"
    # mas o histórico continua visível — o vendedor precisa saber que ele já recusou
    assert "agora não" in it["sinal"]


def test_abertura_de_email_nao_vira_sinal(pool):
    """O proxy do Gmail abre sozinho. Se isto virasse balde, mandaria o vendedor
    ligar pra quem nunca leu nada."""
    pid = _lead(pool, wa_status="entregue", eventos=[("aberto", "")])
    assert _balde_de(pool, pid)["balde"] == "frio"


# --------------------------------------------------------------- escopo e filtros

def test_vendedor_so_ve_a_carteira_dele(pool):
    meu = _lead(pool, vendedor=7, wa_status="lido")
    outro = _lead(pool, vendedor=None, wa_status="lido")
    ids = {i["pid"] for i in _radar(pool, membro=7)[0]}
    assert meu in ids and outro not in ids


def test_filtro_por_campanha(pool):
    a = _lead(pool, camp=1, wa_status="lido")
    b = _lead(pool, camp=2, wa_status="lido")
    ids = {i["pid"] for i in _radar(pool, camp_id=2)[0]}
    assert b in ids and a not in ids


def test_filtro_de_balde_nao_mexe_na_contagem(pool):
    """Os números dos 4 cartões continuam sendo o total — senão, ao filtrar, a tela
    diria que os outros baldes esvaziaram."""
    _lead(pool, wa_status="lido")
    itens, contagem = _radar(pool, balde="mao")
    assert all(i["balde"] == "mao" for i in itens)
    assert contagem["leu"] > 0 and sum(contagem.values()) > len(itens)


# ------------------------------------------------------------------ pra contatar

def test_link_do_whatsapp_sai_com_ddi(pool):
    pid = _lead(pool, fone="(86) 99400-8350", wa_status="lido")
    assert _balde_de(pool, pid)["wa_link"] == "https://wa.me/5586994008350"


def test_numero_ja_com_ddi_nao_ganha_outro(pool):
    pid = _lead(pool, fone="+5586994008350", wa_status="lido")
    assert _balde_de(pool, pid)["wa_link"] == "https://wa.me/5586994008350"


def test_lead_sem_telefone_nao_gera_link_quebrado(pool):
    pid = _lead(pool, fone=None, wa_status="lido")
    assert _balde_de(pool, pid)["wa_link"] == ""


def test_ja_falado_desce_na_lista(pool):
    """Quem já foi contatado sai da frente, mas não some — o vendedor precisa poder
    desfazer se marcou errado."""
    novo = _lead(pool, wa_status="lido")
    falado = _lead(pool, wa_status="lido", contatado=True)
    ordem = [i["pid"] for i in _radar(pool, balde="leu")[0]]
    assert ordem.index(novo) < ordem.index(falado)
    assert _balde_de(pool, falado, balde="leu")["falado_br"]
