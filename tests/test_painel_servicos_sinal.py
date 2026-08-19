"""O botão "Sinal recebido" do funil, pelas ROTAS de verdade.

Testar `agenda.confirmar_pre_reserva` direto não prova nada sobre a tela: já
aconteceu neste repo de o mecanismo estar certo e o botão ser um no-op porque a
rota não chamava ninguém. Aqui passa-se pelo HTTP:

  GET  /painel/servicos/lista        -> é ele que decide se o botão APARECE
  POST /painel/servicos/sinal-recebido -> é ele que firma a data

Banco dedicado e descartável, no padrão de tests/test_orcamento_excluir.py.
"""
import json
import os
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from psycopg_pool import ConnectionPool
from starlette.middleware.sessions import SessionMiddleware

from finance import agenda as ag
from finance import contrato as ctr
from finance import vendas
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
        # lancamentos existe pelo MESMO motivo que titulos: o sinal agora entra no
        # livro-caixa na hora em que cai, e `dar_baixa_titulo` escreve aqui. Sem a
        # tabela, a baixa estouraria dentro do try/except de `confirmar_sinal` e o
        # teste veria "não lançou" sem saber que foi por falta de schema.
        c.execute("""create table lancamentos (id bigserial primary key, conta_id bigint,
            membro_id bigint, tipo text not null, valor_centavos bigint not null,
            categoria text not null default '', descricao text not null default '',
            data date not null, pagamento text default '', forma_pagamento text default '',
            origem text default 'manual', comprovante text default '', chave text,
            natureza text default 'empresa', plano_conta_id bigint,
            centro_custo_id bigint, criado_em timestamptz default now())""")
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
                    "cep", "cidade", "uf", "email_empresa", "telefone", "cnae",
                    # logo_url: `proposta._carregar` lê — e é ele que a rota
                    # "Marcar agora" usa pra remontar o orçamento
                    "logo_url"):
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


# ------------------------------------- quem abre o financeiro é a ASSINATURA
#
# O buraco, medido em produção em 16/08/2026: `fechar_orcamento` só olhava
# `status <> 'fechado'`. Deu pra gerar contas a receber e lançar receita (R$
# 2.940,00, lançamento 622) de um negócio cujo contrato estava `enviado`, sem
# assinatura nenhuma. E o botão se chamava "Fechar contrato".

def _com_plano(c, *, sinal=294000, resto=686000, conta_id=CONTA):
    """Orçamento de evento aprovado, com plano de pagamento e contrato ainda sem
    assinar — o estado exato em que o fechamento tem que ser recusado."""
    with c.pool.connection() as cx:
        oid = cx.execute(
            """insert into orcamentos (conta_id, cliente, empresa, status, criado_por,
                 setup_centavos, primeiro_ano_centavos, modo, parcelas, sinal_centavos)
               values (%s,'Marina','Marina','aprovada','',%s,%s,'evento',%s::jsonb,%s)
               returning id""",
            (conta_id, sinal + resto, sinal + resto,
             '[{"obs":"Sinal — confirma a reserva da data","venc":"2026-08-22",'
             f'"forma":"Pix","valor_centavos":{sinal}}},'
             '{"obs":"Restante","venc":"2026-10-14","forma":"Pix",'
             f'"valor_centavos":{resto}}}]', sinal)).fetchone()[0]
        ct = cx.execute(
            """insert into contratos (conta_id, numero, orcamento_id, status, token)
               values (%s,(select coalesce(max(numero),0)+1 from contratos where conta_id=%s),
                       %s,'enviado',%s) returning id""",
            (conta_id, conta_id, oid, f"tok{oid}")).fetchone()[0]
        cx.commit()
    return oid, ct


def _titulos(c, oid):
    with c.pool.connection() as cx:
        return cx.execute(
            """select parcela_idx, valor_centavos, status, lancamento_id
                 from titulos where orcamento_id=%s order by parcela_idx""",
            (oid,)).fetchall()


def test_fechar_e_recusado_enquanto_o_cliente_nao_assinar(cliente):
    """O pedido do dono, virado regra do sistema: sem assinatura, nada de contas a
    receber. A trava fica no servidor porque o pedido vem do navegador."""
    oid, _ = _com_plano(cliente)
    r = cliente.post("/painel/servicos/fechar", json={"id": oid})
    assert r.status_code == 400
    assert "assinou" in r.json()["erro"]
    assert _titulos(cliente, oid) == []
    with cliente.pool.connection() as cx:
        assert cx.execute("select status from orcamentos where id=%s",
                          (oid,)).fetchone()[0] == "aprovada"


def test_a_assinatura_e_que_gera_as_contas_a_receber(cliente):
    """E é o único caminho: assinar fecha o negócio e abre o financeiro de uma vez."""
    oid, ct_id = _com_plano(cliente)
    assert ctr.assinar(cliente.pool, CONTA, ct_id, [{"titulo": "C1", "corpo": "x"}],
                       "Marina Souza", "123", "1.2.3.4") is True
    assert [(i, v, s) for i, v, s, _ in _titulos(cliente, oid)] == [
        (0, 294000, "aberto"), (1, 686000, "aberto")]
    with cliente.pool.connection() as cx:
        assert cx.execute("select status from orcamentos where id=%s",
                          (oid,)).fetchone()[0] == "fechado"


def test_o_sinal_entra_no_caixa_quando_cai_e_nao_no_fechamento(cliente):
    """O nó que o desenho novo desata. O sinal é dinheiro que JÁ entrou; segurar o
    lançamento até a assinatura seria recusar-se a registrar dinheiro recebido.
    Só o título DELE nasce — o resto do plano continua esperando a assinatura."""
    oid, _ = _com_plano(cliente)
    r = cliente.post("/painel/servicos/sinal-recebido", json={"id": oid})
    assert r.status_code == 200 and r.json()["titulo_baixado"]
    linhas = _titulos(cliente, oid)
    assert len(linhas) == 1, "só o título do sinal, e nada do resto do plano"
    idx, valor, status, lanc = linhas[0]
    assert (idx, valor, status) == (0, 294000, "pago")
    assert lanc, "o dinheiro tem que ter chegado ao livro-caixa"
    with cliente.pool.connection() as cx:
        assert cx.execute("select valor_centavos, tipo from lancamentos where id=%s",
                          (lanc,)).fetchone() == (294000, "receita")
        # e o negócio NÃO fechou por causa disso
        assert cx.execute("select status from orcamentos where id=%s",
                          (oid,)).fetchone()[0] == "aprovada"


def test_o_sinal_nao_vira_titulo_duas_vezes(cliente):
    """Com o sinal nascendo antes e a assinatura gerando o resto, a segunda etapa
    tem que PULAR o que já existe. Sem isso a receita do sinal dobrava."""
    oid, ct_id = _com_plano(cliente)
    cliente.post("/painel/servicos/sinal-recebido", json={"id": oid})
    ctr.assinar(cliente.pool, CONTA, ct_id, [{"titulo": "C1", "corpo": "x"}],
                "Marina", "123", "1.2.3.4")
    linhas = _titulos(cliente, oid)
    assert [(i, v, s) for i, v, s, _ in linhas] == [
        (0, 294000, "pago"), (1, 686000, "aberto")]
    with cliente.pool.connection() as cx:
        assert cx.execute("select count(*), coalesce(sum(valor_centavos),0) "
                          "from lancamentos where conta_id=%s", (CONTA,)).fetchone() \
            == (1, 294000), "o sinal não pode ter sido lançado duas vezes"


def test_orcamento_sem_plano_de_pagamento_ainda_gera_o_titulo_do_total(cliente):
    """A regressão que o pulo poderia causar: o fallback do título único olhava
    `if not ids`, e agora `ids` pode voltar vazio porque tudo já existia. Se
    continuasse olhando `ids`, um evento COM parcelas ganharia um título extra do
    total por cima — dobrando a receita."""
    with cliente.pool.connection() as cx:
        oid = cx.execute(
            """insert into orcamentos (conta_id, cliente, empresa, status, criado_por,
                 setup_centavos, primeiro_ano_centavos, modo)
               values (%s,'Sem plano','Sem plano','aprovada','',500000,500000,'evento')
               returning id""", (CONTA,)).fetchone()[0]
        ct = cx.execute(
            """insert into contratos (conta_id, numero, orcamento_id, status, token)
               values (%s,90,%s,'enviado','toksemplano') returning id""",
            (CONTA, oid)).fetchone()[0]
        cx.commit()
    ctr.assinar(cliente.pool, CONTA, ct, [{"titulo": "C1", "corpo": "x"}],
                "Alguém", "1", "1.2.3.4")
    assert [(i, v, s) for i, v, s, _ in _titulos(cliente, oid)] == [(None, 500000, "aberto")]


def test_conta_sem_contrato_fecha_pelo_botao_como_sempre(cliente):
    """O escopo, de novo: os nichos recorrentes não têm documento pra assinar, então
    a trava não pode alcançá-los. A conta OUTRA não é de eventos."""
    with cliente.pool.connection() as cx:
        oid = cx.execute(
            """insert into orcamentos (conta_id, cliente, empresa, status, criado_por,
                 setup_centavos, mensal_centavos, modo)
               values (%s,'Clínica','Clínica','aprovada','',920000,45000,'recorrente')
               returning id""", (OUTRA,)).fetchone()[0]
        cx.commit()
    r = vendas.fechar_orcamento(cliente.pool, OUTRA, oid)
    assert r["ok"] is True and r["modo"] == "recorrente"
    assert r["setup_titulo_id"] and r["mensal_titulo_id"]


def test_plano_de_parcela_unica_nao_ganha_titulo_do_total_por_cima(cliente):
    """O caso que expõe o fallback: pagamento à vista na reserva — o plano TEM uma
    parcela, e ela é o sinal. Quando ele cai, o título nasce; na assinatura o laço
    pula essa parcela e `ids` volta VAZIO.

    Se o fallback do título único ainda olhasse `if not ids`, ele concluiria "esse
    evento não tem plano de pagamento" e criaria um título do TOTAL por cima do que
    já estava pago — dobrando a receita do evento. Por isso a condição olha
    `parcelas`, que é a pergunta que ele queria fazer desde sempre."""
    with cliente.pool.connection() as cx:
        oid = cx.execute(
            """insert into orcamentos (conta_id, cliente, empresa, status, criado_por,
                 setup_centavos, primeiro_ano_centavos, modo, parcelas, sinal_centavos)
               values (%s,'À vista','À vista','aprovada','',400000,400000,'evento',
                 '[{"obs":"Sinal — confirma a reserva da data","venc":"2026-08-22",
                    "forma":"Pix","valor_centavos":400000}]'::jsonb,400000)
               returning id""", (CONTA,)).fetchone()[0]
        ct = cx.execute(
            """insert into contratos (conta_id, numero, orcamento_id, status, token)
               values (%s,91,%s,'enviado','tokavista') returning id""",
            (CONTA, oid)).fetchone()[0]
        cx.commit()
    cliente.post("/painel/servicos/sinal-recebido", json={"id": oid})
    ctr.assinar(cliente.pool, CONTA, ct, [{"titulo": "C1", "corpo": "x"}],
                "À vista", "1", "1.2.3.4")
    assert [(i, v, s) for i, v, s, _ in _titulos(cliente, oid)] == [(0, 400000, "pago")]
    with cliente.pool.connection() as cx:
        assert cx.execute("select coalesce(sum(valor_centavos),0) from titulos "
                          "where orcamento_id=%s", (oid,)).fetchone()[0] == 400000


def test_assinar_orcamento_que_ja_estava_fechado_nao_quebra_nem_duplica(cliente):
    """O ESTADO LEGADO, que existe de verdade: o orçamento nº 3 da conta 34 ficou
    `fechado` com o contrato `enviado`, porque o botão antigo não olhava assinatura.
    Registro assim não vai ser normalizado — o passado fica quieto —, então o
    caminho tem que aguentar receber a assinatura depois.

    `fechar_orcamento` devolve "já está 'fechado'" e a assinatura segue de pé: o
    que não pode é estourar nem gerar os títulos de novo por cima dos que existem."""
    oid, ct_id = _com_plano(cliente)
    with cliente.pool.connection() as cx:                 # o estado de antes da trava
        cx.execute("update orcamentos set status='fechado' where id=%s", (oid,))
        cx.execute("""insert into titulos (conta_id, tipo, descricao, valor_centavos,
                        vencimento, orcamento_id, parcela_idx)
                      values (%s,'receber','Evento — Marina · Sinal',294000,
                              '2026-08-22',%s,0),
                             (%s,'receber','Evento — Marina · Restante',686000,
                              '2026-10-14',%s,1)""", (CONTA, oid, CONTA, oid))
        cx.commit()
    assert ctr.assinar(cliente.pool, CONTA, ct_id, [{"titulo": "C1", "corpo": "x"}],
                       "Marina", "1", "1.2.3.4") is True
    assert len(_titulos(cliente, oid)) == 2, "não pode ter gerado o plano de novo"
    with cliente.pool.connection() as cx:
        assert cx.execute("select assinado_em is not null, status from contratos "
                          "where id=%s", (ct_id,)).fetchone() == (True, "assinado")


# ─────────────────────────────── DESCONTO, pelas rotas de verdade
#
# O que importa aqui não é a conta (essa está em test_desconto.py, pura) — é a
# FIAÇÃO: o servidor recalcula em vez de acreditar no total que a tela mandou, o
# desconto sobrevive a reabrir a proposta, e nada disso vaza pro que não pediu.

def _corpo(**extra):
    d = {"cliente": "Marina", "empresa": "Marina", "n_modulos": 3,
         "setup": 17060,           # BRUTO: 12.400 + 1.860 + 2.800
         "mensal": 0, "primeiro_ano": 17060,
         "itens": [
             {"nome": "Pacote", "setup": 12400, "mensal": 0, "qtd": 1,
              "unitario": 12400, "desc_tipo": "pct", "desc_val": 5},
             {"nome": "Hora extra", "setup": 1860, "mensal": 0, "qtd": 3,
              "unitario": 620, "desc_tipo": "valor", "desc_val": 360},
             {"nome": "Cerimonial", "setup": 2800, "mensal": 0, "qtd": 1,
              "unitario": 2800},
         ],
         "evento": {"data": "2026-12-05", "inicio": "19:00", "fim": "23:00",
                    "tipo": "Formatura", "convidados": 150}}
    d.update(extra)
    return d


def _orc(c, oid):
    with c.pool.connection() as cx:
        return cx.execute(
            """select setup_centavos, primeiro_ano_centavos, desconto_tipo,
                      desconto_pct, desconto_centavos
                 from orcamentos where id=%s""", (oid,)).fetchone()


def test_a_conta_do_mockup_chega_inteira_pela_rota(cliente):
    """Os mesmos números do mockup aprovado, atravessando o HTTP: bruto 17.060,
    descontos de item 980, final 10% sobre 16.080, total 14.472."""
    r = cliente.post("/painel/servicos/salvar",
                     json=_corpo(desconto_tipo="pct", desconto_pct=10))
    assert r.status_code == 200
    bruto, total, tipo, pct, cent = _orc(cliente, r.json()["id"])
    assert bruto == 1706000, "setup_centavos continua sendo o BRUTO"
    assert total == 1447200, "primeiro_ano_centavos é o líquido"
    assert (tipo, float(pct), cent) == ("pct", 10.0, 0)


def test_o_total_nao_vem_mais_do_navegador(cliente):
    """A trava que a mudança abriu espaço pra colocar: `primeiro_ano` era gravado
    como veio. Bastava editar o JSON na aba pra fechar um orçamento por qualquer
    valor — e o título a receber sairia com ele."""
    r = cliente.post("/painel/servicos/salvar",
                     json=_corpo(desconto_tipo="pct", desconto_pct=10,
                                 primeiro_ano=1))       # a mentira
    assert _orc(cliente, r.json()["id"])[1] == 1447200


def test_desconto_em_reais_no_total(cliente):
    r = cliente.post("/painel/servicos/salvar",
                     json=_corpo(desconto_tipo="valor", desconto_valor=1000))
    bruto, total, tipo, pct, cent = _orc(cliente, r.json()["id"])
    assert (tipo, cent) == ("valor", 100000)
    assert total == 1608000 - 100000        # subtotal com descontos − R$ 1.000


def test_desconto_maior_que_o_orcamento_nao_vira_acrescimo(cliente):
    """Sem teto, um R$ digitado com um zero a mais faria total negativo — e daí
    saem título, parcela e margem negativos."""
    r = cliente.post("/painel/servicos/salvar",
                     json=_corpo(desconto_tipo="valor", desconto_valor=999999))
    assert _orc(cliente, r.json()["id"])[1] == 0


def test_sem_desconto_o_total_continua_o_de_sempre(cliente):
    """A regressão que mais importa: quem nunca usou desconto não pode ver número
    diferente depois desta mudança. Itens LIMPOS, sem desconto em lugar nenhum."""
    limpos = [{k: v for k, v in it.items() if not k.startswith("desc_")}
              for it in _corpo()["itens"]]
    r = cliente.post("/painel/servicos/salvar", json=_corpo(itens=limpos))
    bruto, total, tipo, pct, cent = _orc(cliente, r.json()["id"])
    assert bruto == 1706000 and total == 1706000
    assert (tipo, float(pct), cent) == ("pct", 0.0, 0)


def test_o_desconto_volta_quando_a_proposta_e_reaberta(cliente):
    """Sem isto, reabrir pra trocar uma vírgula zeraria o desconto negociado — e o
    cliente receberia um link mais caro que o que ele aprovou."""
    oid = cliente.post("/painel/servicos/salvar",
                       json=_corpo(desconto_tipo="valor",
                                   desconto_valor=1000)).json()["id"]
    r = cliente.get(f"/painel/servicos/item/{oid}")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["desconto_tipo"] == "valor" and d["desconto_valor"] == 1000
    # e o desconto de CADA linha volta junto
    por_nome = {i["nome"]: i for i in d["itens"]}
    assert por_nome["Pacote"]["desc_tipo"] == "pct" and por_nome["Pacote"]["desc_val"] == 5
    assert por_nome["Hora extra"]["desc_tipo"] == "valor"
    assert por_nome["Hora extra"]["desc_val"] == 360
    assert por_nome["Cerimonial"]["desc_val"] == 0


def test_reabrir_e_salvar_de_novo_nao_desconta_duas_vezes(cliente):
    """O erro clássico deste desenho: a tela devolve o total já descontado, o
    servidor desconta de novo, e cada edição encolhe a proposta."""
    oid = cliente.post("/painel/servicos/salvar",
                       json=_corpo(desconto_tipo="pct", desconto_pct=10)).json()["id"]
    primeiro = _orc(cliente, oid)[1]
    cliente.post("/painel/servicos/salvar",
                 json=_corpo(id=oid, desconto_tipo="pct", desconto_pct=10))
    assert _orc(cliente, oid)[1] == primeiro == 1447200


# ============================================ o ESTADO DA DATA na linha do funil
#
# O botão "Sinal recebido" acima cobre UM dos quatro estados da data. Em
# 19/08/2026 apareceu um orçamento aprovado que nunca virou pré-reserva, e o
# problema não era só a porta por onde ele escapou: a linha do funil desenhava
# só a pré-reserva correndo. "Firme", "nunca entrou" e "liberada" ficavam com a
# mesma cara — e duas delas são data perdida.
#
# A regra mora em vendas.estado_da_data (testada pura em test_estado_da_data.py).
# Aqui prova-se o que só a ROTA responde: que o campo chega na tela e que o botão
# de conserto conserta.

def _orcamento_aprovado_sem_data_na_agenda(c, *, conta_id=CONTA, inicio="19:00"):
    """Aprovado, com data de evento no futuro, e SEM compromisso — exatamente o
    estado que passava despercebido."""
    quando = (ag.agora_brt() + timedelta(days=45)).date().isoformat()
    with c.pool.connection() as cx:
        oid = cx.execute(
            """insert into orcamentos (conta_id, cliente, empresa, status, criado_por,
                 setup_centavos, modo, evento, token, numero)
               values (%s,'Bruno','Bruno','aprovada','',500000,'evento',
                       %s::jsonb, %s, 77) returning id""",
            (conta_id, json.dumps({"data": quando, "inicio": inicio, "tipo": "Casamento"}),
             f"tok{conta_id}{inicio or 'x'}")).fetchone()[0]
        cx.commit()
    return oid


def test_a_linha_diz_que_a_data_esta_segurada(cliente):
    oid, _ = _orcamento_com_data_segurada(cliente)
    d = _item(cliente, oid)["data"]
    assert d["estado"] == vendas.DATA_SEGURADA and d["acao"] == "sinal"


def test_a_linha_denuncia_a_data_que_ficou_fora_da_agenda(cliente):
    """Sem este campo a tela não tem como desenhar o selo — e foi assim que um
    orçamento aprovado ficou sem data sem ninguém perceber."""
    oid = _orcamento_aprovado_sem_data_na_agenda(cliente)
    d = _item(cliente, oid)["data"]
    assert d["estado"] == vendas.DATA_FORA and d["acao"] == "marcar"


def test_confirmar_o_sinal_deixa_a_linha_dizendo_data_reservada(cliente):
    """O estado que antes não tinha selo nenhum: firme. Depois do sinal a linha
    ficava muda, indistinguível de quem nunca entrou na agenda."""
    oid, _ = _orcamento_com_data_segurada(cliente)
    cliente.post("/painel/servicos/sinal-recebido", json={"id": oid})
    assert _item(cliente, oid)["data"]["estado"] == vendas.DATA_RESERVADA


def test_prazo_vencido_acende_o_selo_de_data_liberada(cliente):
    """A sequência de verdade: ninguém pagou, o robô expirou a pré-reserva. Antes
    a linha voltava a parecer normal."""
    oid, ev_id = _orcamento_com_data_segurada(cliente, dias=1)
    with cliente.pool.connection() as cx:
        cx.execute("update eventos_agenda set pre_reserva_ate=now() - interval '1 hour' "
                   "where id=%s", (ev_id,))
        cx.commit()
    ag.expirar_pre_reservas(cliente.pool, ag.agora_brt())
    d = _item(cliente, oid)["data"]
    assert d["estado"] == vendas.DATA_LIBERADA and d["acao"] == "resegurar"


def test_proposta_ainda_nao_aprovada_nao_fala_de_data(cliente):
    oid = _orcamento_aprovado_sem_data_na_agenda(cliente)
    with cliente.pool.connection() as cx:
        cx.execute("update orcamentos set status='enviado' where id=%s", (oid,))
        cx.commit()
    assert _item(cliente, oid)["data"] is None


# ------------------------------------------------------- o botão que conserta

def test_marcar_agora_poe_a_data_na_agenda(cliente):
    oid = _orcamento_aprovado_sem_data_na_agenda(cliente)
    r = cliente.post("/painel/servicos/marcar-data", json={"id": oid})
    assert r.status_code == 200, r.text
    with cliente.pool.connection() as cx:
        ev_id = cx.execute("select evento_agenda_id from orcamentos where id=%s",
                           (oid,)).fetchone()[0]
        assert ev_id == r.json()["evento_id"]
    assert _item(cliente, oid)["data"]["estado"] in (vendas.DATA_RESERVADA,
                                                     vendas.DATA_SEGURADA)


def test_marcar_duas_vezes_nao_cria_dois_compromissos(cliente):
    """A rota usa a mesma função da aprovação, que já é idempotente. Duplo clique
    numa empresa que vende data criaria dois compromissos no mesmo horário — e o
    aviso de choque dispararia contra o próprio orçamento."""
    oid = _orcamento_aprovado_sem_data_na_agenda(cliente)
    cliente.post("/painel/servicos/marcar-data", json={"id": oid})
    r2 = cliente.post("/painel/servicos/marcar-data", json={"id": oid})
    assert r2.status_code == 409
    with cliente.pool.connection() as cx:
        assert cx.execute("select count(*) from eventos_agenda where conta_id=%s",
                          (CONTA,)).fetchone()[0] == 1


def test_sem_a_hora_de_inicio_o_botao_diz_qual_campo_falta(cliente):
    """A porta mais larga. "Não consegui marcar" mandaria o vendedor procurar no
    escuro justamente o campo que a linha do funil já apontou."""
    oid = _orcamento_aprovado_sem_data_na_agenda(cliente, inicio="")
    r = cliente.post("/painel/servicos/marcar-data", json={"id": oid})
    assert r.status_code == 400
    assert "hora de início" in r.json()["erro"]


def test_segurar_de_novo_solta_o_compromisso_vencido_e_cria_outro(cliente):
    """O compromisso cancelado NÃO é ressuscitado: fica como histórico de que a
    data chegou a vencer, e o orçamento ganha um novo."""
    oid, ev_id = _orcamento_com_data_segurada(cliente, dias=1)
    with cliente.pool.connection() as cx:
        cx.execute("update orcamentos set evento=%s::jsonb where id=%s",
                   (json.dumps({"data": (ag.agora_brt() + timedelta(days=30)).date().isoformat(),
                                "inicio": "19:00"}), oid))
        cx.execute("update eventos_agenda set pre_reserva_ate=now() - interval '1 hour' "
                   "where id=%s", (ev_id,))
        cx.commit()
    ag.expirar_pre_reservas(cliente.pool, ag.agora_brt())
    r = cliente.post("/painel/servicos/marcar-data", json={"id": oid})
    assert r.status_code == 200, r.text
    novo = r.json()["evento_id"]
    assert novo != ev_id
    with cliente.pool.connection() as cx:
        assert cx.execute("select status from eventos_agenda where id=%s",
                          (ev_id,)).fetchone()[0] == "cancelado"


def test_orcamento_de_outra_conta_nao_se_marca_daqui(cliente):
    oid = _orcamento_aprovado_sem_data_na_agenda(cliente, conta_id=OUTRA)
    assert cliente.post("/painel/servicos/marcar-data", json={"id": oid}).status_code == 404


def test_proposta_nao_aprovada_nao_marca_data(cliente):
    oid = _orcamento_aprovado_sem_data_na_agenda(cliente)
    with cliente.pool.connection() as cx:
        cx.execute("update orcamentos set status='enviado' where id=%s", (oid,))
        cx.commit()
    assert cliente.post("/painel/servicos/marcar-data", json={"id": oid}).status_code == 400
