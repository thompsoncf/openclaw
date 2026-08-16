"""A tela onde a empresa escreve o próprio contrato — e quem pode abri-la.

O contrato de locação de espaço é do NICHO DE EVENTOS. Uma conta recorrente
(tecnologia, consórcio) teria um contrato de serviço, que é outro documento e
outra conversa; oferecer a ela um contrato de locação de salão seria pior que
não oferecer nada.

O que estes testes prendem:

* **A trava do nicho está no SERVIDOR.** O card some do template numa conta
  recorrente, mas esconder botão não é controle de acesso: as rotas são POST e
  qualquer um monta a chamada. Cada uma das três responde 404 para quem não é de
  eventos.
* **Restaurar o padrão não apaga nada.** O botão troca o texto NA TELA; o que
  está gravado só morre no salvar. Um clique curioso não pode apagar o contrato
  da empresa sem chance de desistir.
* **A prévia usa orçamento de verdade.** Dado inventado esconderia justamente o
  erro que interessa — o campo que não resolve porque o item saiu do catálogo.

Banco dedicado e descartável.
"""
import os

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from psycopg_pool import ConnectionPool
from starlette.middleware.sessions import SessionMiddleware

from finance import contrato as ctr
from web import painel_servicos as ps

CONTA_EV = 11        # nicho eventos
CONTA_REC = 22       # nicho recorrente


@pytest.fixture()
def cliente(monkeypatch):
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_contrato_tela"
    with admin.connection() as c:
        c.autocommit = True
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    pool = ConnectionPool(url, min_size=1, max_size=3, open=True,
                          kwargs={"prepare_threshold": None})
    with pool.connection() as c:
        c.execute("create table contas (id bigserial primary key, nome text)")
        # `membros` existe porque o resumo do card resolve o NOME de quem alterou
        # o contrato; 'dono' não tem linha aqui e cai no nome da conta
        c.execute("create table membros (id bigserial primary key, conta_id bigint, nome text)")
        c.execute("insert into contas (id, nome) values (%s,'Prime'),(%s,'SaaS')",
                  (CONTA_EV, CONTA_REC))
        c.commit()
    with pool.connection() as c:
        ps._garantir_tabela(c)
    with pool.connection() as c:
        # o que a listagem do funil lê e o _garantir_tabela não cria (vem das
        # migrações do sinal e da agenda)
        c.execute("""alter table orcamentos add column if not exists sinal_centavos bigint;
                     alter table orcamentos add column if not exists sinal_pago_em timestamptz;
                     create table if not exists eventos_agenda (id bigserial primary key,
                       conta_id bigint, status text, pre_reserva_ate timestamptz);""")
        c.execute("""create table contrato_modelo (
                       conta_id bigint primary key references contas(id) on delete cascade,
                       clausulas jsonb not null default '[]'::jsonb,
                       regras jsonb not null default '{}'::jsonb,
                       atualizado_em timestamptz not null default now(),
                       atualizado_por text not null default '')""")
        c.execute("""create table servicos_catalogo (id bigserial primary key, conta_id bigint,
                       slug text, nome text, descricao text,
                       setup_centavos bigint default 0, mensal_centavos bigint default 0,
                       custo_centavos bigint default 0, ordem int default 0,
                       categoria text, foto_url text, icone text,
                       ativo boolean default true)""")
        c.execute("""insert into servicos_catalogo (conta_id, slug, nome, setup_centavos)
                     values (%s,'hora-extra','HORA EXTRA',62000),
                            (%s,'taxa-de-limpeza','TAXA DE LIMPEZA',40000)""",
                  (CONTA_EV, CONTA_EV))
        c.commit()

    monkeypatch.setattr(ps, "get_pool", lambda: pool)
    monkeypatch.setattr(ps.scat, "garantir_tabela", lambda pool: None)
    # o nicho é o que decide tudo aqui
    monkeypatch.setattr(ps.emp, "obter_dados_empresa",
                        lambda pool, cid: {"nicho": "eventos" if cid == CONTA_EV else "tecnologia",
                                           "razao_social": "PRIME LTDA", "cnpj": "52.752.898/0001-58"})

    estado = {"conta": CONTA_EV}

    def _logada(request):
        conta = [None] * 15
        conta[0], conta[11], conta[12], conta[14] = estado["conta"], True, True, True
        return tuple(conta)
    monkeypatch.setattr(ps, "conta_logada", _logada)

    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="teste")
    app.include_router(ps.router)

    @app.post("/_entrar")
    async def _entrar(request: Request):
        request.session["papel"] = "dono"
        request.session["membro_id"] = 1
        return {"ok": True}

    c = TestClient(app)
    c.post("/_entrar")
    c.pool = pool
    c.estado = estado
    yield c
    pool.close()


def _clausulas():
    return [{"titulo": "Cláusula 1", "corpo": "Hora extra: {preco.hora-extra}."}]


# --------------------------------------------------- o gate: só nicho evento

def test_conta_de_eventos_abre_a_tela(cliente):
    r = cliente.get("/painel/servicos/contrato")
    assert r.status_code == 200
    d = r.json()
    assert d["novo"] is True                       # nunca editou: vem o modelo padrão
    assert len(d["clausulas"]) >= 5


@pytest.mark.parametrize("metodo, url", [
    ("get", "/painel/servicos/contrato"),
    ("post", "/painel/servicos/contrato/salvar"),
    ("post", "/painel/servicos/contrato/previa"),
])
def test_conta_recorrente_nao_alcanca_nenhuma_rota(cliente, metodo, url):
    """Esconder o card no template não é controle de acesso — as rotas são POST
    e qualquer um monta a chamada."""
    cliente.estado["conta"] = CONTA_REC
    r = (cliente.get(url) if metodo == "get"
         else cliente.post(url, json={"clausulas": _clausulas(), "regras": {}}))
    assert r.status_code == 404
    assert "eventos" in r.json()["erro"]


def test_conta_recorrente_nao_grava_nada(cliente):
    cliente.estado["conta"] = CONTA_REC
    cliente.post("/painel/servicos/contrato/salvar",
                 json={"clausulas": _clausulas(), "regras": {}})
    with cliente.pool.connection() as c:
        n = c.execute("select count(*) from contrato_modelo").fetchone()[0]
    assert n == 0


# ------------------------------------------------------------ salvar e voltar

def test_salva_e_le_de_volta(cliente):
    r = cliente.post("/painel/servicos/contrato/salvar",
                     json={"clausulas": _clausulas(), "regras": {"sinal_pct": 40}})
    assert r.status_code == 200 and r.json()["clausulas"] == 1
    d = cliente.get("/painel/servicos/contrato").json()
    assert d["novo"] is False
    assert d["clausulas"][0]["corpo"] == "Hora extra: {preco.hora-extra}."
    assert d["regras"]["sinal_pct"] == 40


def test_salvar_de_novo_sobrescreve_sem_duplicar(cliente):
    cliente.post("/painel/servicos/contrato/salvar", json={"clausulas": _clausulas(), "regras": {}})
    cliente.post("/painel/servicos/contrato/salvar",
                 json={"clausulas": [{"titulo": "Outra", "corpo": "x"}], "regras": {}})
    with cliente.pool.connection() as c:
        n = c.execute("select count(*) from contrato_modelo").fetchone()[0]
    assert n == 1
    assert cliente.get("/painel/servicos/contrato").json()["clausulas"][0]["titulo"] == "Outra"


def test_clausula_vazia_e_descartada(cliente):
    """Linha em branco que o dono adicionou e não preencheu não vira cláusula
    fantasma no contrato do cliente."""
    cliente.post("/painel/servicos/contrato/salvar", json={
        "clausulas": [{"titulo": "Boa", "corpo": "texto"}, {"titulo": "", "corpo": ""}],
        "regras": {}})
    assert len(cliente.get("/painel/servicos/contrato").json()["clausulas"]) == 1


# ------------------------------------------------- restaurar não apaga nada

def test_restaurar_padrao_nao_toca_no_que_esta_salvo(cliente):
    """O botão troca o texto NA TELA. Se ele apagasse o gravado, um clique
    curioso levaria embora o contrato da empresa sem chance de desistir."""
    cliente.post("/painel/servicos/contrato/salvar",
                 json={"clausulas": [{"titulo": "Meu contrato", "corpo": "meu texto"}],
                       "regras": {"sinal_pct": 40}})
    padrao = cliente.get("/painel/servicos/contrato?padrao=1").json()
    assert padrao["clausulas"][0]["titulo"] != "Meu contrato"
    ainda = cliente.get("/painel/servicos/contrato").json()
    assert ainda["clausulas"][0]["titulo"] == "Meu contrato"
    assert ainda["regras"]["sinal_pct"] == 40


# ------------------------------------------------------------------- a paleta

def test_a_paleta_traz_os_precos_do_catalogo_da_conta(cliente):
    """É por ela que o dono descobre o slug certo. Escrever de cabeça é a forma
    mais fácil de criar uma falta silenciosa."""
    campos = [c["campo"] for c in cliente.get("/painel/servicos/contrato").json()["campos"]]
    assert "preco.hora-extra" in campos
    assert "preco.taxa-de-limpeza" in campos
    assert "cliente.nome" in campos and "regra.sinal_pct" in campos


# --------------------------------------------------------------------- prévia

def _orcamento(cliente, *, com_evento=True):
    with cliente.pool.connection() as c:
        c.execute("""insert into orcamentos (conta_id, cliente, cnpj, setup_centavos, numero,
                       evento, modo, status)
                     values (%s,'Thompson','000.000.000-00',890000,27,%s::jsonb,'evento','aprovada')""",
                  (CONTA_EV, '{"data":"31/12/2026","inicio":"21:00","convidados":50,"tipo":"Casamento"}'
                   if com_evento else '{}'))
        c.commit()


def test_previa_usa_um_orcamento_de_verdade(cliente):
    _orcamento(cliente)
    r = cliente.post("/painel/servicos/contrato/previa", json={
        "clausulas": [{"titulo": "Objeto",
                       "corpo": "{cliente.nome}, dia {evento.data}, total {valor.total}, "
                                "hora extra {preco.hora-extra}"}],
        "regras": {}})
    assert r.status_code == 200
    d = r.json()
    assert d["faltas"] == []
    assert d["clausulas"][0]["corpo"] == (
        "Thompson, dia 31/12/2026, total R$ 8.900,00, hora extra R$ 620,00")


def test_previa_denuncia_o_item_que_saiu_do_catalogo(cliente):
    """O erro que a prévia existe pra pegar."""
    _orcamento(cliente)
    d = cliente.post("/painel/servicos/contrato/previa", json={
        "clausulas": [{"titulo": "X", "corpo": "Segurança: {preco.seguranca}"}],
        "regras": {}}).json()
    assert d["faltas"] == ["preco.seguranca"]
    assert "{preco.seguranca}" in d["clausulas"][0]["corpo"]


def test_previa_reflete_as_regras_da_tela_sem_salvar(cliente):
    """O dono muda a multa no formulário e vê o efeito antes de gravar."""
    _orcamento(cliente)
    d = cliente.post("/painel/servicos/contrato/previa", json={
        "clausulas": [{"titulo": "X", "corpo": "multa de {regra.multa_cancelamento}"}],
        "regras": {"multa_cancelamento": 15}}).json()
    assert d["clausulas"][0]["corpo"] == "multa de 15%"
    with cliente.pool.connection() as c:
        assert c.execute("select count(*) from contrato_modelo").fetchone()[0] == 0


def test_sem_orcamento_com_data_a_previa_avisa_em_vez_de_fingir(cliente):
    _orcamento(cliente, com_evento=False)
    r = cliente.post("/painel/servicos/contrato/previa",
                     json={"clausulas": _clausulas(), "regras": {}})
    assert r.status_code == 404
    assert "orçamento" in r.json()["erro"]


# ------------------------------------------------- o resumo do card recolhido
#
# O contrato se escreve uma vez e fica. O card vive RECOLHIDO na tela do dia a
# dia (montar orçamento, ver o funil), e o resumo é o que responde "está no ar e
# é o meu?" sem obrigar a abrir.
#
# O selo de falta custa uma consulta a mais por carregamento, e vale: um campo
# sem valor não aparece em lugar nenhum até sair no contrato DO CLIENTE. É o
# único erro deste fluxo que estreia na frente dele.

def test_conta_nova_nao_finge_que_esta_configurada(cliente):
    """`novo` é o que faz o card abrir sozinho — quem nunca configurou não pode
    ter que descobrir a seta."""
    d = cliente.get("/painel/servicos/contrato").json()
    assert d["novo"] is True
    assert d["resumo"]["em"] == "" and d["resumo"]["por"] == ""


def test_resumo_conta_as_clausulas_e_diz_quem_mexeu(cliente):
    cliente.post("/painel/servicos/contrato/salvar",
                 json={"clausulas": [{"titulo": "A", "corpo": "x"},
                                     {"titulo": "B", "corpo": "y"}], "regras": {}})
    r = cliente.get("/painel/servicos/contrato").json()["resumo"]
    assert r["n"] == 2
    assert r["em"]                      # dd/mm de hoje
    assert r["por"] == "Prime"          # 'dono' cai no nome da conta


def test_o_selo_denuncia_o_item_que_saiu_do_catalogo(cliente):
    """O card fechado avisa antes de o campo vazio sair no contrato do cliente."""
    _orcamento(cliente)
    cliente.post("/painel/servicos/contrato/salvar", json={
        "clausulas": [{"titulo": "X", "corpo": "Segurança: {preco.seguranca}"}], "regras": {}})
    assert cliente.get("/painel/servicos/contrato").json()["resumo"]["faltas"] == \
        ["preco.seguranca"]


def test_contrato_saudavel_nao_tem_falta(cliente):
    _orcamento(cliente)
    cliente.post("/painel/servicos/contrato/salvar", json={
        "clausulas": [{"titulo": "X", "corpo": "Hora extra: {preco.hora-extra}"}], "regras": {}})
    assert cliente.get("/painel/servicos/contrato").json()["resumo"]["faltas"] == []


def test_o_item_sumir_do_catalogo_acende_o_selo(cliente):
    """A sequência real: o contrato estava certo, alguém apagou o item, e o card
    passa a avisar sem ninguém ter tocado no contrato."""
    _orcamento(cliente)
    cliente.post("/painel/servicos/contrato/salvar", json={
        "clausulas": [{"titulo": "X", "corpo": "Limpeza: {preco.taxa-de-limpeza}"}], "regras": {}})
    assert cliente.get("/painel/servicos/contrato").json()["resumo"]["faltas"] == []
    with cliente.pool.connection() as c:
        c.execute("delete from servicos_catalogo where slug='taxa-de-limpeza'")
        c.commit()
    assert cliente.get("/painel/servicos/contrato").json()["resumo"]["faltas"] == \
        ["preco.taxa-de-limpeza"]


def test_sem_orcamento_de_exemplo_o_selo_fica_quieto(cliente):
    """Sem base pra montar, dizer "tudo certo" seria mentira e dizer "faltando"
    seria alarme falso. O resumo só não fala de faltas."""
    cliente.post("/painel/servicos/contrato/salvar", json={
        "clausulas": [{"titulo": "X", "corpo": "{preco.inexistente}"}], "regras": {}})
    r = cliente.get("/painel/servicos/contrato").json()["resumo"]
    assert r["faltas"] == [] and r["n"] == 1


def test_restaurar_padrao_nao_inventa_historico(cliente):
    """A prévia do padrão não pode dizer "alterado por Manoel" — ninguém alterou."""
    cliente.post("/painel/servicos/contrato/salvar",
                 json={"clausulas": [{"titulo": "Meu", "corpo": "x"}], "regras": {}})
    r = cliente.get("/painel/servicos/contrato?padrao=1").json()["resumo"]
    assert r["em"] == "" and r["por"] == ""


# --------------------------------------- editar o orçamento refaz o acordo
#
# Editar uma proposta já aceita não é um ajuste: é refazer o acordo. O orçamento
# manda no contrato inteiro (valor, data, convidados, parcelas), então mudar os
# termos torna falso o que o cliente assinou.
#
# O sistema já derrubava a aprovação da PROPOSTA. Faltava o contrato: deixá-lo
# assinado apontando pro orçamento nº X que agora diz outra coisa seria manter
# dois documentos do mesmo cliente em desacordo — o que este fluxo inteiro
# existe pra impedir.

def _proposta_aceita(cliente):
    """Orçamento aprovado, contrato assinado e foto de preços tirada — o estado
    completo que a edição precisa desmontar."""
    with cliente.pool.connection() as c:
        oid = c.execute(
            """insert into orcamentos (conta_id, cliente, empresa, status, modo,
                 setup_centavos, numero, evento, aprovada_por, aprovada_em,
                 contrato_texto, contrato_assinado_em, contrato_assinado_por,
                 contrato_precos)
               values (%s,'Thompson','Thompson','aprovada','evento',890000,27,
                 '{"data":"31/12/2026","inicio":"21:00","convidados":50}'::jsonb,
                 'Thompson Ferreira', now(),
                 '[{"titulo":"C1","corpo":"texto"}]'::jsonb, now(), 'Thompson Ferreira',
                 '{"hora-extra": 62000}'::jsonb)
               returning id""", (CONTA_EV,)).fetchone()[0]
        c.commit()
        return oid


def _estado(cliente, oid):
    with cliente.pool.connection() as c:
        return c.execute(
            """select status, aprovada_por, contrato_assinado_por, contrato_texto,
                      contrato_precos
                 from orcamentos where id=%s""", (oid,)).fetchone()


def test_editar_derruba_a_aprovacao_e_a_assinatura_do_contrato(cliente):
    oid = _proposta_aceita(cliente)
    r = cliente.post("/painel/servicos/salvar", json={
        "id": oid, "empresa": "Thompson", "cliente": "Thompson",
        "modulos": [], "itens": [], "evento": {"data": "31/12/2026", "convidados": 80}})
    assert r.status_code == 200
    status, aprov, contrato_por, texto, precos = _estado(cliente, oid)
    assert status == "enviado"          # volta pra fila do cliente
    assert aprov is None                # a aprovação some…
    assert contrato_por is None         # …e a assinatura do contrato também
    assert texto is None                # inclusive o documento congelado
    assert precos is None               # e a foto de preços daquele aceite


def test_orcamento_fechado_continua_intocavel(cliente):
    """Fechado virou contrato e título a receber: editar não passa nem antes."""
    oid = _proposta_aceita(cliente)
    with cliente.pool.connection() as c:
        c.execute("update orcamentos set status='fechado' where id=%s", (oid,))
        c.commit()
    cliente.post("/painel/servicos/salvar", json={
        "id": oid, "empresa": "Outro", "cliente": "Outro",
        "modulos": [], "itens": [], "evento": {}})
    assert _estado(cliente, oid)[0] == "fechado"
    assert _estado(cliente, oid)[2] == "Thompson Ferreira"


def test_a_lista_diz_o_que_a_edicao_vai_derrubar(cliente):
    """O aviso do botão Editar precisa nomear quem aprovou e quem assinou — um
    "tem certeza?" genérico não deixa ninguém decidir."""
    _proposta_aceita(cliente)
    it = cliente.get("/painel/servicos/lista").json()["itens"][0]
    assert it["aprovada_por"] == "Thompson Ferreira"
    assert it["contrato_por"] == "Thompson Ferreira"
    assert it["contrato_em"]


def test_o_modelo_padrao_da_tela_monta_sem_falta(cliente):
    """Conta nova abre a tela, clica em pré-visualizar e não pode ver buraco."""
    _orcamento(cliente)
    d0 = cliente.get("/painel/servicos/contrato").json()
    d = cliente.post("/painel/servicos/contrato/previa",
                     json={"clausulas": d0["clausulas"], "regras": d0["regras"]}).json()
    assert d["faltas"] == []
