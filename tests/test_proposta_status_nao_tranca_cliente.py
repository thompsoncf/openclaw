"""Mover o card no funil não pode trancar o cliente do lado de fora.

O QUE ACONTECEU (produção, Prime Eventos, orçamento nº 20)
A Jacqueline criou a proposta da Kelma às 17:15 e mandou por e-mail às 17:19 e às
17:41. Às 18:47 alguém moveu o card para "Aprovada" no funil. A partir daí a folha
pública passou a dizer:

    ✓ Proposta fechada
    Esta proposta já foi contratada. Fale com a empresa para os próximos passos.

A cliente nunca tinha assinado nada, e o contrato — que nascia só na aprovação
DELA — nunca existiu. O negócio morreu ali, em silêncio.

A CONFUSÃO ERA ENTRE DUAS COISAS QUE SÓ COINCIDEM QUANDO O CLIENTE RESPONDE:

    status       é do FUNIL   — o vendedor move à mão
    aprovada_em  é do CLIENTE — só ele produz

`web/proposta` tratava as duas como uma:

    d["assinada"] = d["status"] in ("aprovada", "fechado")           # escondia o botão
    ... where token=%s and status not in ('aprovada','fechado')      # e recusava o POST

Duas travas para um estado que o cliente nunca produziu — o formulário sumia e a
fechadura ficava trancada por dentro.

O QUE ESTE ARQUIVO PRENDE
1. card movido à mão NÃO tranca: o cliente ainda vê o formulário e ainda assina;
2. 'fechado' continua barrando — ali o negócio virou contrato com títulos;
3. assinatura continua idempotente: assinar duas vezes não sobrescreve a primeira;
4. mover para "Aprovada" faz o contrato nascer, sem forjar a assinatura do cliente.
"""
import json
import os

import pytest
from psycopg_pool import ConnectionPool

from finance import cockpit as ck
from web import proposta as pp

CONTA = 34

_SQL = """
create table nichos (id bigserial primary key, nome text, slug text unique, tipo text);
-- as colunas de EMPRESA que `contrato.criar_para_orcamento` lê pra qualificar a
-- contratada no documento. Sem elas o contrato falha — e o teste passaria medindo
-- a tolerância do `mudar_status`, não a criação.
create table contas (id bigserial primary key, nome text, nicho_id bigint, chip_de bigint,
  nome_fantasia text, razao_social text, documento text, endereco text, bairro text,
  cep text, cidade text, uf text, telefone text, email_empresa text, logo_url text,
  cnae text);
create table membros (id bigserial primary key, conta_id bigint, nome text);
-- as colunas que a FOLHA PÚBLICA lê pra montar a proposta (web/proposta._carregar):
-- contato, endereço e o vínculo com a agenda. O teste renderiza o HTML de verdade,
-- então precisa da mesma forma que produção tem.
create table orcamentos (id bigserial primary key, conta_id bigint, cliente text,
  empresa text, segmento text, escopo text, itens jsonb, parcelas jsonb,
  whatsapp text, email text, telefone text, cnpj text,
  endereco text, cep text, cidade text, uf text,
  setup_centavos bigint default 0, mensal_centavos bigint default 0,
  primeiro_ano_centavos bigint default 0,
  status text default 'rascunho', criado_por text, token text, numero int,
  modo text default 'evento', evento jsonb, evento_agenda_id bigint, cliente_id bigint,
  aprovada_em timestamptz, aprovada_por text, aprovada_doc text, aprovada_ip text,
  sinal_centavos bigint, sinal_pago_em timestamptz,
  desconto_tipo text not null default 'pct',
  desconto_pct numeric(5,2) not null default 0,
  desconto_centavos bigint not null default 0,
  criado_em timestamptz default now(), atualizado_em timestamptz default now());
create table eventos_agenda (id bigserial primary key, conta_id bigint, status text,
  pre_reserva_ate timestamptz);
create table clientes (id bigserial primary key, dono_id bigint, pessoa_id bigint,
  nome text, endereco text, cep text, cidade text, uf text, telefone text, email text);
create table pessoas (id bigserial primary key, nome text, cpf text, cnpj text);
create table contratos (id bigserial primary key, conta_id bigint, numero int,
  orcamento_id bigint, status text default 'enviado', texto jsonb,
  valor_centavos bigint, assinado_em timestamptz, assinado_por text,
  assinado_doc text, assinado_ip text, rescindido_em timestamptz,
  rescisao_motivo text, substitui_id bigint, token text,
  criado_em timestamptz default now(), criado_por text default '', enviado_em timestamptz);
create unique index ux_ct_conta_numero on contratos (conta_id, numero);
create table contrato_modelo (conta_id bigint primary key, clausulas jsonb not null
  default '[]'::jsonb, regras jsonb not null default '{}'::jsonb,
  atualizado_em timestamptz default now(), atualizado_por text default '',
  assinar_antes_do_sinal boolean not null default false);
"""

TOKEN = "8CrOgy9JRoJYet8ZTKpwYw"   # o token real do caso, pra o teste falar dele


@pytest.fixture()
def pool():
    dbname = "zaq_status_nao_tranca"
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
        c.execute("insert into nichos (nome, slug, tipo) values ('Eventos','eventos','servico')")
        c.execute("insert into contas (id, nome, nicho_id) values (%s,'Prime',"
                  "(select id from nichos where slug='eventos'))", (CONTA,))
        c.execute("insert into membros (id, conta_id, nome) values (31,%s,'JACQUELINE')", (CONTA,))
        c.commit()
    yield p
    p.close()


@pytest.fixture()
def kelma(pool):
    """A proposta como a Jacqueline a deixou: mandada, esperando a cliente."""
    with pool.connection() as c:
        oid = c.execute(
            """insert into orcamentos (conta_id, cliente, empresa, token, status,
                 setup_centavos, primeiro_ano_centavos, numero, modo, criado_por, evento)
               values (%s,'Kelma Costa da Silva','Kelma Costa da Silva',%s,'enviado',
                 890000,890000,20,'evento','31',
                 '{"data":"2027-01-15","inicio":"18:00","convidados":115}'::jsonb)
               returning id""", (CONTA, TOKEN)).fetchone()[0]
        c.commit()
    return oid


def _estado(pool, oid):
    with pool.connection() as c:
        return c.execute("select status, aprovada_em, aprovada_por from orcamentos "
                         "where id=%s", (oid,)).fetchone()


def _contratos(pool, oid):
    with pool.connection() as c:
        return c.execute("select count(*) from contratos where orcamento_id=%s",
                         (oid,)).fetchone()[0]


def _mover(pool, oid, novo):
    return ck.mudar_status_orcamento(pool, CONTA, oid, novo, membro_id=31)


# ═══════════════ o cliente não é trancado ═══════════════

def test_card_movido_a_mao_nao_impede_o_cliente_de_assinar(pool, kelma):
    """O CASO DA KELMA. Antes, este assinar devolvia False e ela ficava de fora."""
    _mover(pool, kelma, "aprovada")

    assinou = pp.registrar_assinatura(pool, TOKEN, "Kelma Costa da Silva",
                                      "123.456.789-00", "1.2.3.4")

    assert assinou is True, "o card movido trancou a assinatura do cliente"
    status, em, por = _estado(pool, kelma)
    assert em is not None and por == "Kelma Costa da Silva"


def test_negocio_fechado_continua_barrando(pool, kelma):
    """Trilho: 'fechado' é outra coisa — ali os títulos já existem e não há o que
    assinar. Destravar o card movido não pode ter aberto esta porta também."""
    with pool.connection() as c:
        c.execute("update orcamentos set status='fechado' where id=%s", (kelma,))
        c.commit()
    assert pp.registrar_assinatura(pool, TOKEN, "Kelma", "", "1.2.3.4") is False
    assert _estado(pool, kelma)[1] is None


def test_assinar_duas_vezes_nao_sobrescreve_a_primeira(pool, kelma):
    """A idempotência é do registro: a assinatura que vale é a que o cliente deu
    primeiro, com a data e o IP daquele momento."""
    assert pp.registrar_assinatura(pool, TOKEN, "Kelma Costa", "111", "1.1.1.1") is True
    primeiro = _estado(pool, kelma)

    assert pp.registrar_assinatura(pool, TOKEN, "Outra Pessoa", "222", "9.9.9.9") is False
    assert _estado(pool, kelma) == primeiro


def test_sem_nome_nao_assina(pool, kelma):
    assert pp.registrar_assinatura(pool, TOKEN, "   ", "111", "1.1.1.1") is False
    assert _estado(pool, kelma)[1] is None


# ═══════════════ a folha do cliente ═══════════════

class _Req:
    """Só o que a rota lê do request: o IP."""
    headers: dict = {}

    class client:
        host = "203.0.113.7"


def _folha(monkeypatch, pool):
    """A folha do cliente, renderizada de verdade — é o HTML que a Kelma abriu."""
    monkeypatch.setattr(pp, "get_pool", lambda: pool)
    return pp.proposta_publica(_Req(), TOKEN).body.decode()


def test_a_folha_nao_diz_que_foi_contratada_sem_assinatura(pool, kelma, monkeypatch):
    """O SINTOMA EXATO que o dono viu no link da Kelma. Com o card movido à mão e
    sem assinatura, a folha tem que continuar oferecendo o botão de aprovar."""
    _mover(pool, kelma, "aprovada")
    html = _folha(monkeypatch, pool)

    assert "Proposta fechada" not in html
    assert "já foi contratada" not in html
    assert "Aprovar e assinar" in html


def test_depois_de_assinar_a_folha_mostra_quem_assinou(pool, kelma, monkeypatch):
    """Trilho: destravar não pode ter apagado o carimbo de quem assinou de verdade."""
    pp.registrar_assinatura(pool, TOKEN, "Kelma Costa da Silva", "111", "1.1.1.1")
    html = _folha(monkeypatch, pool)

    assert "Aprovar e assinar" not in html
    assert "Kelma Costa da Silva" in html


# ═══════════════ o contrato nasce ═══════════════

def test_mover_pra_aprovada_faz_o_contrato_nascer(pool, kelma):
    """Era o outro lado do buraco: o vendedor marcava aprovada porque o cliente
    disse sim no WhatsApp, e o negócio ficava sem documento nenhum."""
    assert _contratos(pool, kelma) == 0
    r = _mover(pool, kelma, "aprovada")
    assert r["ok"] is True
    assert r["contrato_id"] is not None
    assert _contratos(pool, kelma) == 1


def test_mover_pra_aprovada_nao_forja_a_assinatura_do_cliente(pool, kelma):
    """O vendedor não assina no lugar do cliente. É o que mantém a folha pública
    oferecendo o botão — e o que faz o teste de cima ser possível."""
    _mover(pool, kelma, "aprovada")
    status, em, por = _estado(pool, kelma)
    assert status == "aprovada"
    assert em is None and not por


def test_mover_duas_vezes_nao_gera_dois_contratos(pool, kelma):
    _mover(pool, kelma, "aprovada")
    _mover(pool, kelma, "negociando")
    _mover(pool, kelma, "aprovada")
    assert _contratos(pool, kelma) == 1


def test_o_cliente_assinar_depois_nao_gera_um_segundo(pool, kelma):
    """Os dois caminhos criam o contrato, e os dois são idempotentes — senão a
    proposta que passou pelos dois sairia com dois documentos numerados."""
    _mover(pool, kelma, "aprovada")
    pp.registrar_assinatura(pool, TOKEN, "Kelma Costa da Silva", "111", "1.1.1.1")
    assert _contratos(pool, kelma) == 1


def test_outros_status_nao_criam_contrato(pool, kelma):
    for s in ("negociando", "enviado", "perdido"):
        _mover(pool, kelma, s)
        assert _contratos(pool, kelma) == 0, f"criou contrato ao mover pra {s}"


def test_falha_ao_criar_contrato_nao_desfaz_o_movimento(pool, kelma, monkeypatch):
    """O status é o registro; o contrato é a consequência. Perder o movimento do
    card porque o documento falhou seria trocar um problema por um pior — o
    vendedor veria o card voltar sozinho, sem entender."""
    from finance import contrato as ctr
    def _explode(*a, **k):
        raise RuntimeError("sem banco")
    monkeypatch.setattr(ctr, "criar_para_orcamento", _explode)

    r = _mover(pool, kelma, "aprovada")

    assert r["ok"] is True and r["contrato_id"] is None
    assert _estado(pool, kelma)[0] == "aprovada"


def test_vendedor_nao_move_proposta_de_outro(pool, kelma):
    """Trilho de escopo: o id vem da tela, e tela não é fonte confiável."""
    r = ck.mudar_status_orcamento(pool, CONTA, kelma, "aprovada", membro_id=99)
    assert r["ok"] is False
    assert _contratos(pool, kelma) == 0
