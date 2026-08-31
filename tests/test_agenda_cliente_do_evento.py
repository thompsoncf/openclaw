"""O compromisso passa a saber DE QUEM é.

POR QUE ISTO EXISTE. Em 31/08/2026 o relatório de Agenda mostrava "—" na coluna
Cliente em 51 dos 60 compromissos da Prime. A causa não era o relatório: o
formulário de novo compromisso NÃO TINHA CAMPO DE CLIENTE. Tinha título, data,
hora, encerramento, "só segurar a data", descrição, local, tipo, participantes e
avisos — e nenhum lugar pra dizer de quem era a festa. O nome acabava dentro do
texto do título ("Locação — Fulano"), onde é texto e não dado: não soma por
cliente, não liga na ficha, não vira histórico.

Dos 43 EVENTOS da conta (locação, casamento, formatura) nenhum tinha lead, e é
esperado — locação não nasce de lead de WhatsApp, nasce de telefonema. Os dois
caminhos que existiam (orçamento aprovado e visita marcada pelo Cockpit) cobriam
9 dos 60.

Aqui prende-se a camada 1: o campo grava `eventos_agenda.cliente_id` (migração
192), reusando a busca do PDV e o `salvar_cliente` da aba Clientes — que desde
29/08 não duplica. O mesmo formulário passou a perguntar o TIPO da festa e os
CONVIDADOS, colunas que existem desde a 179 e que só a importação preenchia.
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
from web import painel_agenda as pa

CONTA = 5
OUTRA = 6
BASE = Path(__file__).resolve().parent.parent / "db" / "migracoes"

_MIGRACOES = ("098_agenda.sql", "099_agenda_tipo.sql", "100_evento_convidados.sql",
              "101_agenda_lembretes.sql", "126_agenda_avisar_convidados.sql",
              "130_evento_desfecho.sql", "131_evento_link_online.sql",
              "132_convidado_canal_resposta.sql", "139_agenda_mensagens_log.sql",
              "146_agenda_enviar_confirmacao.sql", "160_agenda_pre_reserva.sql",
              "163_evento_sinal_esperado.sql", "179_agenda_tipo_e_hora_sugerida.sql",
              "064_clientes_lojista.sql", "066_pessoas_identidade.sql",
              "131_pessoa_cnpj.sql", "149_cliente_cidade_uf.sql",
              "182_clientes_papel.sql", "192_evento_cliente.sql")


# ------------------------------------------------------------- parte pura

@pytest.mark.parametrize("txt, esperado", [
    ("180", 180), ("180 pessoas", 180), (" 60 ", 60), ("1.200", 1200),
    # "não sei" não é zero: zero convidado é uma afirmação, e gravá-la por
    # engano faria o buffet ser dimensionado pra ninguém.
    ("", None), ("   ", None), ("abc", None), (None, None),
])
def test_inteiro_le_o_numero_como_o_dono_digita(txt, esperado):
    assert pa._inteiro(txt) is esperado or pa._inteiro(txt) == esperado


# ------------------------------------------------------------------ o banco

@pytest.fixture
def cliente_http(monkeypatch):
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_agenda_cliente_evento"
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
        c.execute("create table contas (id bigserial primary key, tipo text, nome text, "
                  "chip_de bigint, plano text, status text default 'ativa')")
        c.execute("create table membros (id bigserial primary key, conta_id bigint, "
                  "nome text, papel text)")
        # a 066 pendura `cliente_id` em `lancamentos` (a venda ao cliente). Aqui
        # nada usa lançamento — só a coluna precisa ter onde nascer.
        c.execute("create table lancamentos (id bigserial primary key, conta_id bigint)")
        # `acesso_pj` (que decide se o campo de cliente aparece) lê estas duas.
        c.execute("create table planos (codigo text primary key, tipo_conta text)")
        c.execute("create table conta_modulos (conta_id bigint, modulo text, ativo boolean)")
        for nome in _MIGRACOES:
            c.execute((BASE / nome).read_text(encoding="utf-8"))
        # endereco/cep vêm da 152, que também mexe em `orcamentos` — tabela que este
        # teste não tem. São só duas colunas; replicá-las aqui evita arrastar a
        # migração inteira, e sem elas o SELECT de `clientes` quebra.
        c.execute("alter table clientes add column if not exists endereco text")
        c.execute("alter table clientes add column if not exists cep text")
        c.execute("insert into contas (id, tipo, nome) values (%s,'pj','Buffet')", (CONTA,))
        c.execute("insert into contas (id, tipo, nome) values (%s,'pj','Vizinha')", (OUTRA,))
        # a conta tem o módulo Empresa (é o que faz o campo de cliente existir)
        c.execute("insert into conta_modulos (conta_id, modulo, ativo) values (%s,'pj',true)",
                  (CONTA,))
        c.commit()

    monkeypatch.setattr(pa, "get_pool", lambda: pool)
    monkeypatch.setattr(pa, "_acesso",
                        lambda request: ({"conta_id": CONTA, "membro_id": None,
                                          "papel": "dono"}, None))
    estado = {"vende": True}

    class _VendasFake:
        @staticmethod
        def vende_data(pool_, conta_id_):
            return estado["vende"]

        @staticmethod
        def fichas_de_eventos(pool_, conta_id_, ids):
            return {}

    monkeypatch.setattr(pa, "_vendas", lambda: _VendasFake)
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="teste")
    app.include_router(pa.router)
    c = TestClient(app, follow_redirects=False)
    c.pool = pool
    c.estado = estado
    yield c
    pool.close()


def _marcar(c, **extra):
    dados = {"titulo": "Locação — Fulano",
             "data": (ag.agora_brt() + timedelta(days=25)).date().isoformat(),
             "hora": "19:00", "local": "", "descricao": "", "tipo": "empresa",
             "link_online": "", "m": ""}
    dados.update(extra)
    return c.post("/painel/agenda/novo", data=dados)


def _ultimo(c):
    with c.pool.connection() as cx:
        return cx.execute(
            "select id, titulo, cliente_id, tipo_evento, convidados "
            "from eventos_agenda order by id desc limit 1").fetchone()


def _quantos_clientes(c, dono=CONTA):
    with c.pool.connection() as cx:
        return cx.execute("select count(*) from clientes where dono_id=%s and ativo",
                          (dono,)).fetchone()[0]


# --------------------------------------------------- escolher da lista

def test_cliente_escolhido_da_lista_fica_ligado(cliente_http):
    cid = cli.criar_cliente(cliente_http.pool, CONTA, "Jonas Barreto Castro Neto")
    assert _marcar(cliente_http, cliente_id=str(cid)).status_code == 303
    _id, _tit, cliente_id, _tp, _cv = _ultimo(cliente_http)
    assert cliente_id == cid
    assert _quantos_clientes(cliente_http) == 1, "escolher da lista não cadastra de novo"


def test_id_de_cliente_de_outra_loja_e_ignorado(cliente_http):
    """`cliente_id` chega do navegador, e navegador não é fonte confiável. O
    servidor confere que a relação é MESMO desta conta antes de ligar."""
    alheio = cli.criar_cliente(cliente_http.pool, OUTRA, "Cliente da vizinha")
    assert _marcar(cliente_http, cliente_id=str(alheio)).status_code == 303
    _id, _tit, cliente_id, _tp, _cv = _ultimo(cliente_http)
    assert cliente_id is None


def test_id_que_nao_existe_nao_derruba_a_marcacao(cliente_http):
    assert _marcar(cliente_http, cliente_id="99999").status_code == 303
    _id, _tit, cliente_id, _tp, _cv = _ultimo(cliente_http)
    assert cliente_id is None, "ligou num id inexistente"


# --------------------------------------------------- digitar um nome

def test_nome_digitado_vira_cadastro_e_fica_ligado(cliente_http):
    """Quem marca uma locação de alguém novo não devia ter que sair da tela pra
    cadastrar antes."""
    assert _marcar(cliente_http, cliente_nome="Eva da Silva Fontoura").status_code == 303
    _id, _tit, cliente_id, _tp, _cv = _ultimo(cliente_http)
    assert cliente_id is not None
    c = cli.obter_cliente(cliente_http.pool, CONTA, cliente_id)
    assert c["nome"] == "Eva da Silva Fontoura"


def test_nome_que_ja_existe_reusa_a_ficha_em_vez_de_duplicar(cliente_http):
    """O conserto de 29/08 valendo aqui: o cadastro passa por `salvar_cliente`,
    então digitar de novo um nome que já está na base não cunha um segundo."""
    cid = cli.criar_cliente(cliente_http.pool, CONTA, "Zenilda Rosa Silva",
                            telefone="8695000001")
    assert _quantos_clientes(cliente_http) == 1
    assert _marcar(cliente_http, cliente_nome="Zenilda Rosa Silva").status_code == 303
    assert _quantos_clientes(cliente_http) == 1, "cunhou um cadastro repetido"
    _id, _tit, cliente_id, _tp, _cv = _ultimo(cliente_http)
    assert cliente_id == cid


def test_nome_ambiguo_nao_liga_em_ninguem_e_nao_cadastra(cliente_http):
    """Dois cadastros com o mesmo nome: o sistema não escolhe por você. O
    compromisso entra sem vínculo — honesto — e quem decide é o dono, na lista da
    busca. Chutar um dos dois seria pôr a festa na ficha errada."""
    cli.criar_cliente(cliente_http.pool, CONTA, "Maria Souza", telefone="8695000011")
    cli.criar_cliente(cliente_http.pool, CONTA, "Maria Souza", telefone="8695000022")
    assert _quantos_clientes(cliente_http) == 2
    assert _marcar(cliente_http, cliente_nome="Maria Souza").status_code == 303
    _id, _tit, cliente_id, _tp, _cv = _ultimo(cliente_http)
    assert cliente_id is None, "chutou um dos dois homônimos"
    assert _quantos_clientes(cliente_http) == 2, "cunhou um terceiro"


def test_o_id_manda_sobre_o_nome_digitado(cliente_http):
    """Os dois campos viajam juntos: o nome é o que está escrito na caixa, o id é
    a escolha. Escolheu, vale a escolha."""
    cid = cli.criar_cliente(cliente_http.pool, CONTA, "Nome de verdade")
    r = _marcar(cliente_http, cliente_id=str(cid), cliente_nome="texto qualquer")
    assert r.status_code == 303
    _id, _tit, cliente_id, _tp, _cv = _ultimo(cliente_http)
    assert cliente_id == cid
    assert _quantos_clientes(cliente_http) == 1


# --------------------------------------------------- sem cliente

def test_sem_cliente_o_compromisso_nasce_como_sempre(cliente_http):
    """Reunião interna e compromisso pessoal não têm dono. O campo é opcional, e
    exigir um transformaria a tela em ruído."""
    assert _marcar(cliente_http, titulo="REUNIÃO COM ENGENHEIRA").status_code == 303
    _id, titulo, cliente_id, _tp, _cv = _ultimo(cliente_http)
    assert titulo == "REUNIÃO COM ENGENHEIRA" and cliente_id is None


def test_nome_so_com_espacos_nao_cadastra_ninguem(cliente_http):
    assert _marcar(cliente_http, cliente_nome="   ").status_code == 303
    _id, _tit, cliente_id, _tp, _cv = _ultimo(cliente_http)
    assert cliente_id is None and _quantos_clientes(cliente_http) == 0


def test_tropeco_no_cadastro_nao_impede_a_data_de_entrar(cliente_http, monkeypatch):
    """Marcar o compromisso é o que importa. Se o cadastro falhar, a data entra
    assim mesmo — perder a data pra salvar uma ficha seria a troca errada."""
    def _explode(*a, **k):
        raise RuntimeError("banco de clientes fora do ar")
    monkeypatch.setattr(cli, "salvar_cliente", _explode)
    assert _marcar(cliente_http, cliente_nome="Alguém").status_code == 303
    _id, titulo, cliente_id, _tp, _cv = _ultimo(cliente_http)
    assert titulo == "Locação — Fulano" and cliente_id is None


# --------------------------------------------------- tipo e convidados

def test_tipo_do_evento_e_convidados_sao_gravados(cliente_http):
    """As duas colunas existem desde a 179 e até aqui só a importação preenchia —
    é daí que vinham as festas "sem tipo" no relatório."""
    r = _marcar(cliente_http, tipo_evento="Locação", convidados="180")
    assert r.status_code == 303
    _id, _tit, _cl, tipo_evento, convidados = _ultimo(cliente_http)
    assert tipo_evento == "Locação" and convidados == 180


def test_conta_que_nao_vende_data_nao_grava_tipo_nem_convidados(cliente_http):
    """Mesma regra da 179 — as colunas são do nicho de eventos. O gate está no
    SERVIDOR porque o formulário vem do navegador."""
    cliente_http.estado["vende"] = False
    r = _marcar(cliente_http, tipo_evento="Locação", convidados="180")
    assert r.status_code == 303
    _id, _tit, _cl, tipo_evento, convidados = _ultimo(cliente_http)
    assert tipo_evento is None and convidados is None


def test_convidados_em_branco_fica_nulo_e_nao_zero(cliente_http):
    assert _marcar(cliente_http, tipo_evento="Casamento", convidados="").status_code == 303
    _id, _tit, _cl, tipo_evento, convidados = _ultimo(cliente_http)
    assert tipo_evento == "Casamento" and convidados is None


# --------------------------------------------------- a tela

def test_a_tela_oferece_os_campos_novos(cliente_http):
    html = cliente_http.get("/painel/agenda").text
    assert 'name="cliente_nome"' in html and 'name="cliente_id"' in html
    assert 'name="tipo_evento"' in html and 'name="convidados"' in html
    for t in ("Locação", "Casamento", "Formatura"):
        assert f'<option value="{t}">' in html


def test_a_busca_de_cliente_e_a_mesma_do_pdv(cliente_http):
    """Um endpoint só, um comportamento só. (O JS da agenda é servido como
    arquivo — `_JS_CRU` vira agenda.js —, então se lê a fonte, não a página.)"""
    assert "/painel/clientes/buscar" in pa._JS_CRU


def test_o_titulo_se_monta_do_tipo_e_do_cliente(cliente_http):
    """No formato que a equipe JÁ digita hoje — ninguém muda de hábito — e para de
    se montar quando alguém reescreve, porque o vínculo não mora no texto."""
    assert "function montaTitulo()" in pa._JS_CRU
    assert "tipo + ' — ' + nome" in pa._JS_CRU
    assert "TIT_MEXIDO" in pa._JS_CRU
