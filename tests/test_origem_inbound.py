"""O código do anúncio chega na mensagem e fica carimbado no lead.

Por que existe: a Prime atende num número só, por QR. Anúncio, orgânico e indicação
caem no mesmo WhatsApp, e até 07/09/2026 não havia como separar — a agência media até
"conversas iniciadas" e o resto era digitado à mão numa planilha, uma vez por mês.

O que estes testes protegem, em ordem de quanto custa quebrar:

1. **A mensagem do cliente não pode se perder.** Se limpar o código esvaziasse o
   corpo, a entrada seria descartada lá no wa-qr ("entrada ignorada (sem texto...)").
   Regra 0 da casa.
2. **O primeiro toque não é sobrescrito.** Dois criativos reivindicando a mesma venda
   fariam a soma do painel ficar maior que o faturamento real.
3. **O vendedor não vê o código.** Ele é enfeite de relatório, não recado do cliente.

O parser em si é `tests/test_origem_anuncio.py` — puro, sem banco. Aqui é o encaixe.
"""
import os

import pytest
from psycopg_pool import ConnectionPool

from web import painel_prospeccao as pp

CONTA = 7
NUM = "558694455743"
OUTRO = "558691112222"

_SQL = """
create table prospeccao (id bigserial primary key, conta_id bigint, vendedor_id bigint,
  empresa text, contato text, cnpj text, cpf text, tipo text default 'pj',
  telefone text, whatsapp text, email text, origem text, origem_codigo text,
  status text default 'novo', temperatura text default 'frio', estagio text default 'base',
  atualizado_em timestamptz default now(), criado_em timestamptz default now());
create table prospeccao_atividades (id bigserial primary key, prospeccao_id bigint,
  membro_id bigint, tipo text, resultado text, descricao text,
  agendado_para timestamptz, criado_em timestamptz default now());
create table conversas (id bigserial primary key, conta_id bigint, prospeccao_id bigint,
  canal text default 'whatsapp', contato_ref text, contato_nome text,
  status text default 'aberta', agente_ativo boolean default true,
  responsavel_membro_id bigint, janela_expira_em timestamptz,
  ultima_msg_em timestamptz default now(), criado_em timestamptz default now(),
  chip_id bigint, visto_ate_id bigint);
create table mensagens (id bigserial primary key, conversa_id bigint, canal text,
  direcao text, autor text, texto text, membro_id bigint, provider_sid text,
  status text, criado_em timestamptz default now(),
  midia_ref jsonb, midia_tipo text, midia_meta jsonb, midia_arquivo text,
  midia_guardada_em timestamptz, midia_guardada_por bigint);
create table wa_contatos (conta_id bigint, numero8 text, nome text,
  da_agenda boolean default false, primary key (conta_id, numero8));
create table membros (id bigserial primary key, conta_id bigint, nome text, email text,
  papel text, ativo boolean default true, cockpit_pausado boolean default false,
  whatsapp text);
create table contas (id bigserial primary key, nome text, chip_de bigint);
create table canais_config (conta_id bigint, canal text, rotulo text);
create table distribuicao (conta_id bigint primary key, ativo boolean default false,
  ponteiro int default 0, avisar boolean default true,
  aviso_zap boolean default false, aviso_zap_chip_id bigint, aviso_zap_texto text,
  atualizado_em timestamptz default now());
create table distribuicao_fila (conta_id bigint, membro_id bigint, ordem int default 0,
  primary key (conta_id, membro_id));
create unique index if not exists idx_mensagens_sid_conversa
  on mensagens (conversa_id, provider_sid) where provider_sid is not null;
create unique index if not exists idx_conversas_lead_canal
  on conversas (conta_id, prospeccao_id, canal) where prospeccao_id is not null;
"""


@pytest.fixture()
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_origem_inbound_test"
    with admin.connection() as c:
        c.autocommit = True
        # `with (force)` derruba conexão pendurada de um run morto no meio — sem ele,
        # um único teste travado envenena todos os runs seguintes.
        c.execute(f"drop database if exists {dbname} with (force)")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=3, open=True,
                       kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_SQL)
        c.commit()
    # O aviso ao vendedor sai do caminho: ele roda em thread com `get_pool()`, que
    # exige DATABASE_URL — e a regra 2 proíbe apontar isso pra qualquer banco aqui.
    from finance import distribuicao as _dist
    _avisar, _getpool = _dist.avisar_vendedor, pp.get_pool
    _dist.avisar_vendedor = lambda *a, **k: None
    pp.get_pool = lambda: p
    yield p
    _dist.avisar_vendedor, pp.get_pool = _avisar, _getpool
    p.close()


def _entrega(c, texto, numero=NUM, sid=None):
    return pp._wa_inbound_conversa(c, CONTA, numero, texto, sid or f"SID{abs(hash(texto)) % 10**9}",
                                   "Juliana Marques", False)


def _lead(c, numero=NUM):
    return c.execute(
        r"""select id, origem_codigo from prospeccao where conta_id=%s
             and right(regexp_replace(coalesce(whatsapp,''),'\D','','g'),8)=%s""",
        (CONTA, numero[-8:])).fetchone()


def _textos(c):
    return [r[0] for r in c.execute(
        """select m.texto from mensagens m join conversas cv on cv.id=m.conversa_id
            where cv.conta_id=%s order by m.id""", (CONTA,)).fetchall()]


def _notas(c, lead_id):
    return [r[0] for r in c.execute(
        "select descricao from prospeccao_atividades where prospeccao_id=%s order by id",
        (lead_id,)).fetchall()]


# ------------------------------------------------------------- o caminho feliz

def test_lead_novo_nasce_com_o_codigo_do_anuncio(pool):
    with pool.connection() as c:
        _entrega(c, "Olá! Quero saber sobre o espaço. [#A3]")
        c.commit()
        assert _lead(c)[1] == "A3"


def test_o_vendedor_nao_ve_o_codigo_na_conversa(pool):
    """O código é enfeite de relatório, não recado do cliente."""
    with pool.connection() as c:
        _entrega(c, "Olá! Quero saber sobre o espaço. [#A3]")
        c.commit()
        assert _textos(c) == ["Olá! Quero saber sobre o espaço."]


def test_sem_codigo_o_lead_nasce_sem_origem_e_o_texto_fica_intacto(pool):
    with pool.connection() as c:
        t = "Boa tarde, queria um orçamento para 120 pessoas"
        _entrega(c, t)
        c.commit()
        assert _lead(c)[1] is None
        assert _textos(c) == [t]


# ---------------------------------------------- a mensagem não pode se perder

def test_mensagem_so_com_o_codigo_nao_fica_vazia(pool):
    """Mensagem sem texto é DESCARTADA no wa-qr. Se a limpeza esvaziasse o corpo, a
    entrada do cliente sumiria por causa de um enfeite de relatório — regra 0."""
    with pool.connection() as c:
        _entrega(c, "[#A3]")
        c.commit()
        assert _lead(c)[1] == "A3"
        assert _textos(c) == ["[#A3]"]
        assert _textos(c)[0].strip() != ""


# ------------------------------------------------------------ o primeiro toque

def test_lead_que_ja_existia_sem_codigo_recebe_o_carimbo(pool):
    """Quem já estava na base e agora chega por anúncio ganha a origem: é o primeiro
    toque que a gente conseguiu medir."""
    with pool.connection() as c:
        c.execute("""insert into prospeccao (conta_id, empresa, contato, whatsapp,
                       origem, estagio) values (%s,'Juliana','Juliana',%s,'manual','lead')""",
                  (CONTA, "+" + NUM))
        c.commit()
        _entrega(c, "oi, vim pelo anúncio [#V2]")
        c.commit()
        assert _lead(c)[1] == "V2"


def test_segundo_anuncio_nao_rouba_o_lead_do_primeiro(pool):
    """Se dois criativos pudessem carimbar o mesmo lead, os dois reivindicariam a
    mesma venda e o total do painel ficaria maior que o faturamento de verdade."""
    with pool.connection() as c:
        _entrega(c, "Quero saber do espaço [#A3]", sid="S1")
        c.commit()
        lid = _lead(c)[0]
        _entrega(c, "voltei, ainda tenho interesse [#V2]", sid="S2")
        c.commit()
        assert _lead(c)[1] == "A3", "o segundo anúncio sobrescreveu o primeiro"
        notas = _notas(c, lid)
        assert any("V2" in n and "A3" in n for n in notas), \
            "o retorno por outro anúncio precisa ficar no histórico do lead"


def test_o_mesmo_codigo_de_novo_nao_vira_nota(pool):
    """Cliente que volta pelo mesmo criativo não é 'outro anúncio' — anotar isso
    encheria o histórico de ruído."""
    with pool.connection() as c:
        _entrega(c, "oi [#A3]", sid="S1")
        c.commit()
        lid = _lead(c)[0]
        _entrega(c, "oi de novo [#A3]", sid="S2")
        c.commit()
        assert _notas(c, lid) == []


def test_mensagem_sem_codigo_depois_nao_apaga_a_origem(pool):
    with pool.connection() as c:
        _entrega(c, "quero saber do espaço [#A3]", sid="S1")
        c.commit()
        _entrega(c, "e tem estacionamento?", sid="S2")
        c.commit()
        assert _lead(c)[1] == "A3"


# --------------------------------------------------------------- dois clientes

def test_cada_numero_guarda_a_propria_origem(pool):
    with pool.connection() as c:
        _entrega(c, "oi [#A3]", numero=NUM, sid="S1")
        _entrega(c, "oi [#V2]", numero=OUTRO, sid="S2")
        c.commit()
        assert _lead(c, NUM)[1] == "A3"
        assert _lead(c, OUTRO)[1] == "V2"
