"""A proposta que o AGENTE monta também tem que entrar no funil.

O QUE ACONTECEU
Conta 34 (PRIME EVENTOS), 20/08/2026. O chip 2 recebeu a primeira conversa de
verdade, o agente atendeu, montou a proposta 32 às 17:18 e o cliente APROVOU às
17:19 pelo link. No funil não aconteceu nada: o lead 696 continuou parado em
"contatado", com uma proposta aprovada do lado.

Três coisas faltavam no caminho do agente — e só nele; as propostas 29, 30 e 31,
feitas pelo cockpit e pelo app do vendedor no mesmo dia, tinham as três:

 1. ZERO linhas em `orcamento_envios`. O `proposta_email.registrar` é o ponto único
    por onde "a proposta saiu" passa, e o comentário dele já previa a quinta rota
    de envio esquecendo de chamar. O agente era essa quinta rota.
 2. `prospeccao.orcamento_id` NULO. O gatilho `orcamento_enviado` é
    `join orcamentos o on o.id = p.orcamento_id`: sem vínculo não existe card pra
    mover, e não importa que o gatilho esteja ligado — ele estava.
 3. `whatsapp` VAZIO na proposta. Sem o telefone, nem a segunda porta do
    `proposta_lead` (casar lead por telefone) tinha por onde procurar.

E um quarto, que não é do funil mas nasce no mesmo insert: `primeiro_ano_centavos`
ficava no DEFAULT da coluna, que é ZERO. Quem gera título lê
`coalesce(primeiro_ano_centavos, setup_centavos, 0)` — e o coalesce só cai pro
setup quando o campo é NULO. Zero não é nulo: a proposta 32 foi aprovada valendo
R$ 0,00.

POR QUE O VÍNCULO AQUI É DIRETO, E NÃO PELO TELEFONE
O `garantir` procura o lead pelo telefone porque nas outras portas não existe
conversa pra perguntar. Aqui existe: a conversa já sabe de quem é o lead. Amarrar
direto dispensa o palpite — e dispensa o empate, que o `garantir` (com razão) se
recusa a resolver no chute.
"""
import json
import os

import pytest
from psycopg_pool import ConnectionPool

from finance import agente as ag

CONTA = 34
CONVERSA = 2700

_SCHEMA = """
create table contas (id bigint primary key, nome text, nicho_id bigint);
create table nichos (id bigint primary key, slug text);
create table prospeccao (id bigserial primary key, conta_id bigint, vendedor_id bigint,
  empresa text, contato text, cnpj text, whatsapp text, telefone text, email text,
  cidade text, uf text, segmento text, origem text, status text default 'novo',
  estagio text default 'lead', orcamento_id bigint, criado_por bigint,
  atualizado_em timestamptz default now(), criado_em timestamptz default now());
create table orcamentos (id bigserial primary key, conta_id bigint,
  status text default 'rascunho', cliente text, empresa text, cnpj text,
  whatsapp text, telefone text, email text, cidade text, uf text, segmento text,
  modulos jsonb, itens jsonb, escopo text, evento jsonb,
  setup_centavos bigint default 0, mensal_centavos bigint default 0,
  primeiro_ano_centavos bigint default 0, n_modulos int default 0,
  criado_por text, token text, modo text,
  atualizado_em timestamptz default now(), criado_em timestamptz default now());
create table orcamento_envios (id bigserial primary key, conta_id bigint,
  orcamento_id bigint, canal text default 'email', destino text default '',
  remetente text default '', ok boolean default true, erro text default '',
  por text default '', criado_em timestamptz default now());
create table funil_movimentos (id bigserial primary key, conta_id bigint,
  prospeccao_id bigint, de text, para text, motivo text, membro_id bigint,
  criado_em timestamptz default now());
"""


@pytest.fixture()
def pool():
    dbname = "zaq_agente_proposta_funil"
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1,
                           open=True, kwargs={"autocommit": True, "prepare_threshold": None})
    with admin.connection() as c:
        c.execute("select pg_terminate_backend(pid) from pg_stat_activity "
                  "where datname=%s and pid <> pg_backend_pid()", (dbname,))
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=4, open=True,
                       kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_SCHEMA)
        c.execute("insert into contas (id, nome) values (%s,'PRIME EVENTOS')", (CONTA,))
        c.commit()
    yield p
    p.close()


@pytest.fixture(autouse=True)
def sem_whatsapp(monkeypatch):
    """O `_enviar` de verdade abre socket. Aqui só interessa o que fica no banco —
    mas o commit dele é parte do fluxo (o registro do envio abre outra conexão e
    precisa enxergar a proposta), então o dublê commita igual."""
    enviados = []

    def _falso(c, conta_id, conversa_id, canal, destino, texto):
        enviados.append({"canal": canal, "destino": destino, "texto": texto})
        c.commit()

    monkeypatch.setattr(ag, "_enviar", _falso)
    monkeypatch.setattr("finance.email_sender._app_url", lambda: "https://app.zaq-ia.com")
    return enviados


CATALOGO = [{"slug": "pacote", "nome": "PACOTE ESSENCIAL", "descricao": "", "categoria": "",
             "setup_centavos": 780000, "mensal_centavos": 0},
            {"slug": "dj", "nome": "DJ", "descricao": "", "categoria": "",
             "setup_centavos": 150000, "mensal_centavos": 0}]

DECISAO = {"servicos": [{"slug": "pacote", "qtd": 1}, {"slug": "dj", "qtd": 1}],
           "evento": {"data": "31/12/2026", "inicio": "21h", "tipo": "casamento"}}


def _lead(pool, **kw):
    campos = {"empresa": "Joao Pedro Monteiro", "whatsapp": "", "status": "contatado"}
    campos.update(kw)
    with pool.connection() as c:
        lid = c.execute(
            "insert into prospeccao (conta_id, empresa, contato, whatsapp, status, estagio) "
            "values (%s,%s,%s,%s,%s,'lead') returning id",
            (CONTA, campos["empresa"], campos["empresa"], campos["whatsapp"],
             campos["status"])).fetchone()[0]
        c.commit()
    return lid


def _conv(lead_id, telefone="558694426769"):
    """A tupla que o `_atender` monta: agente_ativo, prospeccao_id, contato_ref,
    empresa, whatsapp, telefone, segmento, cidade, uf, canal."""
    return (True, lead_id, telefone, "Joao Pedro Monteiro", telefone, "",
            "", "", "", "whatsapp")


def _rodar(pool, conv, destino="558694426769"):
    with pool.connection() as c:
        ag._orcamento(pool, c, CONTA, CONVERSA, conv, CATALOGO, DECISAO,
                      "whatsapp", destino, "Aqui está o orçamento", {})
        c.commit()
    with pool.connection() as c:
        return c.execute(
            """select id, whatsapp, setup_centavos, primeiro_ano_centavos, status
                 from orcamentos where conta_id=%s order by id desc limit 1""",
            (CONTA,)).fetchone()


# --------------------------------------------------------------- o vínculo

def test_a_proposta_do_agente_amarra_no_lead_da_conversa(pool):
    lead = _lead(pool)
    orc = _rodar(pool, _conv(lead))
    with pool.connection() as c:
        assert c.execute("select orcamento_id from prospeccao where id=%s",
                         (lead,)).fetchone()[0] == orc[0]


def test_e_o_card_anda_pra_proposta(pool):
    lead = _lead(pool)
    _rodar(pool, _conv(lead))
    with pool.connection() as c:
        assert c.execute("select status from prospeccao where id=%s",
                         (lead,)).fetchone()[0] == "proposta"
        mov = c.execute("""select de, para, motivo from funil_movimentos
                            where prospeccao_id=%s""", (lead,)).fetchone()
    assert mov == ("contatado", "proposta", "orcamento")


def test_o_envio_fica_registrado(pool):
    """É `orcamento_envios` que o gatilho `orcamento_enviado` lê enquanto a proposta
    ainda está em rascunho — sem a linha, o funil não tem como saber que ela saiu."""
    lead = _lead(pool)
    orc = _rodar(pool, _conv(lead))
    with pool.connection() as c:
        env = c.execute("""select canal, destino, ok, por from orcamento_envios
                            where orcamento_id=%s""", (orc[0],)).fetchone()
    assert env == ("whatsapp", "558694426769", True, "agente")


def test_desfecho_nao_e_atropelado(pool):
    """Negócio perdido não volta pro meio do funil por causa de uma proposta nova —
    mas o vínculo é feito, senão a proposta some do lead."""
    lead = _lead(pool, status="perdido")
    orc = _rodar(pool, _conv(lead))
    with pool.connection() as c:
        st, oid = c.execute("select status, orcamento_id from prospeccao where id=%s",
                            (lead,)).fetchone()
    assert st == "perdido" and oid == orc[0]


def test_conversa_sem_lead_nao_derruba_o_atendimento(pool):
    """Conversa órfã (o histórico importado cria assim) não tem lead pra amarrar. A
    proposta sai do mesmo jeito — com o telefone gravado, que é o que deixa o
    `garantir` achar o card mais tarde."""
    orc = _rodar(pool, _conv(None))
    assert orc is not None and orc[1] == "558694426769"


# ------------------------------------------------------ os campos da proposta

def test_o_telefone_do_cliente_vai_na_proposta(pool):
    lead = _lead(pool)
    assert _rodar(pool, _conv(lead))[1] == "558694426769"


def test_primeiro_ano_nao_fica_zerado(pool):
    """R$ 7.800 + R$ 1.500 = R$ 9.300. O `coalesce(primeiro_ano, setup, 0)` que gera
    os títulos só cai pro setup quando o campo é NULO — e o default da coluna é
    zero, que não é nulo. Foi assim que a proposta 32 foi aprovada valendo R$ 0,00."""
    lead = _lead(pool)
    orc = _rodar(pool, _conv(lead))
    assert orc[2] == 930000
    assert orc[3] == 930000


def test_o_cliente_recebe_o_link(pool, sem_whatsapp):
    lead = _lead(pool)
    _rodar(pool, _conv(lead))
    assert len(sem_whatsapp) == 1
    assert "https://app.zaq-ia.com/proposta/" in sem_whatsapp[0]["texto"]
