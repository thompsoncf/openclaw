"""O botão "Sinal recebido" do funil, pelas ROTAS de verdade.

Testar `agenda.confirmar_pre_reserva` direto não prova nada sobre a tela: já
aconteceu neste repo de o mecanismo estar certo e o botão ser um no-op porque a
rota não chamava ninguém. Aqui passa-se pelo HTTP:

  GET  /painel/servicos/lista        -> é ele que decide se o botão APARECE
  POST /painel/servicos/sinal-recebido -> é ele que firma a data

Banco dedicado e descartável, no padrão de tests/test_orcamento_excluir.py.
"""
import os
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from psycopg_pool import ConnectionPool
from starlette.middleware.sessions import SessionMiddleware

from finance import agenda as ag
from web import painel_servicos as ps

CONTA = 7
OUTRA = 8
BASE = Path(__file__).resolve().parent.parent / "db" / "migracoes"


@pytest.fixture()
def cliente(monkeypatch):
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_sinal_recebido"
    with admin.connection() as c:
        c.autocommit = True
        # conexão pendurada do caso anterior segura o drop; derruba antes de tentar
        c.execute("select pg_terminate_backend(pid) from pg_stat_activity where datname=%s",
                  (dbname,))
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    pool = ConnectionPool(url, min_size=1, max_size=3, open=True,
                          kwargs={"prepare_threshold": None})
    with pool.connection() as c:
        c.execute("create table contas (id bigserial primary key, nome text)")
        # 098_agenda referencia membros (dono do compromisso); só a coluna importa aqui
        c.execute("create table membros (id bigserial primary key, conta_id bigint, "
                  "nome text, papel text)")
        # titulos existe aqui pra a busca do título do sinal RODAR de verdade: sem a
        # tabela, o erro seria engolido pelo try/except da rota e `titulo_baixado`
        # viria None por motivo errado. Nestes casos o contrato nunca é fechado, então
        # o certo é não achar título nenhum — e é isso que se quer provar.
        c.execute("""create table titulos (id bigserial primary key, conta_id bigint,
            tipo text, descricao text, contraparte text default '', valor_centavos int,
            vencimento date, status text default 'aberto', recorrente boolean default false,
            categoria text default '', lancamento_id bigint, pago_em date,
            criado_por bigint, orcamento_id bigint, parcela_idx int,
            criado_em timestamptz default now())""")
        # salvar/1 valida os módulos contra o catálogo da conta e espelha o cliente;
        # as duas leituras precisam das tabelas existirem, mesmo vazias.
        c.execute("""create table servicos_catalogo (id bigserial primary key,
            conta_id bigint, slug text, nome text, descricao text,
            setup_centavos bigint default 0, mensal_centavos bigint default 0,
            custo_centavos bigint default 0, ordem int default 0,
            ativo boolean default true, categoria text, foto_url text, icone text)""")
        # o MODO do orçamento sai do nicho da conta (vendas.modo_do_orcamento), e a
        # conta de teste é de eventos — é o que faz salvar/1 gravar modo='evento'.
        c.execute("create table nichos (id bigserial primary key, nome text, "
                  "slug text unique, tipo text, ativo boolean default true)")
        c.execute("alter table contas add column if not exists nicho_id bigint")
        for col in ("documento", "razao_social", "nome_fantasia", "endereco", "bairro",
                    "cep", "cidade", "uf", "email_empresa", "telefone", "cnae"):
            c.execute(f"alter table contas add column if not exists {col} text")
        c.execute("insert into nichos (nome, slug, tipo) values ('Eventos','eventos','servico')")
        # 164: confirmar o sinal também CRIA o contrato. A tabela existe aqui pra o
        # teste medir isso de verdade — sem ela o `contrato_id` viria None por erro
        # engolido, não porque a regra decidiu assim.
        c.execute("""create table contratos (id bigserial primary key, conta_id bigint not null,
            numero int not null, orcamento_id bigint, status text not null default 'enviado',
            texto jsonb, valor_centavos bigint, assinado_em timestamptz, assinado_por text,
            assinado_doc text, assinado_ip text, rescindido_em timestamptz,
            rescisao_motivo text, substitui_id bigint, token text,
            criado_em timestamptz default now(),
            criado_por text default '')""")
        c.execute("create unique index ux_ct_cn on contratos (conta_id, numero)")
        c.commit()
    with pool.connection() as c:
        ps._garantir_tabela(c)          # cria orcamentos como em produção
    with pool.connection() as c:
        for nome in ("098_agenda.sql", "099_agenda_tipo.sql", "130_evento_desfecho.sql",
                     "131_evento_link_online.sql", "160_agenda_pre_reserva.sql",
                     "161_orcamento_sinal.sql", "163_evento_sinal_esperado.sql"):
            c.execute((BASE / nome).read_text(encoding="utf-8"))
        c.execute("insert into contas (id, nome, nicho_id) values "
                  "(%s,'Buffet Teste',(select id from nichos where slug='eventos'))", (CONTA,))
        c.execute("insert into contas (id, nome) values (%s,'Vizinha')", (OUTRA,))
        c.commit()

    monkeypatch.setattr(ps, "get_pool", lambda: pool)
    conta = [None] * 15
    conta[0], conta[11], conta[12], conta[14] = CONTA, True, True, True
    monkeypatch.setattr(ps, "conta_logada", lambda request: tuple(conta))

    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="teste-de-sessao")
    app.include_router(ps.router)

    @app.post("/_entrar")
    async def _entrar(request: Request):
        request.session["papel"] = "dono"
        request.session["membro_id"] = None
        return {"ok": True}

    c = TestClient(app)
    c.pool = pool
    c.post("/_entrar")
    yield c
    pool.close()


def _orcamento_com_data_segurada(c, *, conta_id=CONTA, dias=3, sinal=181000):
    """Um orçamento de evento aprovado cuja data está SEGURADA esperando o sinal —
    o estado em que o botão precisa aparecer."""
    ate = ag.agora_brt() + timedelta(days=dias)
    ev = ag.criar_evento(c.pool, conta_id, "Casamento — Ana",
                         ag.agora_brt() + timedelta(days=30), pre_reserva_ate=ate)
    with c.pool.connection() as cx:
        oid = cx.execute(
            """insert into orcamentos (conta_id, cliente, empresa, status, criado_por,
                 setup_centavos, modo, evento_agenda_id, sinal_centavos)
               values (%s,'Ana','Ana','aprovada','',745000,'evento',%s,%s) returning id""",
            (conta_id, ev["id"], sinal)).fetchone()[0]
        cx.commit()
    return oid, ev["id"]


def _item(c, oid):
    itens = c.get("/painel/servicos/lista").json()["itens"]
    return next(i for i in itens if i["id"] == oid)


def test_lista_manda_o_que_o_botao_precisa(cliente):
    """Se a rota não mandar `pre_reserva_ate`, o botão simplesmente não é desenhado
    — foi assim que um painel já mostrou reserva que o botão não sabia usar."""
    oid, _ = _orcamento_com_data_segurada(cliente)
    it = _item(cliente, oid)
    assert it["pre_reserva_ate"] and it["sinal"] == "R$ 1.810,00"
    assert it["sinal_pago"] is False


def test_confirmar_firma_a_data_e_o_botao_some(cliente):
    oid, ev_id = _orcamento_com_data_segurada(cliente)
    r = cliente.post("/painel/servicos/sinal-recebido", json={"id": oid})
    assert r.status_code == 200
    # titulo_baixado None porque o contrato não foi fechado: não existe título ainda.
    # Quem dá a baixa nesse caminho é o próprio fechar_orcamento, depois.
    d = r.json()
    assert {k: d[k] for k in ("ok", "ja_estava", "reserva_firmada", "titulo_baixado")} == {
        "ok": True, "ja_estava": False, "reserva_firmada": True, "titulo_baixado": None}
    # e o CONTRATO nasceu junto — é o fato que substitui as três condições que a
    # folha reavaliava a cada carregamento
    assert d["contrato_id"]
    with cliente.pool.connection() as cx:
        assert cx.execute("select status, numero, orcamento_id from contratos where id=%s",
                          (d["contrato_id"],)).fetchone() == ("enviado", 1, oid)
    with cliente.pool.connection() as cx:
        assert cx.execute("select status, pre_reserva_ate from eventos_agenda where id=%s",
                          (ev_id,)).fetchone() == ("ativo", None)
        assert cx.execute("select sinal_pago_em from orcamentos where id=%s",
                          (oid,)).fetchone()[0] is not None
    # e a tela para de oferecer o botão sozinha (a subconsulta zera)
    it = _item(cliente, oid)
    assert it["pre_reserva_ate"] == "" and it["sinal_pago"] is True


def test_a_linha_do_funil_carrega_o_link_do_contrato(cliente):
    """O contrato é documento PRÓPRIO, com URL própria — e o dono precisa mandar
    esse link do mesmo lugar de onde já manda o da proposta. Sem `contrato_token`
    na resposta da lista, os botões 📜/↗ simplesmente não são desenhados e o
    contrato existe sem ninguém ter como abrir."""
    oid, _ = _orcamento_com_data_segurada(cliente)
    # antes de existir contrato, a linha não pode fingir que existe
    assert _item(cliente, oid)["contrato_token"] == ""
    cliente.post("/painel/servicos/sinal-recebido", json={"id": oid})
    it = _item(cliente, oid)
    assert it["contrato_token"] and it["contrato_numero"] == 1
    assert it["contrato_assinado"] is False
    # e o token da linha é o token DO contrato daquele orçamento, não outro
    with cliente.pool.connection() as cx:
        assert cx.execute("select token from contratos where orcamento_id=%s",
                          (oid,)).fetchone()[0] == it["contrato_token"]
        cx.execute("update contratos set assinado_em=now(), status='assinado' "
                   "where orcamento_id=%s", (oid,))
        cx.commit()
    assert _item(cliente, oid)["contrato_assinado"] is True


def test_contrato_de_outro_orcamento_nao_vaza_pra_linha(cliente):
    """A subconsulta casa por `orcamento_id`. Se casasse só por conta, toda linha
    do funil mostraria o contrato do vizinho — e o dono mandaria pro cliente
    errado um documento com o nome e o CPF de outra pessoa."""
    oid_a, _ = _orcamento_com_data_segurada(cliente)
    oid_b, _ = _orcamento_com_data_segurada(cliente)
    cliente.post("/painel/servicos/sinal-recebido", json={"id": oid_a})
    assert _item(cliente, oid_a)["contrato_token"] != ""
    assert _item(cliente, oid_b)["contrato_token"] == ""


def test_confirmar_duas_vezes_nao_quebra(cliente):
    """O botão é clicável de novo enquanto a resposta não volta; a segunda vez não
    pode ser erro nem desfazer nada."""
    oid, ev_id = _orcamento_com_data_segurada(cliente)
    cliente.post("/painel/servicos/sinal-recebido", json={"id": oid})
    r = cliente.post("/painel/servicos/sinal-recebido", json={"id": oid})
    assert r.status_code == 200
    d = r.json()
    assert {k: d[k] for k in ("ok", "ja_estava", "reserva_firmada", "titulo_baixado")} == {
        "ok": True, "ja_estava": True, "reserva_firmada": False, "titulo_baixado": None}
    # idempotente também no contrato: a segunda vez devolve o MESMO, não cria outro
    with cliente.pool.connection() as cx:
        assert cx.execute("select count(*) from contratos where orcamento_id=%s",
                          (oid,)).fetchone()[0] == 1
    with cliente.pool.connection() as cx:
        assert cx.execute("select status from eventos_agenda where id=%s",
                          (ev_id,)).fetchone()[0] == "ativo"


def test_orcamento_de_outra_conta_nao_e_confirmado(cliente):
    """Escopo multi-tenant: o id vem da tela, e tela não é fonte confiável."""
    oid, ev_id = _orcamento_com_data_segurada(cliente, conta_id=OUTRA)
    r = cliente.post("/painel/servicos/sinal-recebido", json={"id": oid})
    assert r.status_code == 404
    with cliente.pool.connection() as cx:
        assert cx.execute("select status from eventos_agenda where id=%s",
                          (ev_id,)).fetchone()[0] == ag.PRE_RESERVADO


def test_orcamento_sem_data_segurada_nao_aparece_como_pendente(cliente):
    """Proposta recorrente (ou evento sem sinal) não pode ganhar o aviso âmbar."""
    with cliente.pool.connection() as cx:
        oid = cx.execute(
            """insert into orcamentos (conta_id, cliente, empresa, status, criado_por,
                 setup_centavos, modo) values (%s,'Clínica','Clínica','aprovada','',
                 920000,'recorrente') returning id""", (CONTA,)).fetchone()[0]
        cx.commit()
    it = _item(cliente, oid)
    assert it["pre_reserva_ate"] == "" and it["sinal"] == ""


# ---------------------------------- reabrir a proposta, pela rota de verdade

def _aprovado_com_data(c, *, sinal_pago=False, dias=20):
    """Um orçamento APROVADO com a data já na agenda — o estado de onde a reabertura
    parte. `pre_reservado` quando o sinal não caiu, `ativo` quando caiu."""
    dia = (ag.agora_brt() + timedelta(days=dias)).date()
    # a MESMA janela que o payload vai mandar — senão o teste do "nada mudou"
    # mediria uma remarcação de verdade (e a de baixo, o contrário).
    quando, fim = ag.janela_evento(dia.isoformat(), "19:00", "23:00")
    ev = ag.criar_evento(c.pool, CONTA, "Casamento — Ana", quando, fim=fim,
                         pre_reserva_ate=None if sinal_pago else quando)
    with c.pool.connection() as cx:
        oid = cx.execute(
            """insert into orcamentos (conta_id, cliente, empresa, status, criado_por,
                 setup_centavos, primeiro_ano_centavos, modo, evento, parcelas,
                 evento_agenda_id, sinal_centavos, sinal_pago_em)
               values (%s,'Ana','Ana','aprovada','',745000,745000,'evento',
                       %s::jsonb, %s::jsonb, %s, 181000, %s) returning id""",
            (CONTA,
             '{"data":"' + quando.date().isoformat() + '","inicio":"19:00","fim":"23:00",'
             '"tipo":"Casamento","convidados":100}',
             '[{"venc":"2026-01-10","valor_centavos":181000,"forma":"Pix",'
             '"obs":"Sinal — confirma a reserva da data"}]',
             ev["id"], "now()" if False else (ag.agora_brt() if sinal_pago else None))
        ).fetchone()[0]
        cx.commit()
    return oid, ev["id"], quando


def _payload(oid, *, data, inicio="19:00", fim="23:00"):
    return {"id": oid, "cliente": "Ana", "empresa": "Ana", "setup": 7450,
            "primeiro_ano": 7450, "n_modulos": 1,
            "itens": [{"nome": "Pacote", "setup": 7450, "mensal": 0}],
            "evento": {"data": data, "inicio": inicio, "fim": fim,
                       "tipo": "Casamento", "convidados": 100},
            "parcelas": [{"venc": "2026-01-10", "valor_centavos": 181000,
                          "forma": "Pix", "obs": "Sinal — confirma a reserva da data"}]}


def test_editar_proposta_aprovada_libera_a_data_pela_rota(cliente):
    """O buraco que isso fecha: editar reabria a proposta mas deixava a data
    ocupada por um orçamento que voltou a ser rascunho — e a re-aprovação nem
    remarcava, porque o vínculo continuava lá."""
    oid, ev_id, quando = _aprovado_com_data(cliente)
    r = cliente.post("/painel/servicos/salvar",
                     json=_payload(oid, data=quando.date().isoformat()))
    assert r.status_code == 200
    assert r.json()["reaberta"] == {"liberou": True, "remarcou": False}
    with cliente.pool.connection() as cx:
        assert cx.execute("select status from eventos_agenda where id=%s",
                          (ev_id,)).fetchone()[0] == "cancelado"
        assert cx.execute("select status, evento_agenda_id from orcamentos where id=%s",
                          (oid,)).fetchone() == ("enviado", None)


def test_editar_proposta_com_sinal_pago_mantem_a_data(cliente):
    oid, ev_id, quando = _aprovado_com_data(cliente, sinal_pago=True)
    r = cliente.post("/painel/servicos/salvar",
                     json=_payload(oid, data=quando.date().isoformat()))
    assert r.json()["reaberta"] == {"liberou": False, "remarcou": False}
    with cliente.pool.connection() as cx:
        assert cx.execute("select status from eventos_agenda where id=%s",
                          (ev_id,)).fetchone()[0] == "ativo"


def test_editar_a_data_com_sinal_pago_remarca_o_compromisso(cliente):
    """Quem pagou não perde a data — mas se a festa mudou de dia, o compromisso
    acompanha. O vínculo continua, então uma re-aprovação não criaria outro."""
    oid, ev_id, quando = _aprovado_com_data(cliente, sinal_pago=True)
    nova = (quando + timedelta(days=7)).date().isoformat()
    r = cliente.post("/painel/servicos/salvar", json=_payload(oid, data=nova))
    assert r.json()["reaberta"] == {"liberou": False, "remarcou": True}
    with cliente.pool.connection() as cx:
        ini, st = cx.execute("select inicio, status from eventos_agenda where id=%s",
                             (ev_id,)).fetchone()
    assert st == "ativo" and ini.astimezone(ag.BRT).date().isoformat() == nova


def test_orcamento_com_contrato_assinado_nao_pode_ser_editado(cliente):
    """O buraco que a 164 fecha: a trava de edição só barrava `status='fechado'`, e
    um contrato ASSINADO — texto congelado, aceite e IP do cliente — não impedia
    ninguém de mudar os itens e valores do orçamento por baixo. O documento
    assinado passava a dizer uma coisa e o sistema outra."""
    oid, _ev, quando = _aprovado_com_data(cliente, sinal_pago=True)
    with cliente.pool.connection() as cx:
        cx.execute("""insert into contratos (conta_id, numero, orcamento_id, status,
                        texto, assinado_em, assinado_por)
                      values (%s, 90, %s, 'assinado', '[]'::jsonb, now(), 'Ana')""",
                   (CONTA, oid))
        cx.commit()
    r = cliente.post("/painel/servicos/salvar",
                     json=_payload(oid, data=quando.date().isoformat()))
    assert r.status_code == 409 and "aditivo" in r.json()["erro"]
    with cliente.pool.connection() as cx:
        # e nada foi tocado: o status segue 'aprovada', não virou 'enviado'
        assert cx.execute("select status from orcamentos where id=%s",
                          (oid,)).fetchone()[0] == "aprovada"


def test_contrato_so_enviado_nao_trava_a_edicao(cliente):
    """A trava é sobre ASSINADO. Contrato criado e ainda não assinado não congelou
    nada — editar continua sendo o caminho normal da negociação."""
    oid, _ev, quando = _aprovado_com_data(cliente, sinal_pago=True)
    with cliente.pool.connection() as cx:
        cx.execute("""insert into contratos (conta_id, numero, orcamento_id, status)
                      values (%s, 91, %s, 'enviado')""", (CONTA, oid))
        cx.commit()
    r = cliente.post("/painel/servicos/salvar",
                     json=_payload(oid, data=quando.date().isoformat()))
    assert r.status_code == 200


def test_editar_proposta_nao_aprovada_nao_mexe_em_data_nenhuma(cliente):
    """Regressão: editar um rascunho é o caminho de sempre e não tem caminho de
    volta pra percorrer."""
    with cliente.pool.connection() as cx:
        oid = cx.execute(
            """insert into orcamentos (conta_id, cliente, empresa, status, criado_por,
                 setup_centavos, modo) values (%s,'Bia','Bia','enviado','',300000,'evento')
               returning id""", (CONTA,)).fetchone()[0]
        cx.commit()
    r = cliente.post("/painel/servicos/salvar", json=_payload(oid, data="2026-12-20"))
    assert r.status_code == 200 and "reaberta" not in r.json()


def test_sinal_confirmado_mesmo_sem_agenda_nao_perde_o_pagamento(cliente):
    """A agenda é o segundo passo, não o primeiro: orçamento com sinal mas sem
    compromisso vinculado (caso raro — a reserva falhou na assinatura) ainda
    registra o pagamento."""
    with cliente.pool.connection() as cx:
        oid = cx.execute(
            """insert into orcamentos (conta_id, cliente, empresa, status, criado_por,
                 setup_centavos, modo, sinal_centavos)
               values (%s,'Bia','Bia','aprovada','',300000,'evento',50000) returning id""",
            (CONTA,)).fetchone()[0]
        cx.commit()
    r = cliente.post("/painel/servicos/sinal-recebido", json={"id": oid})
    assert r.status_code == 200 and r.json()["reserva_firmada"] is False
    with cliente.pool.connection() as cx:
        assert cx.execute("select sinal_pago_em from orcamentos where id=%s",
                          (oid,)).fetchone()[0] is not None
