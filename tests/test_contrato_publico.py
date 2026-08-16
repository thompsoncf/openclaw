"""O contrato na folha do cliente: quando aparece, quando pode ser assinado, e
por que o texto congela.

TRÊS REGRAS, e elas são diferentes de propósito:

* **Visível desde a aprovação.** O cliente precisa ler o que vai assinar ANTES de
  pagar. Liberar o texto só depois do sinal criaria o intervalo em que ele já
  pagou e ainda não sabe o que aceitou.
* **Assinável só depois do sinal.** A cláusula da reserva diz que a data só fica
  garantida com a entrada; assinar antes é aceitar um contrato cuja primeira
  obrigação ainda não foi cumprida.
* **Congelado no ato.** O que fica gravado é o texto que o cliente LEU, não uma
  referência ao modelo. Sem isso, o dono editar as cláusulas amanhã reescreveria
  retroativamente o que foi aceito ontem — e nenhum contrato assinado no Zaq se
  sustentaria.

E, como em toda tela deste sistema: as travas são revalidadas no POST. Esconder
o formulário não impede ninguém de montar a chamada.
"""
import json
import os

import pytest
from psycopg_pool import ConnectionPool

from web import proposta as pp

CONTA = 34
TOKEN = "tok-contrato-teste"

_SQL = """
create table nichos (id bigserial primary key, nome text, slug text unique, tipo text);
create table contas (id bigserial primary key, nome text, nome_fantasia text,
  razao_social text, documento text, endereco text, cep text, bairro text,
  cidade text, uf text, telefone text, email_empresa text, logo_url text, cnae text,
  -- o contrato de locação é do nicho de eventos, e agora a CRIAÇÃO dele também
  -- passa por essa porta (contrato.criar_para_orcamento -> tem_contrato)
  nicho_id bigint references nichos(id));
create table membros (id bigserial primary key, conta_id bigint, nome text);
create table eventos_agenda (id bigserial primary key, conta_id bigint, status text,
  pre_reserva_ate timestamptz);
create table orcamentos (id bigserial primary key, conta_id bigint, cliente text,
  empresa text, segmento text, escopo text, itens jsonb, whatsapp text, email text,
  telefone text, cnpj text, endereco text, cep text, cidade text, uf text,
  setup_centavos bigint default 0, mensal_centavos bigint default 0,
  primeiro_ano_centavos bigint default 0, status text default 'rascunho',
  criado_em timestamptz default now(), criado_por text, token text,
  aprovada_em timestamptz, aprovada_por text, aprovada_doc text, aprovada_ip text,
  modo text default 'evento', evento jsonb, parcelas jsonb, numero int,
  evento_agenda_id bigint, cliente_id bigint,
  sinal_centavos bigint, sinal_pago_em timestamptz,
  contrato_texto jsonb, contrato_assinado_em timestamptz,
  contrato_assinado_por text, contrato_assinado_doc text, contrato_assinado_ip text);
create table contrato_modelo (conta_id bigint primary key, clausulas jsonb not null
  default '[]'::jsonb, regras jsonb not null default '{}'::jsonb,
  atualizado_em timestamptz default now(), atualizado_por text default '');
-- 164: o contrato virou documento próprio. As colunas velhas em `orcamentos`
-- continuam acima só pra provar que ninguém as lê mais.
create table contratos (id bigserial primary key, conta_id bigint not null,
  numero int not null, orcamento_id bigint,
  status text not null default 'enviado', texto jsonb, valor_centavos bigint,
  assinado_em timestamptz, assinado_por text, assinado_doc text, assinado_ip text,
  rescindido_em timestamptz, rescisao_motivo text, substitui_id bigint,
  criado_em timestamptz default now(), criado_por text default '');
create unique index ux_ct_conta_numero on contratos (conta_id, numero);
create table servicos_catalogo (id bigserial primary key, conta_id bigint, slug text,
  nome text, descricao text, setup_centavos bigint default 0,
  mensal_centavos bigint default 0, custo_centavos bigint default 0, ordem int default 0,
  categoria text, foto_url text, icone text, ativo boolean default true);
"""


@pytest.fixture()
def pool(monkeypatch):
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_contrato_publico"
    with admin.connection() as c:
        c.autocommit = True
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=3, open=True, kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_SQL)
        c.execute("insert into nichos (nome, slug, tipo) values ('Eventos','eventos','servico')")
        c.execute("insert into contas (id, nome, razao_social, documento, nicho_id) "
                  "values (%s,'Prime','PRIME LTDA','52.752.898/0001-58',"
                  "(select id from nichos where slug='eventos'))", (CONTA,))
        c.execute("""insert into servicos_catalogo (conta_id, slug, nome, setup_centavos)
                     values (%s,'hora-extra','HORA EXTRA',62000)""", (CONTA,))
        c.execute("""insert into contrato_modelo (conta_id, clausulas, regras)
                     values (%s,%s::jsonb,'{}'::jsonb)""",
                  (CONTA, json.dumps([
                      {"titulo": "Cláusula 1", "corpo": "Evento de {cliente.nome} em {evento.data}."},
                      {"titulo": "Cláusula 2", "corpo": "Hora extra: {preco.hora-extra}."}])))
        c.commit()
    monkeypatch.setattr(pp, "get_pool", lambda: p)
    monkeypatch.setattr(pp, "emp_dados", lambda pool, cid: {"nicho": "eventos",
                                                            "razao_social": "PRIME LTDA"})
    yield p
    p.close()


def _orcamento(pool, *, status="aprovada", sinal_pago=False, assinado=False):
    with pool.connection() as c:
        c.execute("delete from contratos")
        c.execute("delete from orcamentos")
        oid = c.execute(
            """insert into orcamentos (conta_id, cliente, empresa, token, status,
                 setup_centavos, numero, evento, modo, sinal_pago_em)
               values (%s,'Thompson','Thompson',%s,%s,890000,27,
                 '{"data":"31/12/2026","inicio":"21:00","convidados":50}'::jsonb,'evento',
                 %s) returning id""",
            (CONTA, TOKEN, status,
             "2026-08-16 12:00:00+00" if sinal_pago else None)).fetchone()[0]
        # o CONTRATO agora é linha própria (164), criada quando o sinal cai.
        if sinal_pago or assinado:
            c.execute(
                """insert into contratos (conta_id, numero, orcamento_id, status,
                     texto, assinado_em, assinado_por)
                   values (%s,1,%s,%s,%s::jsonb,%s,%s)""",
                (CONTA, oid, "assinado" if assinado else "enviado",
                 json.dumps([{"titulo": "CONGELADA", "corpo": "texto de ontem"}]) if assinado else None,
                 "2026-08-16 13:00:00+00" if assinado else None,
                 "Thompson Ferreira" if assinado else None))
        c.commit()


def _ct(pool):
    return pp._contrato_da_proposta(pp._carregar(TOKEN, pool), pool)


# ------------------------------------------------------ visível desde a aprovação

def test_proposta_nao_aprovada_ainda_nao_mostra_contrato(pool):
    _orcamento(pool, status="enviado")
    assert _ct(pool)["aprovada"] is False


def test_aprovada_sem_sinal_ja_mostra_o_texto_inteiro(pool):
    """A regra que evita o cliente pagar antes de saber o que aceitou."""
    _orcamento(pool, status="aprovada", sinal_pago=False)
    ct = _ct(pool)
    assert ct["aprovada"] is True
    assert ct["sinal_pago"] is False          # ainda não pode assinar…
    assert len(ct["clausulas"]) == 2          # …mas lê tudo
    assert ct["clausulas"][0]["corpo"] == "Evento de Thompson em 31/12/2026."


def test_o_preco_da_clausula_vem_do_catalogo(pool):
    _orcamento(pool)
    assert _ct(pool)["clausulas"][1]["corpo"] == "Hora extra: R$ 620,00."


def test_conta_sem_nicho_de_eventos_nao_tem_contrato(pool, monkeypatch):
    monkeypatch.setattr(pp, "emp_dados", lambda pool, cid: {"nicho": "tecnologia"})
    _orcamento(pool)
    assert _ct(pool) is None


# ------------------------------------------------- assinável só depois do sinal

def test_sem_sinal_a_assinatura_e_recusada(pool):
    """A trava é revalidada no POST: esconder o formulário não impede a chamada."""
    _orcamento(pool, status="aprovada", sinal_pago=False)
    pp.proposta_assinar_contrato(_Req(), TOKEN, nome="Thompson", doc="000", aceite="on")
    assert _ct(pool)["assinado"] is False


def test_proposta_nao_aprovada_nao_assina_nem_com_sinal(pool):
    _orcamento(pool, status="enviado", sinal_pago=True)
    pp.proposta_assinar_contrato(_Req(), TOKEN, nome="Thompson", doc="000", aceite="on")
    assert _ct(pool)["assinado"] is False


def test_com_sinal_pago_assina(pool):
    _orcamento(pool, status="aprovada", sinal_pago=True)
    pp.proposta_assinar_contrato(_Req(), TOKEN, nome="Thompson Ferreira", doc="000", aceite="on")
    ct = _ct(pool)
    assert ct["assinado"] is True
    assert ct["assinado_por"] == "Thompson Ferreira"


def test_sem_marcar_o_aceite_nao_assina(pool):
    """A caixinha é a prova de que leu — vale mais que o clique se um dia a
    assinatura for questionada."""
    _orcamento(pool, status="aprovada", sinal_pago=True)
    pp.proposta_assinar_contrato(_Req(), TOKEN, nome="Thompson", doc="000", aceite="")
    assert _ct(pool)["assinado"] is False


def test_sem_nome_nao_assina(pool):
    _orcamento(pool, status="aprovada", sinal_pago=True)
    pp.proposta_assinar_contrato(_Req(), TOKEN, nome="  ", doc="000", aceite="on")
    assert _ct(pool)["assinado"] is False


# ------------------------------------------------------------ congelado no ato

def test_o_texto_assinado_e_o_que_o_cliente_leu(pool):
    _orcamento(pool, status="aprovada", sinal_pago=True)
    lido = _ct(pool)["clausulas"]
    pp.proposta_assinar_contrato(_Req(), TOKEN, nome="Thompson", doc="000", aceite="on")
    with pool.connection() as c:
        gravado = c.execute(
            """select ct.texto from contratos ct join orcamentos o on o.id = ct.orcamento_id
                where o.token=%s""", (TOKEN,)).fetchone()[0]
    assert gravado == lido


def test_editar_o_modelo_depois_nao_mexe_no_assinado(pool):
    """O teste que sustenta juridicamente tudo isto."""
    _orcamento(pool, status="aprovada", sinal_pago=True)
    pp.proposta_assinar_contrato(_Req(), TOKEN, nome="Thompson", doc="000", aceite="on")
    antes = _ct(pool)["clausulas"]
    with pool.connection() as c:      # o dono reescreve o contrato da empresa
        c.execute("update contrato_modelo set clausulas=%s::jsonb where conta_id=%s",
                  (json.dumps([{"titulo": "NOVA", "corpo": "multa de 90%"}]), CONTA))
        c.commit()
    assert _ct(pool)["clausulas"] == antes


def test_mudar_o_preco_do_catalogo_nao_mexe_no_assinado(pool):
    """Congelar o TEXTO, e não os campos, é o que faz isto valer: depois de
    assinado o número não acompanha mais o catálogo."""
    _orcamento(pool, status="aprovada", sinal_pago=True)
    pp.proposta_assinar_contrato(_Req(), TOKEN, nome="Thompson", doc="000", aceite="on")
    with pool.connection() as c:
        c.execute("update servicos_catalogo set setup_centavos=99900 where conta_id=%s", (CONTA,))
        c.commit()
    assert "R$ 620,00" in _ct(pool)["clausulas"][1]["corpo"]


def test_assinar_duas_vezes_nao_sobrescreve(pool):
    """Duplo clique ou reenvio do formulário não pode trocar quem assinou."""
    _orcamento(pool, status="aprovada", sinal_pago=True)
    pp.proposta_assinar_contrato(_Req(), TOKEN, nome="Primeiro", doc="1", aceite="on")
    pp.proposta_assinar_contrato(_Req(), TOKEN, nome="Segundo", doc="2", aceite="on")
    assert _ct(pool)["assinado_por"] == "Primeiro"


def test_antes_de_assinar_o_texto_acompanha_o_modelo(pool):
    """O oposto do congelamento, e igualmente necessário: enquanto ninguém
    assinou, o cliente tem que ler a versão ATUAL."""
    _orcamento(pool, status="aprovada", sinal_pago=True)
    with pool.connection() as c:
        c.execute("update contrato_modelo set clausulas=%s::jsonb where conta_id=%s",
                  (json.dumps([{"titulo": "Atualizada", "corpo": "texto novo"}]), CONTA))
        c.commit()
    ct = _ct(pool)
    assert ct["clausulas"] == [{"titulo": "Atualizada", "corpo": "texto novo"}]


class _Req:
    """Só o que a rota lê do request: o IP."""
    headers: dict = {}

    class client:
        host = "203.0.113.7"


# ------------------------------------------------- o contrato como DOCUMENTO (164)

def test_contrato_nasce_com_numero_proprio_e_e_idempotente(pool):
    """Série própria, por conta: "Contrato nº 1" e "Orçamento nº 27" sendo o mesmo
    número seria confusão garantida na hora de citar o documento."""
    from finance import contrato as ctr
    _orcamento(pool, status="aprovada")          # sem sinal: nenhum contrato ainda
    with pool.connection() as c:
        oid = c.execute("select id from orcamentos where token=%s", (TOKEN,)).fetchone()[0]
    ct = ctr.criar_para_orcamento(pool, CONTA, oid, valor_centavos=890000)
    assert ct and ct["numero"] == 1 and ct["status"] == "enviado"
    assert ct["orcamento_id"] == oid and ct["valor_centavos"] == 890000
    # de novo: devolve o MESMO, não cria outro
    de_novo = ctr.criar_para_orcamento(pool, CONTA, oid)
    assert de_novo["id"] == ct["id"]
    with pool.connection() as c:
        assert c.execute("select count(*) from contratos").fetchone()[0] == 1


def test_contrato_nao_nasce_fora_do_nicho_de_eventos(pool):
    """Mesma porta do modo do orçamento. Conta de nicho recorrente não tem contrato
    de locação de espaço — teria um de serviço, que é outro documento."""
    from finance import contrato as ctr
    _orcamento(pool, status="aprovada")
    with pool.connection() as c:
        oid = c.execute("select id from orcamentos where token=%s", (TOKEN,)).fetchone()[0]
        c.execute("update contas set nicho_id=null where id=%s", (CONTA,))
        c.commit()
    assert ctr.criar_para_orcamento(pool, CONTA, oid) is None
    with pool.connection() as c:
        c.execute("update contas set nicho_id=(select id from nichos where slug='eventos') "
                  "where id=%s", (CONTA,))
        c.commit()


def test_assinado_do_orcamento_e_a_pergunta_que_trava_a_edicao(pool):
    from finance import contrato as ctr
    _orcamento(pool, status="aprovada", sinal_pago=True)      # cria contrato 'enviado'
    with pool.connection() as c:
        oid = c.execute("select id from orcamentos where token=%s", (TOKEN,)).fetchone()[0]
    assert ctr.assinado_do_orcamento(pool, CONTA, oid) is False
    ct = ctr.por_orcamento(pool, CONTA, oid)
    assert ctr.assinar(pool, CONTA, ct["id"], [{"titulo": "X", "corpo": "y"}],
                       "Thompson", "000", "1.2.3.4") is True
    assert ctr.assinado_do_orcamento(pool, CONTA, oid) is True
    # a segunda assinatura não sobrescreve o texto nem a data
    assert ctr.assinar(pool, CONTA, ct["id"], [{"titulo": "OUTRO", "corpo": "z"}],
                       "Outro", "111", "9.9.9.9") is False
    with pool.connection() as c:
        texto, quem, st = c.execute(
            "select texto, assinado_por, status from contratos where id=%s",
            (ct["id"],)).fetchone()
    assert texto == [{"titulo": "X", "corpo": "y"}] and quem == "Thompson"
    assert st == "assinado"


def test_contrato_de_outra_conta_nao_aparece(pool):
    """Escopo multi-tenant: a busca é por (conta, orçamento)."""
    from finance import contrato as ctr
    _orcamento(pool, status="aprovada", sinal_pago=True)
    with pool.connection() as c:
        oid = c.execute("select id from orcamentos where token=%s", (TOKEN,)).fetchone()[0]
    assert ctr.por_orcamento(pool, CONTA, oid) is not None
    assert ctr.por_orcamento(pool, CONTA + 999, oid) is None
    assert ctr.assinado_do_orcamento(pool, CONTA + 999, oid) is False
