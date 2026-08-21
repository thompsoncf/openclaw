"""O atalho "Aqui na <empresa>" no campo Local do novo compromisso.

POR QUE ELE EXISTE. Quem tem espaço próprio marca quase tudo lá dentro, e até aqui o
sistema mandava procurar no Google o endereço que ele mesmo já tinha guardado em
`contas`. O resultado, medido na Prime Eventos (conta 34) em 21/08/2026: de 42
compromissos, 37 ficaram SEM local nenhum, três com "Prime Eventos" digitado à mão
(texto solto não vira link de mapa) e um com "Pri," — alguém começou a digitar e
desistiu no meio. O campo não estava difícil, estava sendo abandonado.

O que estes testes travam, em ordem de gravidade:

1. BOTÃO MORTO NÃO NASCE. Sem endereço cadastrado ele não aparece — foi a lição do
   "Abrir →" do PR dos chips. E o convite pra cadastrar só vai pra quem PODE
   cadastrar; pro resto a tela fica idêntica à de hoje.
2. O ENDEREÇO SAI COMPLETO. Rua sem cidade vira link de mapa que aponta pra rua de
   mesmo nome em qualquer lugar do país — então rua sem cidade não habilita o botão.
3. ELE FICA ACIMA DA BUSCA. Foi a escolha do dono, e é a diferença entre um atalho e
   um item escondido.
4. A DICA NÃO MENTE. "Endereço confirmado pelo Google" num endereço que veio do
   cadastro da empresa afirma uma conferência que não houve.

Renderiza o template de verdade (Jinja + tema), com banco de teste próprio.
"""
import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from psycopg_pool import ConnectionPool
from starlette.middleware.sessions import SessionMiddleware

from web import painel_agenda as pa

BASE = Path(__file__).resolve().parent.parent / "db" / "migracoes"
CONTA = 901


@pytest.fixture()
def cli(monkeypatch):
    dbname = "zaq_end_empresa"
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1,
                           open=True, kwargs={"autocommit": True,
                                              "prepare_threshold": None})
    with admin.connection() as c:
        c.execute("select pg_terminate_backend(pid) from pg_stat_activity "
                  "where datname=%s and pid <> pg_backend_pid()", (dbname,))
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    pool = ConnectionPool(url, min_size=1, max_size=3, open=True,
                          kwargs={"prepare_threshold": None})
    with pool.connection() as c:
        c.execute("""create table contas (id bigserial primary key, tipo text, nome text,
                       nome_fantasia text, endereco text, bairro text, cidade text,
                       uf text, cep text, chip_de bigint)""")
        c.execute("create table membros (id bigserial primary key, conta_id bigint, "
                  "nome text, papel text)")
        for nome in ("098_agenda.sql", "099_agenda_tipo.sql", "100_evento_convidados.sql",
                     "101_agenda_lembretes.sql", "126_agenda_avisar_convidados.sql",
                     "130_evento_desfecho.sql", "131_evento_link_online.sql",
                     "132_convidado_canal_resposta.sql", "139_agenda_mensagens_log.sql",
                     "146_agenda_enviar_confirmacao.sql", "160_agenda_pre_reserva.sql",
                     "163_evento_sinal_esperado.sql",
                     "179_agenda_tipo_e_hora_sugerida.sql"):
            c.execute((BASE / nome).read_text(encoding="utf-8"))
        c.execute("insert into contas (id, tipo, nome) values (%s,'pj','Prime')", (CONTA,))
        c.commit()

    papel = {"v": "dono"}
    monkeypatch.setattr(pa, "get_pool", lambda: pool)
    monkeypatch.setattr(pa, "_acesso",
                        lambda request: ({"conta_id": CONTA, "membro_id": None,
                                          "papel": papel["v"]}, None))

    class _VendasFake:
        @staticmethod
        def vende_data(pool_, conta_id_):
            return True

        @staticmethod
        def fichas_de_eventos(pool_, conta_id_, ids):
            return {}

    monkeypatch.setattr(pa, "_vendas", lambda: _VendasFake)

    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="teste-de-sessao")
    app.include_router(pa.router)
    c = TestClient(app, follow_redirects=False)
    c.pool = pool
    c.papel = papel
    yield c
    pool.close()


def _endereco(cli, **campos):
    """Preenche (ou apaga) o endereço da conta. Sem argumento, deixa tudo vazio."""
    dados = {"nome_fantasia": None, "endereco": None, "bairro": None,
             "cidade": None, "uf": None}
    dados.update(campos)
    with cli.pool.connection() as c:
        c.execute("""update contas set nome_fantasia=%(nome_fantasia)s, endereco=%(endereco)s,
                       bairro=%(bairro)s, cidade=%(cidade)s, uf=%(uf)s where id=%(id)s""",
                  {**dados, "id": CONTA})
        c.commit()


def _prime(cli):
    _endereco(cli, nome_fantasia="PRIME EVENTOS", endereco="DEOCLECIO BRITO, 3399",
              bairro="PLANALTO", cidade="TERESINA", uf="PI")


def _html(cli):
    r = cli.get("/painel/agenda")
    assert r.status_code == 200, r.text[:400]
    return r.text


def _papel(cli, p):
    cli.papel["v"] = p


# ═════════════════════════════ 1. com endereço, o atalho aparece

def test_botao_traz_nome_e_endereco_completo(cli):
    _prime(cli)
    html = _html(cli)
    assert "Aqui na PRIME EVENTOS" in html
    assert "DEOCLECIO BRITO, 3399 — PLANALTO — TERESINA/PI" in html


def test_endereco_vai_nos_data_attrs_pro_js_usar(cli):
    """É por eles que o clique chama o mesmo `escolherLocal` da busca."""
    _prime(cli)
    html = _html(cli)
    assert 'data-nome="PRIME EVENTOS"' in html
    assert 'data-endereco="DEOCLECIO BRITO, 3399 — PLANALTO — TERESINA/PI"' in html


def test_botao_fica_acima_da_busca(cli):
    """A escolha do dono. Abaixo, ele seria um item escondido em vez de um atalho."""
    _prime(cli)
    html = _html(cli)
    assert html.index('id="endEmpBtn"') < html.index('id="addrInput"')


def test_com_endereco_nao_pede_pra_cadastrar(cli):
    _prime(cli)
    assert "Cadastre o <b>endereço da empresa</b>" not in _html(cli)


# ═════════════════════════════ 2. endereço pela metade não habilita

def test_rua_sem_cidade_nao_vira_botao(cli):
    """Link de mapa com rua e sem cidade aponta pra rua de mesmo nome em qualquer
    lugar do país — pior que não ter link."""
    _endereco(cli, nome_fantasia="MEIA BOCA", endereco="RUA SEM CIDADE, 10")
    assert 'id="endEmpBtn"' not in _html(cli)


def test_cidade_sem_rua_nao_vira_botao(cli):
    _endereco(cli, nome_fantasia="SÓ CIDADE", cidade="TERESINA", uf="PI")
    assert 'id="endEmpBtn"' not in _html(cli)


def test_sem_bairro_e_sem_uf_o_endereco_nao_fica_torto(cli):
    """Metade das contas preencheu só rua e cidade, e várias gravaram a UF DENTRO da
    cidade ("teresina-pi"). Grudar "/" num campo vazio produziria "teresina-pi/"."""
    _endereco(cli, nome_fantasia="ZE DO ARROZ", endereco="rua das laranjeiras",
              cidade="teresina-pi")
    html = _html(cli)
    assert 'data-endereco="rua das laranjeiras — teresina-pi"' in html
    assert "teresina-pi/" not in html


def test_sem_nome_fantasia_cai_no_nome_da_conta(cli):
    _endereco(cli, endereco="AV HOMERO, 1", cidade="TERESINA", uf="PI")
    assert "Aqui na Prime" in _html(cli)


# ═════════════════════════════ 3. sem endereço: convite só pra quem pode

def test_sem_endereco_dono_e_convidado_a_cadastrar(cli):
    html = _html(cli)
    assert 'id="endEmpBtn"' not in html, "botão morto não nasce"
    assert "Cadastre o <b>endereço da empresa</b>" in html
    assert 'href="/painel/empresa"' in html


def test_financeiro_tambem_ve_o_convite(cli):
    _papel(cli, "financeiro")
    assert "Cadastre o <b>endereço da empresa</b>" in _html(cli)


def test_vendedor_nao_ve_convite_pra_algo_que_nao_pode_fazer(cli):
    """Vendedor não mexe nos dados da empresa (`caps.financeiro` é False). Pra ele a
    tela fica exatamente a de hoje."""
    _papel(cli, "vendedor")
    html = _html(cli)
    assert "Cadastre o <b>endereço da empresa</b>" not in html
    assert 'id="endEmpBtn"' not in html
    assert 'id="addrInput"' in html, "a busca de sempre tem que continuar lá"


# ═════════════════════════════ 4. a busca não foi mexida

def test_busca_manual_e_online_continuam_na_tela(cli):
    _prime(cli)
    html = _html(cli)
    assert 'id="addrInput"' in html
    assert 'id="manualToggle"' in html
    assert 'id="onlineToggle"' in html


# O CSS e o JS da agenda são servidos como ARQUIVO ESTÁTICO (agenda.css/agenda.js), não
# embutidos no HTML — a página só traz a <script src>. Então o que se afirma sobre
# comportamento se afirma sobre a fonte, e não sobre o corpo da resposta.

def test_dica_nao_diz_google_pro_endereco_da_empresa():
    """A frase de hoje é "Endereço confirmado pelo Google". Repetir isso num endereço
    que veio do cadastro afirma uma conferência que não houve."""
    assert "Endereço da sua empresa — o link do mapa vai junto" in pa._JS_CRU
    assert "Endereço confirmado pelo Google" in pa._JS_CRU, "a frase da busca segue valendo"


def test_o_clique_cai_no_mesmo_escolherLocal_da_busca():
    """O atalho não é um segundo caminho: é uma entrada mais curta pro que já existia —
    mesmo cartão verde, mesmo link do Maps, mesmo "Enviar pro cliente"."""
    assert "escolherLocal({nome: endEmpBtn.dataset.nome," in pa._JS_CRU


def test_atalho_some_quando_o_local_nao_e_endereco():
    """Manual e online dizem "o local não é um endereço buscável"; o do próprio salão
    é um endereço. Some junto com a busca, e volta junto."""
    assert pa._JS_CRU.count("mostrarAtalho(false)") >= 3, \
        "faltou esconder o atalho em manual, online ou ao escolher um local"
    assert pa._JS_CRU.count("mostrarAtalho(true)") >= 2, \
        "ele precisa voltar ao cancelar o manual/online e ao tirar o endereço escolhido"


def test_o_estilo_do_atalho_existe():
    """Bloco sem estilo é bloco invisível — o erro do PR #523, de outra forma."""
    assert ".end-emp{" in pa._CSS_CRU
    assert ".end-falta{" in pa._CSS_CRU
    assert ".end-ou{" in pa._CSS_CRU


# ═════════════════════════════ 5. o endereço é texto digitado, não markup

def _attrs_do_botao(html):
    """Os atributos do botão do atalho, como o NAVEGADOR os enxerga.

    Ler o HTML cru com `in` não serve aqui: escapado, `onclick=alert(1)` continua
    aparecendo como texto dentro do valor — o que muda é a aspa, e é ela que decide se
    aquilo é conteúdo ou um atributo novo. Quem responde isso é o parser."""
    from html.parser import HTMLParser

    class _P(HTMLParser):
        achou = None

        def handle_starttag(self, tag, attrs):
            d = dict(attrs)
            if d.get("id") == "endEmpBtn":
                _P.achou = d
    p = _P()
    _P.achou = None
    p.feed(html)
    return _P.achou


def test_aspas_no_endereco_nao_criam_atributo_novo(cli):
    """O autoescape deste template está DESLIGADO — `_env` usa `select_autoescape()`,
    que liga por extensão, e a agenda é registrada como "agenda", sem `.html`. O
    endereço é texto que o dono digita: sem `|e`, uma aspa fecha o `data-endereco=` e
    o que vier depois vira atributo de verdade."""
    _endereco(cli, nome_fantasia='Casa " onclick=alert(1) x="',
              endereco='RUA "X", 1', cidade="TERESINA", uf="PI")
    attrs = _attrs_do_botao(_html(cli))
    assert attrs is not None, "o botão nem foi renderizado"
    assert "onclick" not in attrs, "a aspa digitada virou um atributo executável"
    # e o valor chega inteiro do outro lado — escapar não pode corromper o endereço
    assert attrs["data-nome"] == 'Casa " onclick=alert(1) x="'
    assert attrs["data-endereco"] == 'RUA "X", 1 — TERESINA/PI'


def test_tag_no_nome_nao_vira_markup(cli):
    _endereco(cli, nome_fantasia="<script>alert(1)</script>", endereco="RUA A, 1",
              cidade="TERESINA", uf="PI")
    html = _html(cli)
    assert "<script>alert(1)</script>" not in html, "a tag entrou viva no documento"
    assert "&lt;script&gt;" in html
