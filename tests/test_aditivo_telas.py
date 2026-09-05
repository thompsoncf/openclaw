"""As duas telas do aditivo: a página do cliente e o formulário do dono.

O QUE ESTES TESTES PROTEGEM, ALÉM DO HTML

1. QUE A TELA APAREÇA PRO VENDEDOR. O dono decidiu que dono, gestor e vendedor
   fazem aditivo. A aprovação de contas a pagar subiu invisível porque foi
   pendurada numa rota que o vendedor não alcança — aqui o teste pergunta ao
   `rotas_do_papel` de verdade, e não à minha lembrança de qual papel abre o quê.

2. QUE O CONTRATO ASSINADO AVISE. A folha do cliente termina com "guarde este
   link: ele é o seu contrato". Sem a tarja, esse link volta a mostrar o número
   velho de convidados depois de o cliente ter assinado a mudança.

3. QUE O BECO VIRE PORTA. Desde a 164 o salvar respondia "faça um aditivo" — e o
   JS da tela escrevia só "Erro ao salvar", então nem a frase chegava. O teste
   fixa que a resposta carrega o caminho.
"""
import json
import os

import pytest
from psycopg_pool import ConnectionPool

from contas import equipe as eq
from finance import aditivo as ad
from web import aditivo_publico as apub
from web import contrato_publico as cp

CONTA = 34

_SQL_BASE = open(os.path.join(os.path.dirname(__file__), "..", "db", "migracoes",
                              "201_contrato_aditivos.sql"), encoding="utf-8").read()

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
  empresa text, segmento text, escopo text, itens jsonb, whatsapp text, email text,
  telefone text, cnpj text, endereco text, cep text, cidade text, uf text,
  setup_centavos bigint default 0, mensal_centavos bigint default 0,
  primeiro_ano_centavos bigint default 0, status text default 'rascunho',
  criado_em timestamptz default now(), atualizado_em timestamptz, criado_por text,
  token text, modo text default 'evento', evento jsonb, parcelas jsonb, numero int,
  evento_agenda_id bigint, cliente_id bigint,
  sinal_centavos bigint, sinal_pago_em timestamptz);
create table contratos (id bigserial primary key, conta_id bigint not null,
  numero int not null, orcamento_id bigint,
  status text not null default 'enviado', texto jsonb, valor_centavos bigint,
  assinado_em timestamptz, assinado_por text, assinado_doc text, assinado_ip text,
  rescindido_em timestamptz, rescisao_motivo text, substitui_id bigint, token text,
  enviado_em timestamptz,
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
create table servicos_catalogo (id bigserial primary key, conta_id bigint, slug text,
  nome text, descricao text, setup_centavos bigint default 0,
  mensal_centavos bigint default 0, custo_centavos bigint default 0, ordem int default 0,
  categoria text, foto_url text, icone text, ativo boolean default true);
"""


def _migracao(nome: str) -> str:
    caminho = os.path.join(os.path.dirname(__file__), "..", "db", "migracoes", nome)
    with open(caminho, encoding="utf-8") as f:
        return f.read()


class _Req:
    """Só o que a rota lê do request: o IP."""
    headers: dict = {}

    class client:
        host = "203.0.113.7"


@pytest.fixture()
def pool(monkeypatch):
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_aditivo_telas"
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
        c.execute(_SQL_BASE)
        c.execute(_migracao("203_aditivo_modelo.sql"))
        c.execute("insert into nichos (nome, slug, tipo) values ('Eventos','eventos','servico')")
        c.execute(
            """insert into contas (id, nome, razao_social, documento, endereco,
                                   bairro, cidade, uf, nicho_id)
               values (%s,'Prime','M S DE SOUSA JUNIOR FESTAS E EVENTOS',
                       '52752898000158','Rua Deoclécio Brito, 3399','Planalto',
                       'Teresina','PI',(select id from nichos where slug='eventos'))""",
            (CONTA,))
        c.execute("insert into contrato_modelo (conta_id, regras) values (%s,%s::jsonb)",
                  (CONTA, json.dumps({"reagenda_dias": "30", "reagenda_prazo": "180",
                                      "taxa_reagendamento": "10"})))
        c.commit()
    monkeypatch.setattr(apub, "get_pool", lambda: p)
    monkeypatch.setattr(cp, "get_pool", lambda: p)
    yield p
    p.close()


def _cenario(pool):
    """O contrato nº 5 da Prime, assinado — o caso real da Cláudia."""
    with pool.connection() as c:
        pid = c.execute("insert into pessoas (nome, cpf) values "
                        "('Claudia Maria Almeida de Carvalho','07714809388') "
                        "returning id").fetchone()[0]
        cli = c.execute(
            """insert into clientes (dono_id, pessoa_id, nome, endereco, cep, cidade, uf)
               values (%s,%s,'Claudia Maria Almeida de Carvalho',
                       'Rua Benjamin Constant · Centro','64000280','Teresina','PI')
               returning id""", (CONTA, pid)).fetchone()[0]
        ev = c.execute(
            "insert into eventos_agenda (conta_id, titulo, inicio, fim, status) "
            "values (%s,'Casamento — Claudia','2027-01-15 18:00-03',"
            "'2027-01-15 23:40-03','ativo') returning id", (CONTA,)).fetchone()[0]
        oid = c.execute(
            """insert into orcamentos (conta_id, empresa, cliente, numero, evento,
                                       itens, primeiro_ano_centavos, status,
                                       evento_agenda_id, cliente_id, parcelas)
               values (%s,'Claudia Maria Almeida de Carvalho',
                       'Claudia Maria Almeida de Carvalho',18,%s::jsonb,%s::jsonb,
                       775000,'fechado',%s,%s,'[]'::jsonb) returning id""",
            (CONTA, json.dumps({"data": "2027-01-15", "inicio": "18:00",
                                "fim": "23:40", "tipo": "Casamento",
                                "convidados": 115}),
             json.dumps([{"nome": "LOCAÇÃO CLIMATIZADORES"},
                         {"nome": "LOCAÇÃO FREEZER"}]), ev, cli)).fetchone()[0]
        cid = c.execute(
            """insert into contratos (conta_id, numero, orcamento_id, status,
                                      valor_centavos, assinado_em, assinado_por, token)
               values (%s,5,%s,'assinado',775000,'2026-09-02 20:04:34+00',
                       'claudia maria almeida de carvalho','tok-ct-5') returning id""",
            (CONTA, oid)).fetchone()[0]
        c.commit()
    return {"contrato_id": cid, "orcamento_id": oid, "evento_agenda_id": ev}


# ==================================================== quem enxerga a tela

def test_o_vendedor_alcanca_a_tela_do_aditivo():
    """A pergunta que a aprovação de contas a pagar não fez.

    Não basta eu achar que `/painel/servicos/aditivo/...` é acessível: o gate de
    `web/app.py` casa por prefixo contra `rotas_do_papel`, então é a ele que o
    teste pergunta."""
    caminho = "/painel/servicos/aditivo/5"
    for papel in ("vendedor", "gestor"):
        permitido = eq.rotas_do_papel(papel)
        assert any(caminho == a or caminho.startswith(a + "/") for a in permitido), papel


def test_o_financeiro_puro_nao_alcanca():
    # quem só cuida de dinheiro não mexe em contrato de venda
    permitido = eq.rotas_do_papel("financeiro")
    caminho = "/painel/servicos/aditivo/5"
    assert not any(caminho == a or caminho.startswith(a + "/") for a in permitido)


# ==================================================== a página do cliente

def test_a_pagina_mostra_o_de_para_e_as_clausulas(pool):
    c = _cenario(pool)
    a = ad.criar(pool, CONTA, c["contrato_id"], [
        {"campo": "data", "de": "2027-01-15", "para": "2027-01-22"},
        {"campo": "convidados", "de": 115, "para": 140}],
        diferenca_centavos=125000, taxa_centavos=90000,
        forma_pagamento="chave PIX: primeeventosthe@gmail.com")
    html = apub.aditivo_publico(_Req(), a["token"]).body.decode()
    # o quadro antes → depois
    assert "O que muda" in html
    assert "140" in html and "115" in html
    # as cláusulas, na ordem do dono
    assert "1. ALTERAÇÃO NA DATA" in html
    assert "2. ACRÉSCIMO DE CONVIDADOS" in html
    assert "3. AJUSTE NO VALOR" in html
    # as partes, com o CPF que só estava no cadastro
    assert "077.148.093-88" in html
    assert "M S DE SOUSA JUNIOR FESTAS E EVENTOS" in html
    # o contrato original citado
    assert "nº 5" in html


def test_a_pagina_nao_promete_via_nem_testemunha(pool):
    c = _cenario(pool)
    a = ad.criar(pool, CONTA, c["contrato_id"],
                 [{"campo": "convidados", "de": 115, "para": 140}])
    html = apub.aditivo_publico(_Req(), a["token"]).body.decode()
    assert "testemunha" not in html.lower()
    assert "02 (Duas) vias" not in html
    assert "eletronicamente" in html


def test_token_que_nao_existe_da_404_com_texto_de_gente(pool):
    r = apub.aditivo_publico(_Req(), "nao-existe")
    assert r.status_code == 404
    assert "não encontrado" in r.body.decode()


def test_aditivo_cancelado_some_do_link(pool):
    c = _cenario(pool)
    a = ad.criar(pool, CONTA, c["contrato_id"],
                 [{"campo": "convidados", "de": 115, "para": 140}])
    ad.cancelar(pool, CONTA, a["id"])
    assert apub.aditivo_publico(_Req(), a["token"]).status_code == 404


def test_assinar_pela_rota_grava_e_aplica(pool):
    c = _cenario(pool)
    a = ad.criar(pool, CONTA, c["contrato_id"],
                 [{"campo": "convidados", "de": 115, "para": 140}],
                 diferenca_centavos=125000)
    apub.aditivo_assinar(_Req(), a["token"], nome="Claudia Maria",
                         doc="07714809388", aceite="on")
    dep = ad.por_id(pool, CONTA, a["id"])
    assert dep["status"] == "assinado"
    assert dep["assinado_ip"] == "203.0.113.7"
    with pool.connection() as conn:
        ev = conn.execute("select evento from orcamentos where id=%s",
                          (c["orcamento_id"],)).fetchone()[0]
    assert ev["convidados"] == 140


def test_sem_aceite_nao_assina(pool):
    c = _cenario(pool)
    a = ad.criar(pool, CONTA, c["contrato_id"],
                 [{"campo": "convidados", "de": 115, "para": 140}])
    apub.aditivo_assinar(_Req(), a["token"], nome="Claudia", doc="077", aceite="")
    assert ad.por_id(pool, CONTA, a["id"])["status"] == "enviado"


def test_sem_nome_nao_assina(pool):
    c = _cenario(pool)
    a = ad.criar(pool, CONTA, c["contrato_id"],
                 [{"campo": "convidados", "de": 115, "para": 140}])
    apub.aditivo_assinar(_Req(), a["token"], nome="  ", doc="077", aceite="on")
    assert ad.por_id(pool, CONTA, a["id"])["status"] == "enviado"


def test_assinado_a_pagina_vira_comprovante(pool):
    c = _cenario(pool)
    a = ad.criar(pool, CONTA, c["contrato_id"],
                 [{"campo": "convidados", "de": 115, "para": 140}])
    apub.aditivo_assinar(_Req(), a["token"], nome="Claudia Maria", doc="077", aceite="on")
    html = apub.aditivo_publico(_Req(), a["token"]).body.decode()
    assert "Termo aditivo assinado" in html
    assert "Assinado eletronicamente por" in html
    assert "Assinar o termo aditivo</button>" not in html


def test_o_texto_congelado_e_o_que_a_pagina_mostra_depois(pool):
    c = _cenario(pool)
    a = ad.criar(pool, CONTA, c["contrato_id"],
                 [{"campo": "convidados", "de": 115, "para": 140}])
    apub.aditivo_assinar(_Req(), a["token"], nome="Claudia", doc="077", aceite="on")
    # muda o mundo por baixo: o orçamento agora diz 140
    html = apub.aditivo_publico(_Req(), a["token"]).body.decode()
    # o documento continua contando a história de 115 -> 140
    assert "115 (cento e quinze)" in html


# ==================================================== a tarja no contrato

def test_o_contrato_assinado_passa_a_avisar_do_aditivo(pool):
    c = _cenario(pool)
    html = cp.contrato_publico(_Req(), "tok-ct-5").body.decode()
    assert "termo aditivo" not in html.lower()

    a = ad.criar(pool, CONTA, c["contrato_id"],
                 [{"campo": "convidados", "de": 115, "para": 140}])
    apub.aditivo_assinar(_Req(), a["token"], nome="Claudia", doc="077", aceite="on")

    html = cp.contrato_publico(_Req(), "tok-ct-5").body.decode()
    assert "alterado pelo" in html
    assert "1º termo aditivo" in html
    assert f"/aditivo/{a['token']}" in html


def test_base_sem_a_201_nao_derruba_a_folha_do_contrato(pool):
    _cenario(pool)
    with pool.connection() as conn:
        conn.execute("drop table contrato_aditivos")
        conn.commit()
    r = cp.contrato_publico(_Req(), "tok-ct-5")
    assert r.status_code == 200


# ==================================================== o beco virou porta

def test_o_409_do_salvar_carrega_o_caminho_do_aditivo():
    """Antes esta resposta dizia "faça um aditivo" e não dizia onde — e o JS que a
    recebia escrevia só "Erro ao salvar"."""
    import inspect
    from web import painel_servicos as ps
    fonte = inspect.getsource(ps)
    assert "aditivo_url" in fonte
    assert "/painel/servicos/aditivo/" in fonte
    # e o front tem que usar o campo, senão volta a engolir a frase
    assert "d.aditivo_url" in fonte


# ==================================================== o formulário do dono

def test_o_formulario_renderiza_os_cinco_blocos_e_escapa_o_nome():
    """A tela do dono não passa pelas rotas nos testes (precisa de sessão), mas o
    template TEM que compilar e escapar.

    O escape importa aqui de verdade: esta tela imprime nome de cliente e nome de
    item vindos do orçamento, que são texto que alguém digitou. Os painéis antigos
    registram template sem extensão e saem crus; este é `.html` justamente pra o
    `select_autoescape` pegar."""
    import web.app  # noqa: F401 — registra os templates
    from web.portal import _env
    html = _env.get_template("painel_aditivo.html").render(
        est={"contrato_id": 8, "contrato_numero": 5, "cliente": "Claudia <b>X</b>",
             "orcamento_numero": 18, "tipo": "Casamento", "data": "2027-01-15",
             "inicio": "18:00", "fim": "23:40", "convidados": 115,
             "valor_centavos": 775000},
        aberto=None, anteriores=[], pct_taxa="10", valor_atual="R$ 7.750,00",
        itens=["LOCAÇÃO CLIMATIZADORES"], erro="",
        logado=True, titulo="Termo aditivo", secao_ativa="servicos",
        caps={"vendas": True, "financeiro": False, "gerir": False}, papel="vendedor",
        n_contextos=0, versao_app="x", ve_novidades=False, conta=None,
        tem_cesta=False, tem_pj=True, vende_produto=False, vende_servico=True,
        beta_gratis=True, plano_aviso=None, empresa_nome="Prime")
    # os cinco blocos, na ordem que o dono deu
    for n, rotulo in ((1, "data"), (2, "horário"), (3, "quantidade de convidados"),
                      (4, "serviços contratados"), (5, "Ajustar o")):
        assert f"{n} · " in html, n
        assert rotulo in html, rotulo
    # a data é a única que anuncia a cláusula 7
    assert "cai na cláusula 7" in html
    assert html.count("cai na cláusula 7") == 1
    # e o nome do cliente sai escapado
    assert "Claudia &lt;b&gt;X&lt;/b&gt;" in html


def test_o_formulario_avisa_quando_ja_tem_aditivo_esperando():
    import web.app  # noqa: F401
    from web.portal import _env
    html = _env.get_template("painel_aditivo.html").render(
        est={"contrato_id": 8, "contrato_numero": 5, "cliente": "Claudia",
             "orcamento_numero": 18, "tipo": "Casamento", "data": "2027-01-15",
             "inicio": "18:00", "fim": "23:40", "convidados": 115,
             "valor_centavos": 775000},
        aberto={"id": 3, "ordem": 1, "token": "tok-ad"}, anteriores=[],
        pct_taxa="10", valor_atual="R$ 7.750,00", itens=[], erro="",
        logado=True, titulo="Termo aditivo", secao_ativa="servicos",
        caps={"vendas": True, "financeiro": False, "gerir": False}, papel="dono",
        n_contextos=0, versao_app="x", ve_novidades=False, conta=None,
        tem_cesta=False, tem_pj=True, vende_produto=False, vende_servico=True,
        beta_gratis=True, plano_aviso=None, empresa_nome="Prime")
    assert "esperando a assinatura" in html
    assert "/aditivo/tok-ad" in html
    assert "Cancelar este aditivo" in html
    # com um aberto, o formulário de criar NÃO aparece: dois links vivos é o
    # cliente assinando o errado
    assert "Gerar aditivo e mandar pro cliente" not in html


def test_o_funil_leva_ao_aditivo_sem_precisar_errar_antes():
    """O caminho de descoberta.

    O 409 do salvar é a rede; o menu do funil é a porta. Quem vai remarcar uma
    festa procura o contrato, não o botão de editar orçamento — se o único acesso
    fosse o erro, metade das pessoas ia direto pro Word de novo."""
    import inspect
    from web import painel_servicos as ps
    fonte = inspect.getsource(ps)
    assert "Fazer termo aditivo" in fonte
    # só depois de assinado, e endereçado por ID (o token é da folha pública)
    assert "it.contrato_assinado && it.contrato_id" in fonte
    assert "'/painel/servicos/aditivo/'+it.contrato_id" in fonte
    assert '"contrato_id": r[27]' in fonte


# ==================================================== o modelo: quem escreve

def test_escrever_o_texto_e_do_dono_mas_fazer_aditivo_e_dos_tres():
    """A distinção que o desenho faz, e que é fácil de errar nos dois sentidos.

    O card do contrato é gateado por `gerir` — só o dono — com o argumento de que
    ele define o que a empresa se compromete a cumprir. O texto do aditivo é a
    mesma natureza. Já FAZER o aditivo continua sendo dos três, senão a tela
    voltaria a nascer invisível pro vendedor."""
    from web import painel_aditivo as pa
    import inspect
    fonte = inspect.getsource(pa)
    # a trava do modelo existe e usa a mesma capacidade do contrato
    assert "_so_o_dono" in fonte and 'caps_do_papel(request.session.get("papel", "dono"))["gerir"]' in fonte
    # e as quatro rotas do modelo passam por ela
    for rota in ("aditivo_modelo", "aditivo_modelo_salvar",
                 "aditivo_modelo_padrao", "aditivo_modelo_previa"):
        corpo = inspect.getsource(getattr(pa, rota))
        assert "_so_o_dono(request)" in corpo, rota
    # enquanto criar/cancelar seguem no gate da aba (os três papéis)
    for rota in ("aditivo_criar", "aditivo_cancelar"):
        assert "_conta_servico(request)" in inspect.getsource(getattr(pa, rota)), rota


def test_o_card_do_aditivo_esta_junto_do_card_do_contrato():
    """Mesmo lugar, mesmo gate — o `pode_contrato` que já é eventos + dono."""
    import inspect
    from web import painel_servicos as ps
    fonte = inspect.getsource(ps)
    assert 'id="ad-card"' in fonte
    assert "Salvar modelo do aditivo" in fonte
    # dentro do mesmo {% if pode_contrato %} do contrato
    trecho = fonte[fonte.index("{% if pode_contrato %}"):fonte.index('id="ad-card"')]
    assert "{% endif %}" not in trecho


def test_o_formulario_tem_o_sexto_bloco_de_texto_livre():
    import web.app  # noqa: F401
    from web.portal import _env
    html = _env.get_template("painel_aditivo.html").render(
        est={"contrato_id": 8, "contrato_numero": 5, "cliente": "Claudia",
             "orcamento_numero": 18, "tipo": "Casamento", "data": "2027-01-15",
             "inicio": "18:00", "fim": "23:40", "convidados": 115,
             "valor_centavos": 775000},
        aberto=None, anteriores=[], pct_taxa="10", valor_atual="R$ 7.750,00",
        itens=[], erro="", logado=True, titulo="Termo aditivo",
        secao_ativa="servicos",
        caps={"vendas": True, "financeiro": False, "gerir": False}, papel="vendedor",
        n_contextos=0, versao_app="x", ve_novidades=False, conta=None,
        tem_cesta=False, tem_pj=True, vende_produto=False, vende_servico=True,
        beta_gratis=True, plano_aviso=None, empresa_nome="Prime")
    assert "6 · " in html and "Outra alteração" in html
    assert 'name="avulsa_titulo"' in html and 'name="avulsa_texto"' in html
    # e diz o que ela NÃO faz — é o que impede alguém esperar efeito dela
    assert "não muda nada no sistema" in html
