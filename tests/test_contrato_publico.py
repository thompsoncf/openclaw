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
create table contas (id bigserial primary key, nome text, nome_fantasia text,
  razao_social text, documento text, endereco text, cep text, bairro text,
  cidade text, uf text, telefone text, email_empresa text, logo_url text);
create table membros (id bigserial primary key, conta_id bigint, nome text);
create table eventos_agenda (id bigserial primary key, conta_id bigint, status text,
  pre_reserva_ate timestamptz);
create table orcamentos (id bigserial primary key, conta_id bigint, cliente text,
  empresa text, segmento text, escopo text, itens jsonb, whatsapp text, email text,
  telefone text, cnpj text, endereco text, cep text, cidade text, uf text,
  setup_centavos bigint default 0, mensal_centavos bigint default 0,
  primeiro_ano_centavos bigint default 0, status text default 'rascunho',
  criado_em timestamptz default now(), atualizado_em timestamptz default now(),
  criado_por text, token text,
  aprovada_em timestamptz, aprovada_por text, aprovada_doc text, aprovada_ip text,
  modo text default 'evento', evento jsonb, parcelas jsonb, numero int,
  evento_agenda_id bigint, cliente_id bigint,
  sinal_centavos bigint, sinal_pago_em timestamptz,
  contrato_texto jsonb, contrato_assinado_em timestamptz,
  contrato_assinado_por text, contrato_assinado_doc text, contrato_assinado_ip text,
  contrato_precos jsonb);
create table contrato_modelo (conta_id bigint primary key, clausulas jsonb not null
  default '[]'::jsonb, regras jsonb not null default '{}'::jsonb,
  atualizado_em timestamptz default now(), atualizado_por text default '');
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
        c.execute("insert into contas (id, nome, razao_social, documento) "
                  "values (%s,'Prime','PRIME LTDA','52.752.898/0001-58')", (CONTA,))
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
        c.execute("delete from orcamentos")
        c.execute(
            """insert into orcamentos (conta_id, cliente, empresa, token, status,
                 setup_centavos, numero, evento, modo, sinal_pago_em,
                 contrato_texto, contrato_assinado_em, contrato_assinado_por)
               values (%s,'Thompson','Thompson',%s,%s,890000,27,
                 '{"data":"31/12/2026","inicio":"21:00","convidados":50}'::jsonb,'evento',
                 %s,%s::jsonb,%s,%s)""",
            (CONTA, TOKEN, status,
             "2026-08-16 12:00:00+00" if sinal_pago else None,
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
        gravado = c.execute("select contrato_texto from orcamentos where token=%s",
                            (TOKEN,)).fetchone()[0]
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


# ------------------------------------- a foto dos preços, tirada na aprovação
#
# O contrato monta {preco.hora-extra} lendo o catálogo — é o que faz ele nunca
# discordar do orçamento. Mas ele lia o catálogo ATUAL até a hora de ser
# assinado, e a assinatura do contrato vem DEPOIS da aprovação da proposta (a
# regra do sinal). Abria esta janela:
#
#   1. orçamento com hora extra R$ 620  → o cliente APROVA (assina a proposta)
#   2. o dono corrige o catálogo: R$ 600
#   3. o cliente paga o sinal
#   4. o cliente ASSINA O CONTRATO      → montava agora, com R$ 600
#
# Dois documentos assinados pelo MESMO cliente, com números diferentes — o
# problema que o contrato-com-campos veio matar, voltando por outra porta.
#
# O momento em que ele aceitou os valores é a APROVAÇÃO. Então a aprovação tira
# uma foto do catálogo, e o contrato lê dela.

def test_aprovar_tira_a_foto_do_catalogo(pool):
    _orcamento(pool, status="enviado")
    pp.registrar_assinatura(pool, TOKEN, "Thompson", "000", "203.0.113.7")
    with pool.connection() as c:
        foto = c.execute("select contrato_precos from orcamentos where token=%s",
                         (TOKEN,)).fetchone()[0]
    assert foto == {"hora-extra": 62000}


def test_mudar_o_preco_depois_de_aprovado_nao_mexe_no_contrato(pool):
    """O furo, fechado. O cliente aprovou vendo R$ 620; é isso que ele assina."""
    _orcamento(pool, status="enviado")
    pp.registrar_assinatura(pool, TOKEN, "Thompson", "000", "203.0.113.7")
    with pool.connection() as c:     # o dono corrige o catálogo depois do aceite
        c.execute("update servicos_catalogo set setup_centavos=60000 where conta_id=%s", (CONTA,))
        c.execute("update orcamentos set sinal_pago_em=now() where token=%s", (TOKEN,))
        c.commit()
    assert "R$ 620,00" in _ct(pool)["clausulas"][1]["corpo"]


def test_orcamento_sem_foto_segue_lendo_o_catalogo(pool):
    """Aprovado antes da migração: inventar uma foto retroativa registraria como
    "aceito pelo cliente" um preço que talvez não fosse o da época."""
    _orcamento(pool, status="aprovada")          # aprovado direto, sem passar pela rota
    with pool.connection() as c:
        assert c.execute("select contrato_precos from orcamentos where token=%s",
                         (TOKEN,)).fetchone()[0] is None
        c.execute("update servicos_catalogo set setup_centavos=99900 where conta_id=%s", (CONTA,))
        c.commit()
    assert "R$ 999,00" in _ct(pool)["clausulas"][1]["corpo"]


def test_a_foto_nao_congela_o_texto_das_clausulas(pool):
    """Dois congelamentos em dois momentos, porque são coisas diferentes: os
    NÚMEROS são o acordo (congelam na aprovação); o TEXTO é da empresa e pode
    melhorar até a assinatura."""
    _orcamento(pool, status="enviado")
    pp.registrar_assinatura(pool, TOKEN, "Thompson", "000", "203.0.113.7")
    with pool.connection() as c:
        c.execute("update contrato_modelo set clausulas=%s::jsonb where conta_id=%s",
                  (json.dumps([{"titulo": "Corrigida", "corpo": "Hora extra: {preco.hora-extra}."}]),
                   CONTA))
        c.commit()
    ct = _ct(pool)
    assert ct["clausulas"][0]["titulo"] == "Corrigida"     # texto novo…
    assert "R$ 620,00" in ct["clausulas"][0]["corpo"]      # …com o preço aceito


class _Req:
    """Só o que a rota lê do request: o IP."""
    headers: dict = {}

    class client:
        host = "203.0.113.7"
