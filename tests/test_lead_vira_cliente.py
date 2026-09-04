"""O lead do funil vira cliente na agenda — a corrente que arrebentava no fim.

A visita marcada pelo funil trazia TUDO o que era preciso e não deixava cadastro
nenhum atrás de si. Medido na conta da Prime em 31/08/2026:

* 7 visitas vindas de lead;
* 7 com nome E WhatsApp no lead;
* 0 com `cliente_id`;
* 0 daquelas pessoas existindo em `clientes`.

O número se perdia entre ler o lead e criar o evento. São três consertos, e cada
um tem seu bloco aqui:

1. `agendar_visita` procura o cadastro pelos 8 dígitos finais do WhatsApp e liga
   quando acha. **Nunca cria** — nome de lead é anotação de vendedora
   ("Jacque/Elisangela 15 Anos 25/11"), e a base de clientes é a que alimenta
   contrato, orçamento e cobrança.
2. O relatório para de fingir que o nome do lead é ficha: ele vira dedução com
   selo próprio e a célula vira clicável. Até aqui a linha *parecia* resolvida
   justamente porque tinha nome — o pior dos dois mundos.
3. A tela de ligar ganha o campo de telefone. Sem ele, confirmar um lead criaria
   ficha SEM número — a mesma perda, agora dentro do conserto dela.
"""
import os
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from psycopg_pool import ConnectionPool
from starlette.middleware.sessions import SessionMiddleware

from finance import agenda as ag
from finance import clientes as cli
from finance import cockpit as ck
from web import painel_relatorios as rel

CONTA = 11
OUTRA = 12
BASE = Path(__file__).resolve().parent.parent / "db" / "migracoes"

_MIGRACOES = ("064_clientes_lojista.sql", "066_pessoas_identidade.sql",
              "131_pessoa_cnpj.sql", "149_cliente_cidade_uf.sql",
              "182_clientes_papel.sql")

# eventos_agenda e prospeccao montados na mão (mesmo padrão do teste do cockpit):
# a agenda tem 15 migrações e aqui só interessam as colunas que estas três
# consertos tocam.
_BASE_SQL = """
create table contas (id bigserial primary key, nome text, nome_fantasia text,
  endereco text, bairro text, cep text, cidade text, uf text, documento text,
  razao_social text, email_empresa text, telefone text);
create table membros (id bigserial primary key, conta_id bigint, nome text,
  email text, papel text default 'vendedor', ativo boolean default true);
create table prospeccao (id bigserial primary key, conta_id bigint, vendedor_id bigint,
  contato text, empresa text, telefone text, whatsapp text,
  status text default 'novo', temperatura text default 'frio',
  ultimo_contato_em timestamptz, atualizado_em timestamptz default now());
create table prospeccao_atividades (id bigserial primary key, prospeccao_id bigint,
  membro_id bigint, tipo text, resultado text, descricao text,
  criado_em timestamptz default now());
create table orcamentos (id bigserial primary key, conta_id bigint,
  cliente text, empresa text, evento_agenda_id bigint, evento jsonb);
create table eventos_agenda (id bigserial primary key, conta_id bigint, membro_id bigint,
  titulo text, inicio timestamptz, fim timestamptz, local text, descricao text,
  lembrete_min int, tipo text default 'pessoal', link_online text, desfecho text,
  status text default 'ativo', criado_em timestamptz default now(),
  prospeccao_id bigint, ics_token text, pre_reserva_ate timestamptz,
  sinal_centavos int, tipo_evento text, convidados int,
  hora_sugerida boolean default false,
  cliente_id bigint, sem_cliente boolean not null default false);
create table funil_etapas (id bigserial primary key, conta_id bigint, chave text,
  rotulo text, ordem int default 0, fixa boolean default false,
  fase text not null default 'venda', unique (conta_id, chave));
create table lancamentos (id bigserial primary key, conta_id bigint);
"""


@pytest.fixture
def http(monkeypatch):
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_lead_vira_cliente"
    with admin.connection() as c:
        c.autocommit = True
        c.execute("select pg_terminate_backend(pid) from pg_stat_activity where datname=%s",
                  (dbname,))
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    pool = ConnectionPool(url, min_size=1, max_size=3, open=True,
                          kwargs={"prepare_threshold": None})
    with pool.connection() as c:
        c.execute(_BASE_SQL)
        for nome in _MIGRACOES:
            c.execute((BASE / nome).read_text(encoding="utf-8"))
        c.execute("alter table clientes add column if not exists endereco text")
        c.execute("alter table clientes add column if not exists cep text")
        c.execute("insert into contas (id, nome, nome_fantasia, endereco, cidade, uf) "
                  "values (%s,'C','Prime Eventus','Av. Fátima, 1200','Teresina','PI')",
                  (CONTA,))
        c.execute("insert into contas (id, nome) values (%s,'Vizinha')", (OUTRA,))
        c.execute("insert into membros (conta_id, nome, email) "
                  "values (%s,'PEDRO YAN PRIME','p@x.com')", (CONTA,))
        c.commit()

    monkeypatch.setattr(rel, "get_pool", lambda: pool)
    monkeypatch.setattr(rel, "_pode_ver",
                        lambda request: ([CONTA, "pj", "Prime"] + [None] * 13, None))
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="teste")
    app.include_router(rel.router)
    c = TestClient(app, follow_redirects=False)
    c.pool = pool
    yield c
    pool.close()


def _vendedor(http):
    with http.pool.connection() as c:
        return c.execute("select id from membros where conta_id=%s", (CONTA,)).fetchone()[0]


def _lead(http, nome, numero="", *, campo="whatsapp"):
    with http.pool.connection() as c:
        lid = c.execute(
            f"insert into prospeccao (conta_id, vendedor_id, contato, empresa, {campo}) "
            "values (%s,%s,%s,'Empresa',%s) returning id",
            (CONTA, _vendedor(http), nome, numero)).fetchone()[0]
        c.commit()
    return lid


def _marcar(http, lead_id, dia="2026-09-01"):
    """A visita como o Cockpit a marca. `avisar_cliente=False` porque o envio pelo
    WhatsApp não é o que está sob teste aqui."""
    return ck.agendar_visita(http.pool, CONTA, _vendedor(http), lead_id,
                             data=dia, hora="13:00", avisar_cliente=False)


def _estado(http, eid):
    with http.pool.connection() as c:
        return c.execute("select cliente_id, sem_cliente from eventos_agenda where id=%s",
                         (eid,)).fetchone()


def _quantos_clientes(http):
    with http.pool.connection() as c:
        return c.execute("select count(*) from clientes where dono_id=%s and ativo",
                         (CONTA,)).fetchone()[0]


def _colar_numero(http, cliente_id, numero):
    """Faz DOIS cadastros carregarem o mesmo número.

    Não dá pra construir isso pelo `criar_cliente`: ele já reusa a ficha quando o
    telefone bate (é o dedup da própria loja, e funciona). O duplicado real nasce
    de outro jeito — um veio do PDV com o número, o outro veio digitado sem
    número e ganhou o telefone depois. O SQL direto é o que reproduz esse estado
    sem fingir que o dedup não existe."""
    with http.pool.connection() as c:
        c.execute("update pessoas set celular=%s where id="
                  "(select pessoa_id from clientes where id=%s)", (numero, cliente_id))
        c.execute("update clientes set telefone=%s where id=%s", (numero, cliente_id))
        c.commit()


def _linhas(http, especie=""):
    return rel._dados_agenda(http.pool, CONTA, "todos", "", "", "", especie)["linhas"]


# --------------------------------------------------------------------------
# 1. A régua: os 8 dígitos finais, e só quando não há empate
# --------------------------------------------------------------------------

def test_a_regua_casa_o_mesmo_numero_em_formatos_diferentes(http):
    """O número digitado à mão vem "86988001122" e o do WhatsApp vem
    "5586988001122". Comparando texto exato eles nunca se encontram — é o que
    fazia a mesma pessoa duplicar."""
    cid = cli.criar_cliente(http.pool, CONTA, "Elisangela Moreira",
                            telefone="86988001122")
    achado = cli.buscar_unico_por_telefone(http.pool, CONTA, "5586988001122")
    assert achado and achado["id"] == cid


def test_a_regua_nao_chuta_quando_dois_cadastros_tem_o_mesmo_numero(http):
    """Dois cadastros com o mesmo número são um duplicado esperando a fusão.
    Escolher um dos dois no escuro grava vínculo em metade do histórico."""
    cli.criar_cliente(http.pool, CONTA, "Elisangela Moreira", telefone="86988001122")
    outro = cli.criar_cliente(http.pool, CONTA, "Elisangela M.")
    _colar_numero(http, outro, "86988001122")
    assert cli.buscar_unico_por_telefone(http.pool, CONTA, "86988001122") is None
    # e a versão de tela, que serve a quem vai conferir, segue devolvendo um
    assert cli.buscar_por_telefone(http.pool, CONTA, "86988001122") is not None


def test_a_regua_recusa_numero_curto_demais(http):
    """Menos de 8 dígitos casaria gente diferente."""
    cli.criar_cliente(http.pool, CONTA, "Fulano", telefone="86988001122")
    assert cli.buscar_unico_por_telefone(http.pool, CONTA, "1122") is None


def test_a_regua_nao_atravessa_conta(http):
    cli.criar_cliente(http.pool, OUTRA, "De outra loja", telefone="86988001122")
    assert cli.buscar_unico_por_telefone(http.pool, CONTA, "86988001122") is None


# --------------------------------------------------------------------------
# 2. agendar_visita: liga sozinho quando é seguro
# --------------------------------------------------------------------------

def test_visita_do_lead_liga_no_cadastro_que_ja_existe(http):
    cid = cli.criar_cliente(http.pool, CONTA, "Elisangela Moreira",
                            telefone="86988001122")
    lead = _lead(http, "Elisangela", "5586988001122")
    r = _marcar(http, lead)
    assert r["ok"]
    assert _estado(http, r["evento_id"]) == (cid, False)
    assert _quantos_clientes(http) == 1, "criou ficha em vez de reusar"


def test_visita_do_lead_nao_cadastra_ninguem_quando_o_numero_e_novo(http):
    """A decisão do dono em 31/08/2026. Nome de lead é anotação de vendedora, e a
    base de clientes é a que alimenta contrato, orçamento e cobrança."""
    lead = _lead(http, "Jacque/Elisangela 15 Anos 25/11", "5586988009999")
    r = _marcar(http, lead)
    assert r["ok"]
    assert _estado(http, r["evento_id"]) == (None, False)
    assert _quantos_clientes(http) == 0, "cadastrou o nome sujo sem ninguém olhar"


def test_visita_do_lead_sem_numero_nao_liga_nada(http):
    cli.criar_cliente(http.pool, CONTA, "Elisangela Moreira", telefone="86988001122")
    lead = _lead(http, "Elisangela", "")
    r = _marcar(http, lead)
    assert r["ok"] and _estado(http, r["evento_id"])[0] is None


def test_visita_do_lead_com_numero_ambiguo_nao_liga_nada(http):
    """Empatou, não liga: a linha continua oferecendo o botão e quem resolve é o
    dono."""
    cli.criar_cliente(http.pool, CONTA, "Elisangela Moreira", telefone="86988001122")
    _colar_numero(http, cli.criar_cliente(http.pool, CONTA, "Elisangela M."),
                  "86988001122")
    lead = _lead(http, "Elisangela", "5586988001122")
    r = _marcar(http, lead)
    assert r["ok"] and _estado(http, r["evento_id"])[0] is None


def test_lead_com_telefone_e_nao_whatsapp_tambem_liga(http):
    """251 dos 252 leads da Prime têm `whatsapp` e 1 tem só `telefone`. O
    `coalesce` que o `agendar_visita` já usava continua valendo aqui."""
    cid = cli.criar_cliente(http.pool, CONTA, "Zenilda Rosa", telefone="86988003344")
    lead = _lead(http, "Zenilda", "86988003344", campo="telefone")
    r = _marcar(http, lead)
    assert _estado(http, r["evento_id"])[0] == cid


def test_a_visita_e_marcada_mesmo_se_a_busca_de_cliente_quebrar(http, monkeypatch):
    """Uma visita não marcada por causa de uma busca é perda de verdade; o vínculo
    que faltou o botão resolve depois. A conta pode nem ter o módulo de clientes."""
    def _explode(*a, **k):
        raise RuntimeError("sem módulo de clientes")
    monkeypatch.setattr(cli, "buscar_unico_por_telefone", _explode)
    lead = _lead(http, "Elisangela", "5586988001122")
    r = _marcar(http, lead)
    assert r["ok"] and _estado(http, r["evento_id"])[0] is None


# --------------------------------------------------------------------------
# 3. O relatório: o nome do lead passa a se assumir como dedução
# --------------------------------------------------------------------------

def test_nome_vindo_do_lead_sai_marcado_e_clicavel(http):
    """O buraco de 31/08/2026: a célula só virava link quando NÃO havia nome
    nenhum. Como o nome do lead a preenchia, a linha parecia resolvida sem ter
    ficha atrás — pior do que aparecer vazia."""
    lead = _lead(http, "Erys", "5586988009999")
    _marcar(http, lead)
    (linha,) = _linhas(http)
    assert linha["cliente"] == "Erys"
    assert linha["cliente_do_lead"] is True
    assert linha["cliente_deduzido"] is True
    assert linha["cliente_link"], "a linha do lead continuou sem oferecer o botão"


def test_nome_vindo_do_vinculo_sai_limpo_e_sem_botao(http):
    cli.criar_cliente(http.pool, CONTA, "Elisangela Moreira", telefone="86988001122")
    lead = _lead(http, "Elisangela", "5586988001122")
    _marcar(http, lead)
    (linha,) = _linhas(http)
    assert linha["cliente"] == "Elisangela Moreira"
    assert linha["cliente_do_lead"] is False
    assert linha["cliente_deduzido"] is False
    assert linha["cliente_link"] is None


def test_dizer_que_nao_tem_cliente_cala_a_linha_do_lead(http):
    """Sem isto a linha cobraria atenção pra sempre — e lista que nunca esvazia é
    lista que ninguém mais abre."""
    lead = _lead(http, "Erys", "5586988009999")
    r = _marcar(http, lead)
    ag.marcar_sem_cliente(http.pool, CONTA, r["evento_id"], True)
    (linha,) = _linhas(http)
    assert linha["cliente_link"] is None


def test_nome_lido_do_titulo_continua_com_o_selo_dele(http):
    """As duas deduções convivem e são distinguíveis: a do lead é azul, a do
    título é âmbar."""
    with http.pool.connection() as c:
        c.execute("insert into eventos_agenda (conta_id, titulo, inicio, tipo_evento) "
                  "values (%s,'Casamento — Eva da Silva Fontoura',%s,'Casamento')",
                  (CONTA, ag.agora_brt() + timedelta(days=5)))
        c.commit()
    (linha,) = _linhas(http)
    assert linha["cliente"] == "Eva da Silva Fontoura"
    assert linha["cliente_do_titulo"] is True and linha["cliente_do_lead"] is False
    assert linha["cliente_link"]


def test_nome_vindo_de_orcamento_segue_como_estava(http):
    """Também é dedução, mas de outra natureza: ali existe proposta aprovada com
    cliente na ficha. O conserto certo é ligar o vínculo na hora de reservar a
    data, não cobrar do dono na tela do relatório — e este teste é o que avisa se
    alguém mudar isso sem querer."""
    with http.pool.connection() as c:
        eid = c.execute("insert into eventos_agenda (conta_id, titulo, inicio, tipo_evento) "
                        "values (%s,'Locação',%s,'Locação') returning id",
                        (CONTA, ag.agora_brt() + timedelta(days=5))).fetchone()[0]
        c.execute("insert into orcamentos (conta_id, cliente, evento_agenda_id) "
                  "values (%s,'Jonas Barreto',%s)", (CONTA, eid))
        c.commit()
    (linha,) = _linhas(http)
    assert linha["cliente"] == "Jonas Barreto"
    assert linha["cliente_deduzido"] is False and linha["cliente_link"] is None


# --------------------------------------------------------------------------
# 4. A tela de ligar, agora com o número
# --------------------------------------------------------------------------

def test_a_tela_vem_preenchida_com_o_nome_e_o_numero_do_lead(http):
    lead = _lead(http, "Erys", "5586988009999")
    r = _marcar(http, lead)
    html = http.get(f"/painel/relatorios/agenda/{r['evento_id']}/cliente").text
    campo = html.split('name="cliente_nome"')[1][:200]
    assert 'value="Erys"' in campo
    assert 'name="cliente_tel"' in html and "5586988009999" in html
    assert "veio do funil" in html


def test_confirmar_o_lead_cadastra_COM_o_numero(http):
    """Sem o campo de telefone, o conserto repetiria a perda: ficha nova sem
    número é exatamente o dado que a corrente do funil já jogava fora."""
    lead = _lead(http, "Jacque/Elisangela 15 Anos 25/11", "5586988009999")
    r = _marcar(http, lead)
    http.post(f"/painel/relatorios/agenda/{r['evento_id']}/cliente",
              data={"cliente_nome": "Elisangela Moreira",
                    "cliente_tel": "5586988009999"})
    cid, sem = _estado(http, r["evento_id"])
    assert cid and sem is False
    ficha = cli.obter_cliente(http.pool, CONTA, cid)
    assert ficha["nome"] == "Elisangela Moreira", "gravou o nome sujo do lead"
    assert (ficha["telefone"] or "").endswith("88009999"), "a ficha nasceu sem número"


def test_o_numero_manda_sobre_o_nome_corrigido(http):
    """O dono corrigiu o nome, mas o número já tem ficha: liga na que existe em
    vez de abrir a segunda. O nome do lead muda de um compromisso pro outro; o
    número não."""
    cid = cli.criar_cliente(http.pool, CONTA, "Elisangela Moreira",
                            telefone="86988001122")
    lead = _lead(http, "Jacque/Elisangela 15 Anos", "5586988001122")
    r = _marcar(http, lead)
    # o auto-liga já pegou; desfaz pra exercitar a tela como o dono a usaria
    ag.ligar_cliente(http.pool, CONTA, r["evento_id"], None)
    http.post(f"/painel/relatorios/agenda/{r['evento_id']}/cliente",
              data={"cliente_nome": "Elisangela M. Moreira",
                    "cliente_tel": "5586988001122"})
    assert _estado(http, r["evento_id"])[0] == cid
    assert _quantos_clientes(http) == 1, "abriu a segunda ficha pro mesmo número"


def test_sem_numero_a_tela_continua_funcionando_pelo_nome(http):
    """Compromisso digitado à mão não tem lead nenhum — a régua antiga segue de
    pé pra ele."""
    with http.pool.connection() as c:
        eid = c.execute("insert into eventos_agenda (conta_id, titulo, inicio, tipo_evento) "
                        "values (%s,'Casamento — Eva da Silva Fontoura',%s,'Casamento') "
                        "returning id",
                        (CONTA, ag.agora_brt() + timedelta(days=5))).fetchone()[0]
        c.commit()
    http.post(f"/painel/relatorios/agenda/{eid}/cliente",
              data={"cliente_nome": "Eva da Silva Fontoura"})
    cid, _ = _estado(http, eid)
    assert cid and cli.obter_cliente(http.pool, CONTA, cid)["nome"] == "Eva da Silva Fontoura"


def test_numero_ambiguo_na_tela_cai_pro_nome_em_vez_de_chutar(http):
    """Dois cadastros com o mesmo número: a tela não escolhe por conta própria.
    Cai na régua do nome, que aqui acha um só."""
    cli.criar_cliente(http.pool, CONTA, "Elisangela Moreira", telefone="86988001122")
    _colar_numero(http, cli.criar_cliente(http.pool, CONTA, "Elisangela M."),
                  "86988001122")
    lead = _lead(http, "Elisangela", "5586988001122")
    r = _marcar(http, lead)
    http.post(f"/painel/relatorios/agenda/{r['evento_id']}/cliente",
              data={"cliente_nome": "Elisangela M.", "cliente_tel": "5586988001122"})
    cid, _ = _estado(http, r["evento_id"])
    assert cid and cli.obter_cliente(http.pool, CONTA, cid)["nome"] == "Elisangela M."
    assert _quantos_clientes(http) == 2, "cunhou uma terceira"
