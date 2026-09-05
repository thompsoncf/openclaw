"""O termo aditivo: emenda ao contrato, com os quatro casos reais do dono.

O QUE ESTES TESTES FIXAM, E POR QUÊ

O modelo veio em 04/09/2026 com quatro aditivos JÁ ASSINADOS junto (Gardênia,
Grazielly, Bárbara, Ana Aline). Eles não são ilustração: são a especificação. Os
números deles estão nos testes de `extenso` e de cláusula porque cada um pegou
uma armadilha diferente —

* Bárbara (50→73 convidados por R$ 5.576,43, num contrato de R$ 185/pessoa) prova
  que o valor NÃO se calcula. Se algum dia alguém "melhorar" o módulo somando
  valor por convidado, o teste da diferença digitada quebra.
* Gardênia e Grazielly (20:00→01:00 e 22:00→03:00) provam que festa vira a noite,
  e que o dia do TÉRMINO não é o dia do início.
* Ana Aline (40→50, R$ 1.850,00) é o único caso em que a conta fecha — está aqui
  pra que ninguém confunda "fechou uma vez" com "é regra".

E a cláusula 7 tem teste próprio porque ela é a razão de data e horário serem
blocos separados: quatro conferências que só valem pra data.
"""
import json
import os
from datetime import date, timedelta

import pytest
from psycopg_pool import ConnectionPool

from finance import aditivo as ad

CONTA = 34

_SQL = """
create table nichos (id bigserial primary key, nome text, slug text unique, tipo text);
create table contas (id bigserial primary key, nome text, nome_fantasia text,
  razao_social text, documento text, endereco text, cep text, bairro text,
  cidade text, uf text, telefone text, email_empresa text, logo_url text, cnae text,
  nicho_id bigint references nichos(id), chip_de bigint);
create table membros (id bigserial primary key, conta_id bigint, nome text);
create table pessoas (id bigserial primary key, nome text, cpf text, cnpj text);
create table clientes (id bigserial primary key, dono_id bigint, pessoa_id bigint,
  nome text, endereco text, cep text, cidade text, uf text);
create table eventos_agenda (id bigserial primary key, conta_id bigint,
  membro_id bigint, titulo text, inicio timestamptz, fim timestamptz, local text,
  descricao text, lembrete_min int, status text default 'ativo',
  criado_em timestamptz default now(), tipo text, desfecho text, link_online text,
  prospeccao_id bigint, ics_token text, pre_reserva_ate timestamptz,
  sinal_centavos bigint, tipo_evento text, convidados int,
  hora_sugerida boolean default false, cliente_id bigint,
  sem_cliente boolean default false);
create table orcamentos (id bigserial primary key, conta_id bigint, cliente text,
  empresa text, itens jsonb, cnpj text, endereco text, cep text, cidade text, uf text,
  setup_centavos bigint default 0, mensal_centavos bigint default 0,
  primeiro_ano_centavos bigint default 0, status text default 'rascunho',
  criado_em timestamptz default now(), atualizado_em timestamptz,
  token text, modo text default 'evento', evento jsonb, parcelas jsonb, numero int,
  evento_agenda_id bigint, cliente_id bigint,
  sinal_centavos bigint, sinal_pago_em timestamptz);
create table contratos (id bigserial primary key, conta_id bigint not null,
  numero int not null, orcamento_id bigint,
  status text not null default 'enviado', texto jsonb, valor_centavos bigint,
  assinado_em timestamptz, assinado_por text, assinado_doc text, assinado_ip text,
  rescindido_em timestamptz, rescisao_motivo text, substitui_id bigint, token text,
  criado_em timestamptz default now(), criado_por text default '');
create table titulos (id bigserial primary key, conta_id bigint, tipo text,
  descricao text, contraparte text, valor_centavos bigint, vencimento date,
  categoria text, recorrente boolean default false, status text default 'aberto',
  pago_em timestamptz, criado_em timestamptz default now(), criado_por bigint,
  cliente_id bigint, aprovacao text not null default 'autorizado',
  aprovado_por bigint, aprovado_em timestamptz, aprovacao_motivo text,
  pago_sem_autorizacao boolean not null default false,
  -- 196 e 197 (chegaram na main enquanto este PR estava aberto): `criar_titulo`
  -- passou a escrever nelas, então o fixture precisa tê-las — senão o título do
  -- aditivo falha em silêncio e só o log conta.
  periodicidade text, valor_variavel boolean not null default false,
  acrescimo_centavos int not null default 0, lancamento_acrescimo_id bigint);
create table contrato_modelo (conta_id bigint primary key, clausulas jsonb not null
  default '[]'::jsonb, regras jsonb not null default '{}'::jsonb,
  atualizado_em timestamptz default now(), atualizado_por text default '',
  assinar_antes_do_sinal boolean not null default false);
"""


def _migracao(nome: str) -> str:
    caminho = os.path.join(os.path.dirname(__file__), "..", "db", "migracoes", nome)
    with open(caminho, encoding="utf-8") as f:
        return f.read()


def _migracao_201() -> str:
    """A migração de verdade, lida do arquivo.

    Copiar o DDL pro teste é como as duas cópias de preço que o `finance/contrato`
    existe pra matar: um dia divergem e o teste passa contra um esquema que a
    produção não tem."""
    caminho = os.path.join(os.path.dirname(__file__), "..", "db", "migracoes",
                           "201_contrato_aditivos.sql")
    with open(caminho, encoding="utf-8") as f:
        return f.read()


@pytest.fixture()
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_contrato_aditivo"
    with admin.connection() as c:
        c.autocommit = True
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=3, open=True,
                       kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_SQL)
        c.execute(_migracao_201())
        c.execute(_migracao("203_aditivo_modelo.sql"))
        c.execute("insert into nichos (nome, slug, tipo) values ('Eventos','eventos','servico')")
        c.execute("insert into contas (id, nome, razao_social, documento, nicho_id) "
                  "values (%s,'Prime','M S DE SOUSA JUNIOR FESTAS E EVENTOS',"
                  "'52.752.898/0001-58',(select id from nichos where slug='eventos'))",
                  (CONTA,))
        c.execute("insert into contrato_modelo (conta_id, regras) values (%s, %s::jsonb)",
                  (CONTA, json.dumps({"reagenda_dias": "30", "reagenda_prazo": "180",
                                      "taxa_reagendamento": "10"})))
        c.commit()
    yield p
    p.close()


# O contrato nº 5 da Prime, como está em produção: casamento da Cláudia,
# 15/01/2027, 18:00 às 23:40, 115 convidados, R$ 7.750,00.
def _contrato(pool, *, assinado=True, data="2027-01-15", inicio="18:00",
              fim="23:40", convidados=115, valor=775000, com_agenda=True):
    with pool.connection() as c:
        c.execute("delete from contrato_aditivos")
        c.execute("delete from contratos")
        c.execute("delete from orcamentos")
        c.execute("delete from eventos_agenda")
        c.execute("delete from titulos")
        ev_id = None
        if com_agenda:
            ev_id = c.execute(
                "insert into eventos_agenda (conta_id, titulo, inicio, fim, status) "
                "values (%s,'Casamento — Claudia', %s::timestamptz, %s::timestamptz,"
                "'ativo') returning id",
                (CONTA, f"{data} {inicio}-03", f"{data} {fim}-03")).fetchone()[0]
        oid = c.execute(
            """insert into orcamentos (conta_id, cliente, numero, evento, itens,
                                       primeiro_ano_centavos, status,
                                       evento_agenda_id, cliente_id)
               values (%s,'Claudia Maria Almeida de Carvalho',18,%s::jsonb,
                       %s::jsonb,%s,'fechado',%s,null) returning id""",
            (CONTA, json.dumps({"data": data, "inicio": inicio, "fim": fim,
                                "tipo": "Casamento", "convidados": convidados}),
             json.dumps([{"nome": "LOCAÇÃO CLIMATIZADORES", "qtd": 4},
                         {"nome": "LOCAÇÃO FREEZER", "qtd": 2}]),
             valor, ev_id)).fetchone()[0]
        cid = c.execute(
            """insert into contratos (conta_id, numero, orcamento_id, status,
                                      valor_centavos, assinado_em, assinado_por, token)
               values (%s,5,%s,%s,%s,%s,%s,'tok-ct') returning id""",
            (CONTA, oid, "assinado" if assinado else "enviado", valor,
             "2026-09-02 20:04:34+00" if assinado else None,
             "claudia maria almeida de carvalho" if assinado else None)).fetchone()[0]
        c.commit()
    return {"contrato_id": cid, "orcamento_id": oid, "evento_agenda_id": ev_id}


# ============================================================ por extenso

def test_os_quatro_valores_dos_aditivos_reais_saem_certos():
    # Bárbara, Ana Aline — os totais e as diferenças que o dono já assinou
    assert ad.reais_extenso(1350500) == "treze mil, quinhentos e cinco reais"
    assert ad.reais_extenso(557643) == (
        "cinco mil, quinhentos e setenta e seis reais e quarenta e três centavos")
    assert ad.reais_extenso(925000) == "nove mil, duzentos e cinquenta reais"
    assert ad.reais_extenso(185000) == "mil, oitocentos e cinquenta reais"


def test_as_contagens_de_convidado_dos_casos_reais():
    assert ad.qtd_por_extenso(73) == "73 (setenta e três)"
    assert ad.qtd_por_extenso(50) == "50 (cinquenta)"
    assert ad.qtd_por_extenso(40) == "40 (quarenta)"
    assert ad.qtd_por_extenso(115) == "115 (cento e quinze)"


def test_extenso_nos_cantos_que_costumam_quebrar():
    assert ad.extenso(0) == "zero"
    assert ad.extenso(100) == "cem"          # 'cem' exato
    assert ad.extenso(101) == "cento e um"   # e 'cento' quando tem resto
    assert ad.extenso(1000) == "mil"         # sem 'um mil'
    assert ad.extenso(1005) == "mil e cinco"
    assert ad.extenso(1500) == "mil e quinhentos"
    assert ad.reais_extenso(100) == "um real"
    assert ad.reais_extenso(1) == "um centavo"
    assert ad.reais_extenso(0) == "zero reais"


def test_hora_suja_de_producao_vira_hora_de_documento():
    # os seis formatos medidos nos orçamentos com contrato da Prime em 04/09/2026
    assert ad._hora_limpa("20h30") == "20:30"
    assert ad._hora_limpa("01h") == "01:00"
    assert ad._hora_limpa("22") == "22:00"
    assert ad._hora_limpa("17") == "17:00"
    assert ad._hora_limpa("18:00") == "18:00"
    assert ad._hora_limpa("23:40") == "23:40"


def test_encerramento_24_vira_meia_noite_e_nao_24h():
    # 'encerramento 24' é como orçamento de festa escreve meia-noite. No documento
    # tem que sair 00:00 (com o dia rolando), não '24:00h'.
    assert ad._hora_limpa("24") == "00:00"
    assert ad._hora_limpa("24:00") == "00:00"


def test_o_que_nao_e_hora_volta_como_veio():
    # o campo é texto livre e o dono pode ter escrito algo que vale informação —
    # trocar por vazio apagaria o que ele quis dizer
    assert ad._hora_limpa("a combinar") == "a combinar"
    assert ad._hora_limpa("") == ""


# ============================================================ criar / cancelar

def test_nao_se_adita_contrato_que_ninguem_assinou(pool):
    c = _contrato(pool, assinado=False)
    with pytest.raises(ValueError, match="ainda não foi assinado"):
        ad.criar(pool, CONTA, c["contrato_id"], [{"campo": "convidados",
                                                  "de": 115, "para": 140}])


def test_aditivo_vazio_nao_nasce(pool):
    c = _contrato(pool)
    with pytest.raises(ValueError, match="ao menos uma alteração"):
        ad.criar(pool, CONTA, c["contrato_id"], [])


def test_a_ordem_conta_por_contrato(pool):
    c = _contrato(pool)
    a1 = ad.criar(pool, CONTA, c["contrato_id"],
                  [{"campo": "convidados", "de": 115, "para": 140}])
    assert a1["ordem"] == 1
    assert ad.ordinal(a1["ordem"]) == "1º"
    ad.cancelar(pool, CONTA, a1["id"])
    a2 = ad.criar(pool, CONTA, c["contrato_id"],
                  [{"campo": "convidados", "de": 115, "para": 150}])
    assert a2["ordem"] == 2


def test_so_um_aditivo_esperando_assinatura_por_contrato(pool):
    # dois links vivos pro mesmo contrato é o cliente assinando o errado — e como
    # o aditivo muda data, assinar o errado é o evento no dia errado
    c = _contrato(pool)
    ad.criar(pool, CONTA, c["contrato_id"], [{"campo": "convidados", "de": 115, "para": 140}])
    with pytest.raises(ValueError, match="esperando assinatura"):
        ad.criar(pool, CONTA, c["contrato_id"], [{"campo": "convidados", "de": 115, "para": 150}])


def test_cancelar_libera_pra_fazer_outro_e_mata_o_link(pool):
    c = _contrato(pool)
    a = ad.criar(pool, CONTA, c["contrato_id"], [{"campo": "convidados", "de": 115, "para": 140}])
    assert ad.por_token(pool, a["token"]) is not None
    assert ad.cancelar(pool, CONTA, a["id"]) is True
    assert ad.por_token(pool, a["token"]) is None      # link morto
    assert ad.aberto_do_contrato(pool, CONTA, c["contrato_id"]) is None


def test_assinado_nao_cancela(pool):
    c = _contrato(pool)
    a = ad.criar(pool, CONTA, c["contrato_id"], [{"campo": "convidados", "de": 115, "para": 140}])
    ad.assinar(pool, a["id"], "Claudia", "07714809388", "1.2.3.4")
    assert ad.cancelar(pool, CONTA, a["id"]) is False


def test_o_valor_e_digitado_o_modulo_so_soma(pool):
    # o caso da Bárbara: a diferença não sai de conta por convidado
    c = _contrato(pool, valor=775000)
    a = ad.criar(pool, CONTA, c["contrato_id"],
                 [{"campo": "convidados", "de": 115, "para": 140}],
                 diferenca_centavos=125000, taxa_centavos=90000)
    assert a["valor_antes_centavos"] == 775000
    assert a["valor_novo_centavos"] == 775000 + 125000 + 90000


# ============================================================ cláusula 7

def _daqui(dias):
    return (date.today() + timedelta(days=dias)).isoformat()


def _regra(avisos, n):
    return [a for a in avisos if a["regra"] == n]


def test_clausula_7_aprova_a_mudanca_bem_feita(pool):
    c = _contrato(pool, data=_daqui(133))
    av = ad.conferir_data(pool, CONTA, c["contrato_id"], _daqui(140))
    assert all(a["ok"] for a in av), [a for a in av if not a["ok"]]


def test_7_1_avisa_quando_falta_antecedencia(pool):
    c = _contrato(pool, data=_daqui(10))
    av = ad.conferir_data(pool, CONTA, c["contrato_id"], _daqui(20))
    antecedencia = [a for a in _regra(av, "7.1") if "antecedência" in a["texto"]]
    assert antecedencia and antecedencia[0]["ok"] is False
    assert "30" in antecedencia[0]["texto"]


def test_7_3_avisa_quando_a_nova_data_passa_do_prazo(pool):
    c = _contrato(pool, data=_daqui(60))
    av = ad.conferir_data(pool, CONTA, c["contrato_id"], _daqui(60 + 200))
    r = _regra(av, "7.3")
    assert r and r[0]["ok"] is False and "180" in r[0]["texto"]


def test_7_2_ve_a_agenda_e_diz_com_quem_choca(pool):
    c = _contrato(pool, data=_daqui(133))
    alvo = _daqui(140)
    with pool.connection() as conn:
        conn.execute(
            "insert into eventos_agenda (conta_id, titulo, inicio, fim, status) "
            "values (%s,'Casamento Kelma',%s::timestamptz,%s::timestamptz,'ativo')",
            (CONTA, f"{alvo} 18:00-03", f"{alvo} 23:00-03"))
        conn.commit()
    av = ad.conferir_data(pool, CONTA, c["contrato_id"], alvo)
    r = _regra(av, "7.2")
    assert r and r[0]["ok"] is False and "Kelma" in r[0]["texto"]


def test_7_1_conta_so_aditivo_ASSINADO_que_mudou_data(pool):
    c = _contrato(pool, data=_daqui(133))
    assert ad.quantas_mudaram_data(pool, CONTA, c["contrato_id"]) == 0
    a = ad.criar(pool, CONTA, c["contrato_id"],
                 [{"campo": "data", "de": _daqui(133), "para": _daqui(140)}])
    # em aberto ainda não alterou nada
    assert ad.quantas_mudaram_data(pool, CONTA, c["contrato_id"]) == 0
    ad.assinar(pool, a["id"], "Claudia", "077", "1.2.3.4")
    assert ad.quantas_mudaram_data(pool, CONTA, c["contrato_id"]) == 1
    av = ad.conferir_data(pool, CONTA, c["contrato_id"], _daqui(150))
    primeira = [x for x in _regra(av, "7.1") if "já teve" in x["texto"]]
    assert primeira and primeira[0]["ok"] is False


def test_alterar_so_o_horario_nao_gasta_a_alteracao_de_data(pool):
    # é a razão de data e horário serem blocos separados
    c = _contrato(pool, data=_daqui(133))
    a = ad.criar(pool, CONTA, c["contrato_id"],
                 [{"campo": "horario", "de": {"inicio": "18:00", "fim": "23:40"},
                   "para": {"inicio": "19:00", "fim": "23:40"}}])
    ad.assinar(pool, a["id"], "Claudia", "077", "1.2.3.4")
    assert ad.quantas_mudaram_data(pool, CONTA, c["contrato_id"]) == 0


def test_a_taxa_incide_sobre_o_valor_ATUALIZADO(pool):
    # a palavra é da cláusula 7.2: se o mesmo aditivo acrescenta convidados, a
    # taxa é sobre o total já com o acréscimo
    regras = {"taxa_reagendamento": "10"}
    assert ad.taxa_reagendamento(900000, regras) == 90000
    assert ad.taxa_reagendamento(775000, regras) == 77500


# ============================================================ o documento

def test_so_o_que_mudou_vira_clausula_e_a_numeracao_fecha(pool):
    # nos quatro aditivos reais saíram 1, 2, 2 e 2 cláusulas — nunca as cinco
    c = _contrato(pool)
    est = ad.estado_atual(pool, CONTA, c["contrato_id"])
    a = ad.criar(pool, CONTA, c["contrato_id"],
                 [{"campo": "convidados", "de": 115, "para": 140}])
    cls = ad.clausulas(a, est)
    assert len(cls) == 1
    assert cls[0]["titulo"] == "1. ACRÉSCIMO DE CONVIDADOS"


def test_o_titulo_diz_o_sentido_da_mudanca(pool):
    c = _contrato(pool)
    est = ad.estado_atual(pool, CONTA, c["contrato_id"])
    sobe = ad.clausulas({"alteracoes": [{"campo": "convidados", "de": 115, "para": 140}]}, est)
    desce = ad.clausulas({"alteracoes": [{"campo": "convidados", "de": 115, "para": 90}]}, est)
    assert "ACRÉSCIMO" in sobe[0]["titulo"]
    assert "REDUÇÃO" in desce[0]["titulo"]


def test_mudar_so_a_hora_nao_anuncia_alteracao_de_data(pool):
    c = _contrato(pool)
    est = ad.estado_atual(pool, CONTA, c["contrato_id"])
    cls = ad.clausulas({"alteracoes": [
        {"campo": "horario", "de": {"inicio": "18:00", "fim": "23:40"},
         "para": {"inicio": "19:00", "fim": "23:40"}}]}, est)
    assert cls[0]["titulo"] == "1. ALTERAÇÃO NO HORÁRIO"
    assert "DATA" not in cls[0]["titulo"]


def test_a_festa_que_vira_a_noite_termina_no_dia_seguinte(pool):
    # Gardênia (20:00→01:00, 26→27/09) e Grazielly (22:00→03:00, 30→31/05)
    c = _contrato(pool, data="2026-09-26")
    est = ad.estado_atual(pool, CONTA, c["contrato_id"])
    cls = ad.clausulas({"alteracoes": [
        {"campo": "horario", "de": {"inicio": "18:00", "fim": "22:00"},
         "para": {"inicio": "20:00", "fim": "01:00"}}]}, est)
    corpo = cls[0]["corpo"]
    assert "26/09/2026" in corpo and "27/09/2026" in corpo


def test_a_clausula_de_convidados_cita_o_numero_antigo(pool):
    # "em substituição à quantidade originalmente estabelecida de 115" — é o que
    # torna o documento legível um ano depois, quando o orçamento já mudou
    c = _contrato(pool)
    est = ad.estado_atual(pool, CONTA, c["contrato_id"])
    cls = ad.clausulas({"alteracoes": [{"campo": "convidados", "de": 115, "para": 140}]}, est)
    assert "140 (cento e quarenta)" in cls[0]["corpo"]
    assert "115 (cento e quinze)" in cls[0]["corpo"]


def test_valor_com_taxa_discrimina_as_duas_parcelas(pool):
    c = _contrato(pool)
    est = ad.estado_atual(pool, CONTA, c["contrato_id"])
    a = ad.criar(pool, CONTA, c["contrato_id"],
                 [{"campo": "data", "de": "2027-01-15", "para": "2027-01-22"}],
                 diferenca_centavos=125000, taxa_centavos=90000,
                 forma_pagamento="chave PIX: primeeventosthe@gmail.com",
                 vencimento=date(2026, 12, 20))
    corpo = [c2 for c2 in ad.clausulas(a, est) if "VALOR" in c2["titulo"]][0]["corpo"]
    assert "R$ 2.150,00" in corpo                     # o que o cliente paga
    assert "R$ 9.900,00" in corpo                     # o novo total
    assert "taxa de reagendamento" in corpo
    assert "Cláusula 7.2" in corpo
    assert "20/12/2026" in corpo


def test_valor_sem_taxa_nao_repete_o_mesmo_numero(pool):
    c = _contrato(pool)
    est = ad.estado_atual(pool, CONTA, c["contrato_id"])
    a = ad.criar(pool, CONTA, c["contrato_id"],
                 [{"campo": "convidados", "de": 115, "para": 140}],
                 diferenca_centavos=125000)
    corpo = [c2 for c2 in ad.clausulas(a, est) if "VALOR" in c2["titulo"]][0]["corpo"]
    assert "taxa de reagendamento" not in corpo


def test_o_fecho_descreve_o_que_de_fato_acontece(pool):
    # o papel diz "02 vias" e "02 testemunhas"; no link não existe nenhum dos
    # dois, e 3 dos 4 aditivos reais foram assinados sem testemunha
    assert "eletronicamente" in ad.FECHO
    assert "IP" in ad.FECHO
    assert "testemunha" not in ad.FECHO.lower()
    assert "vias" not in ad.FECHO.lower()


# ============================================================ a gravação de volta

def test_assinar_congela_o_texto_que_o_cliente_leu(pool):
    c = _contrato(pool)
    a = ad.criar(pool, CONTA, c["contrato_id"], [{"campo": "convidados", "de": 115, "para": 140}])
    lidas = [{"titulo": "1. ACRÉSCIMO DE CONVIDADOS", "corpo": "texto que ele leu"}]
    assert ad.assinar(pool, a["id"], "Claudia", "07714809388", "9.9.9.9", lidas) is True
    dep = ad.por_id(pool, CONTA, a["id"])
    assert dep["status"] == "assinado"
    assert dep["texto"] == lidas
    assert dep["assinado_por"] == "Claudia"
    assert dep["assinado_ip"] == "9.9.9.9"


def test_assinar_duas_vezes_nao_duplica_nada(pool):
    c = _contrato(pool)
    a = ad.criar(pool, CONTA, c["contrato_id"],
                 [{"campo": "convidados", "de": 115, "para": 140}],
                 diferenca_centavos=125000, vencimento=date(2026, 12, 20))
    assert ad.assinar(pool, a["id"], "Claudia", "077", "1.1.1.1") is True
    assert ad.assinar(pool, a["id"], "Outro", "999", "2.2.2.2") is False
    with pool.connection() as conn:
        n = conn.execute("select count(*) from titulos").fetchone()[0]
    assert n == 1
    assert ad.por_id(pool, CONTA, a["id"])["assinado_por"] == "Claudia"


def test_assinou_o_orcamento_passa_a_dizer_o_novo(pool):
    c = _contrato(pool)
    a = ad.criar(pool, CONTA, c["contrato_id"], [
        {"campo": "data", "de": "2027-01-15", "para": "2027-01-22"},
        {"campo": "horario", "de": {"inicio": "18:00", "fim": "23:40"},
         "para": {"inicio": "19:00", "fim": "23:40"}},
        {"campo": "convidados", "de": 115, "para": 140}],
        diferenca_centavos=125000)
    ad.assinar(pool, a["id"], "Claudia", "077", "1.1.1.1")
    with pool.connection() as conn:
        ev, total = conn.execute(
            "select evento, primeiro_ano_centavos from orcamentos where id=%s",
            (c["orcamento_id"],)).fetchone()
    assert ev["data"] == "2027-01-22"
    assert ev["inicio"] == "19:00"
    assert ev["convidados"] == 140
    assert total == 775000 + 125000


def test_assinou_a_agenda_se_move_junto(pool):
    # documento dizendo 22 e agenda dizendo 15 é a equipe no dia errado
    c = _contrato(pool)
    a = ad.criar(pool, CONTA, c["contrato_id"],
                 [{"campo": "data", "de": "2027-01-15", "para": "2027-01-22"}])
    ad.assinar(pool, a["id"], "Claudia", "077", "1.1.1.1")
    with pool.connection() as conn:
        ini = conn.execute("select inicio from eventos_agenda where id=%s",
                           (c["evento_agenda_id"],)).fetchone()[0]
    assert ini.date().isoformat() == "2027-01-22"


def test_assinou_a_diferenca_vira_titulo_a_receber(pool):
    c = _contrato(pool)
    a = ad.criar(pool, CONTA, c["contrato_id"],
                 [{"campo": "convidados", "de": 115, "para": 140}],
                 diferenca_centavos=125000, taxa_centavos=90000,
                 vencimento=date(2026, 12, 20))
    ad.assinar(pool, a["id"], "Claudia", "077", "1.1.1.1")
    with pool.connection() as conn:
        t = conn.execute(
            "select tipo, descricao, valor_centavos, vencimento, contraparte, "
            "       status, aprovacao from titulos").fetchone()
    assert t[0] == "receber"
    assert "1º aditivo" in t[1] and "nº 5" in t[1]
    assert t[2] == 215000                       # diferença + taxa, numa cobrança só
    assert t[3] == date(2026, 12, 20)
    assert t[4] == "Claudia Maria Almeida de Carvalho"
    assert t[5] == "aberto"
    # dinheiro ENTRANDO não pede liberação do dono — a regra que subiu em 05/09
    assert t[6] == "autorizado"
    assert ad.por_id(pool, CONTA, a["id"])["titulo_id"] is not None


def test_aditivo_sem_dinheiro_nao_cria_cobranca(pool):
    c = _contrato(pool)
    a = ad.criar(pool, CONTA, c["contrato_id"],
                 [{"campo": "horario", "de": {"inicio": "18:00", "fim": "23:40"},
                   "para": {"inicio": "19:00", "fim": "23:40"}}])
    ad.assinar(pool, a["id"], "Claudia", "077", "1.1.1.1")
    with pool.connection() as conn:
        assert conn.execute("select count(*) from titulos").fetchone()[0] == 0


def test_enquanto_ninguem_assina_nada_muda_no_sistema(pool):
    c = _contrato(pool)
    ad.criar(pool, CONTA, c["contrato_id"], [
        {"campo": "data", "de": "2027-01-15", "para": "2027-01-22"},
        {"campo": "convidados", "de": 115, "para": 140}], diferenca_centavos=125000)
    with pool.connection() as conn:
        ev, total = conn.execute(
            "select evento, primeiro_ano_centavos from orcamentos where id=%s",
            (c["orcamento_id"],)).fetchone()
        ini = conn.execute("select inicio from eventos_agenda where id=%s",
                           (c["evento_agenda_id"],)).fetchone()[0]
        n = conn.execute("select count(*) from titulos").fetchone()[0]
    assert ev["data"] == "2027-01-15" and ev["convidados"] == 115
    assert total == 775000
    assert ini.date().isoformat() == "2027-01-15"
    assert n == 0


def test_o_de_do_segundo_aditivo_e_o_estado_ja_alterado(pool):
    # o 'de' sai do estado corrente, não do texto congelado do contrato
    c = _contrato(pool)
    a1 = ad.criar(pool, CONTA, c["contrato_id"],
                  [{"campo": "convidados", "de": 115, "para": 140}])
    ad.assinar(pool, a1["id"], "Claudia", "077", "1.1.1.1")
    est = ad.estado_atual(pool, CONTA, c["contrato_id"])
    assert est["convidados"] == 140


# ============================================================ o que as telas leem

def test_o_contrato_original_passa_a_avisar_que_foi_alterado(pool):
    c = _contrato(pool)
    assert ad.aviso_no_contrato(pool, CONTA, c["contrato_id"]) is None
    a = ad.criar(pool, CONTA, c["contrato_id"],
                 [{"campo": "convidados", "de": 115, "para": 140}],
                 diferenca_centavos=125000)
    # enviado ainda não altera nada, então não há o que avisar
    assert ad.aviso_no_contrato(pool, CONTA, c["contrato_id"]) is None
    ad.assinar(pool, a["id"], "Claudia", "077", "1.1.1.1")
    av = ad.aviso_no_contrato(pool, CONTA, c["contrato_id"])
    assert av and av["rotulo"] == "1º" and av["quantos"] == 1
    assert av["token"] == a["token"]


def test_a_lista_mostra_o_valor_que_vale_hoje(pool):
    c = _contrato(pool)
    assert ad.valor_vigente(pool, CONTA, c["contrato_id"], padrao=775000) == 775000
    a = ad.criar(pool, CONTA, c["contrato_id"],
                 [{"campo": "convidados", "de": 115, "para": 140}],
                 diferenca_centavos=125000)
    ad.assinar(pool, a["id"], "Claudia", "077", "1.1.1.1")
    assert ad.valor_vigente(pool, CONTA, c["contrato_id"], padrao=775000) == 900000


def test_cancelado_nao_conta_pra_nada(pool):
    c = _contrato(pool)
    a = ad.criar(pool, CONTA, c["contrato_id"],
                 [{"campo": "data", "de": "2027-01-15", "para": "2027-01-22"}],
                 diferenca_centavos=125000)
    ad.cancelar(pool, CONTA, a["id"])
    assert ad.aviso_no_contrato(pool, CONTA, c["contrato_id"]) is None
    assert ad.valor_vigente(pool, CONTA, c["contrato_id"], padrao=775000) == 775000
    assert ad.quantas_mudaram_data(pool, CONTA, c["contrato_id"]) == 0


def test_base_sem_a_201_nao_derruba_a_tela(pool):
    # a tela do contrato tem que abrir mesmo antes de a migração rodar
    c = _contrato(pool)
    with pool.connection() as conn:
        conn.execute("drop table contrato_aditivos")
        conn.commit()
    assert ad.do_contrato(pool, CONTA, c["contrato_id"]) == []
    assert ad.aviso_no_contrato(pool, CONTA, c["contrato_id"]) is None
    assert ad.quantas_mudaram_data(pool, CONTA, c["contrato_id"]) == 0


# ============================================================ o modelo editável

def test_sem_nada_salvo_vale_o_padrao_de_fabrica(pool):
    m = ad.carregar_modelo(pool, CONTA)
    assert m["novo"] is True
    assert m["textos"]["convidados"]["titulo"] == "ACRÉSCIMO DE CONVIDADOS"
    assert m["textos"]["convidados"]["titulo_reduz"] == "REDUÇÃO DE CONVIDADOS"
    assert "{aditivo.convidados}" in m["textos"]["convidados"]["corpo"]


def test_o_texto_do_dono_manda_no_documento(pool):
    """É o pedido inteiro em um teste: ele reescreve, o documento sai com a
    redação dele."""
    c = _contrato(pool)
    ad.salvar_modelo(pool, CONTA, {"convidados": {
        "titulo": "MAIS GENTE NA FESTA",
        "corpo": "Agora vão {aditivo.convidados} pessoas, e não {aditivo.convidados_antes}."}},
        por="dono")
    est = ad.estado_atual(pool, CONTA, c["contrato_id"])
    a = ad.criar(pool, CONTA, c["contrato_id"],
                 [{"campo": "convidados", "de": 115, "para": 140}])
    cls = ad.clausulas(a, est, pool)
    assert cls[0]["titulo"] == "1. MAIS GENTE NA FESTA"
    assert cls[0]["corpo"] == ("Agora vão 140 (cento e quarenta) pessoas, e não "
                               "115 (cento e quinze).")


def test_editar_um_texto_nao_apaga_os_outros_quatro(pool):
    """MESCLA por chave. Substituir o bloco inteiro congelaria a conta na versão
    do dia em que ela clicou Salvar pela primeira vez."""
    ad.salvar_modelo(pool, CONTA, {"data": {"titulo": "MUDANÇA DE DIA",
                                            "corpo": "Fica {aditivo.data}."}})
    m = ad.carregar_modelo(pool, CONTA)
    assert m["textos"]["data"]["titulo"] == "MUDANÇA DE DIA"
    assert m["textos"]["valor"]["corpo"] == ad.MODELO_PADRAO["valor"]["corpo"]
    assert m["novo"] is False


def test_o_titulo_de_reducao_tambem_e_do_dono(pool):
    c = _contrato(pool)
    ad.salvar_modelo(pool, CONTA, {"convidados": {
        "titulo": "SUBIU", "titulo_reduz": "DESCEU",
        "corpo": "de {aditivo.convidados_antes} para {aditivo.convidados}."}})
    est = ad.estado_atual(pool, CONTA, c["contrato_id"])
    sobe = ad.clausulas({"conta_id": CONTA, "ordem": 1,
                         "alteracoes": [{"campo": "convidados", "de": 115, "para": 140}]},
                        est, pool)
    desce = ad.clausulas({"conta_id": CONTA, "ordem": 1,
                          "alteracoes": [{"campo": "convidados", "de": 115, "para": 90}]},
                         est, pool)
    assert sobe[0]["titulo"] == "1. SUBIU"
    assert desce[0]["titulo"] == "1. DESCEU"


def test_campo_escrito_errado_fica_visivel_no_texto(pool):
    """Mesma regra do contrato: o valor que evapora em silêncio é o perigoso.
    A cláusula continuaria gramaticalmente inteira, sem o número que lhe dava
    sentido."""
    c = _contrato(pool)
    ad.salvar_modelo(pool, CONTA, {"convidados": {
        "titulo": "X", "corpo": "são {aditivo.convidadso} pessoas."}})
    est = ad.estado_atual(pool, CONTA, c["contrato_id"])
    cls = ad.clausulas({"conta_id": CONTA, "ordem": 1,
                        "alteracoes": [{"campo": "convidados", "de": 115, "para": 140}]},
                       est, pool)
    assert "{aditivo.convidadso}" in cls[0]["corpo"]


def test_a_paleta_traz_os_campos_do_aditivo_e_os_do_contrato():
    campos = {c["campo"] for c in ad.campos_disponiveis()}
    assert "aditivo.convidados" in campos and "aditivo.data_antes" in campos
    # os de sempre continuam valendo: uma cláusula de aditivo pode citar o cliente
    assert "cliente.nome" in campos and "regra.taxa_reagendamento" in campos


def test_o_padrao_de_fabrica_produz_o_texto_que_ja_estava_no_ar(pool):
    """Rede de segurança da migração pro modelo: quem não editar nada tem que
    continuar recebendo, palavra por palavra, o documento do #632."""
    c = _contrato(pool)
    est = ad.estado_atual(pool, CONTA, c["contrato_id"])
    cls = ad.clausulas({"conta_id": CONTA, "ordem": 1,
                        "alteracoes": [{"campo": "convidados", "de": 115, "para": 140}]}, est)
    assert cls[0]["corpo"] == (
        "O número de convidados para prestação de serviços passa a ser "
        "140 (cento e quarenta) convidados, em substituição à quantidade "
        "originalmente estabelecida de 115 (cento e quinze) convidados.")


# ============================================================ a cláusula avulsa

def test_a_avulsa_entra_no_documento_por_ultimo(pool):
    c = _contrato(pool)
    est = ad.estado_atual(pool, CONTA, c["contrato_id"])
    a = ad.criar(pool, CONTA, c["contrato_id"], [
        {"campo": "convidados", "de": 115, "para": 140},
        {"campo": "avulsa", "titulo": "ALTERAÇÃO NO RESPONSÁVEL PELAS CHAVES",
         "texto": "A retirada passa a ser do Sr. Pedro Yan."}])
    cls = ad.clausulas(a, est, pool)
    assert len(cls) == 2
    assert cls[1]["titulo"] == "2. ALTERAÇÃO NO RESPONSÁVEL PELAS CHAVES"
    assert cls[1]["corpo"] == "A retirada passa a ser do Sr. Pedro Yan."


def test_a_avulsa_nao_passa_por_campo(pool):
    """É texto de gente, não gabarito. Uma chave entre chaves ali é literal —
    e é por isso que ela pode ser livre: não representa número do sistema, então
    não há o que divergir."""
    c = _contrato(pool)
    est = ad.estado_atual(pool, CONTA, c["contrato_id"])
    a = ad.criar(pool, CONTA, c["contrato_id"],
                 [{"campo": "avulsa", "titulo": "T", "texto": "combinado {seja o que for}"}])
    cls = ad.clausulas(a, est, pool)
    assert cls[0]["corpo"] == "combinado {seja o que for}"


def test_a_avulsa_nao_muda_nada_no_sistema(pool):
    c = _contrato(pool)
    a = ad.criar(pool, CONTA, c["contrato_id"],
                 [{"campo": "avulsa", "titulo": "T", "texto": "combinado à parte"}])
    ad.assinar(pool, a["id"], "Claudia", "077", "1.1.1.1")
    with pool.connection() as conn:
        ev, total = conn.execute(
            "select evento, primeiro_ano_centavos from orcamentos where id=%s",
            (c["orcamento_id"],)).fetchone()
        n = conn.execute("select count(*) from titulos").fetchone()[0]
    assert ev["convidados"] == 115 and total == 775000 and n == 0


def test_aditivo_só_com_avulsa_é_aditivo_válido(pool):
    c = _contrato(pool)
    a = ad.criar(pool, CONTA, c["contrato_id"],
                 [{"campo": "avulsa", "titulo": "T", "texto": "x"}])
    assert a["ordem"] == 1 and a["token"]
