"""Mandar o CONTRATO por e-mail — a ação que existia e não tinha por onde clicar.

O DEFEITO
A capacidade estava pronta no servidor (`?alvo=contrato` na prévia e no envio), e
na tela existia um botão verde "Mandar o contrato pra assinar". Só que o botão
verde é UM SÓ por linha: `vendas._ORDEM_ACAO` põe `marcar`, `resegurar`, `sinal` e
`comprovante` na frente de `assinar`, então bastava a linha ter outra pendência
pra o e-mail do contrato sumir da tela. E depois de ASSINADO sumia de vez — a ação
vira "Fechar negócio" e, com o negócio fechado, não sobra ação nenhuma.

Justamente aí é quando o cliente liga pedindo a via dele.

No menu "Ações ▾" o grupo Contrato tinha só *Abrir* e *Copiar link*, enquanto o
grupo Proposta logo acima já tinha *Mandar por e-mail*. Medido na conta 34 (Prime
Eventos) em 02/09/2026: 4 contratos, 1 deles assinado com o orçamento fechado —
esse não tinha nenhum caminho pra ser reenviado.

O QUE ESTE ARQUIVO PRENDE
1. o menu oferece o envio do contrato, e ele aponta pro alvo certo;
2. a prévia e o envio dizem A MESMA COISA — assunto, mensagem e link. Eles já
   divergiram uma vez: a prévia mostrava contrato e o envio mandava o link da
   proposta, e o cliente recebia um e-mail com cara de contrato que não assinava
   nada (produção, Prime Eventos/Bianca, 28/08);
3. contrato assinado pede a VIA, não a assinatura — texto e título mudam;
4. reenviar contrato assinado não desassina nada.
"""
import os
import re

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from psycopg_pool import ConnectionPool
from starlette.middleware.sessions import SessionMiddleware

from finance import proposta_email as pmail
from web import painel_servicos as ps

CONTA = 34


# ══════════════════════════ os textos, sem tela nem banco ══════════════════════

def test_assunto_do_contrato_leva_numero_e_empresa():
    assert pmail.assunto_contrato(7, "Prime Eventos") == "Contrato nº 7 — Prime Eventos"


def test_assunto_do_contrato_sem_numero_nao_vira_no_vazio():
    """`Contrato nº  — Prime` era o que a interpolação crua produzia."""
    assert pmail.assunto_contrato(None, "Prime Eventos") == "Contrato — Prime Eventos"
    assert "nº" not in pmail.assunto_contrato(None, "Prime Eventos")


def test_texto_pede_assinatura_enquanto_falta_assinar():
    t = pmail.texto_contrato("Bianca Souza", assinado=False)
    assert "Olá, Bianca!" in t
    assert "assinar" in t


def test_texto_de_contrato_assinado_manda_a_via_e_nao_pede_assinatura():
    """Pedir "abra e assine" num contrato JÁ assinado faz o cliente achar que a
    assinatura dele não valeu."""
    t = pmail.texto_contrato("Bianca Souza", assinado=True)
    assert "assinado" in t
    assert "assine" not in t and "para leitura e assinatura" not in t


def test_sem_nome_do_cliente_nao_sai_ola_virgula():
    for assinado in (False, True):
        assert pmail.texto_contrato("", assinado=assinado).startswith("Olá!")
        assert pmail.texto_contrato(None, assinado=assinado).startswith("Olá!")


# ══════════════════════════ o menu da linha do funil ═══════════════════════════

def _grupo_contrato() -> str:
    """O trecho do JS entre `if(it.contrato_token){` e o fechamento do bloco."""
    src = ps._SERVICOS_TPL if hasattr(ps, "_SERVICOS_TPL") else _tpl_de_servicos()
    i = src.index("if(it.contrato_token){")
    return src[i:src.index("if(it.pgto", i)]


def _tpl_de_servicos() -> str:
    from web.portal import _env
    return _env.loader.mapping["servicos"]


def test_o_menu_oferece_mandar_o_contrato_por_email():
    """O CASO DO BUG: o grupo Contrato tinha Abrir e Copiar link, e só."""
    g = _grupo_contrato()
    assert "abrirEnvio(it.id,'contrato')" in g, \
        "sem isto, mandar o contrato só existe no botão verde — que some"


def test_o_menu_nomeia_o_pedido_conforme_o_estado_do_contrato():
    g = _grupo_contrato()
    assert "Mandar a via assinada" in g and "Mandar pra assinar" in g


def test_o_menu_diz_se_o_contrato_ja_foi_mandado():
    """Sem isso o vendedor manda de novo "por via das dúvidas" — é o mesmo dado
    que o grupo Proposta já mostra em `enviado_em`."""
    g = _grupo_contrato()
    assert "contrato_enviado_em" in g
    assert "nunca mandado" in g


def test_o_grupo_proposta_continua_com_o_envio_dele():
    """Trilho: o envio da PROPOSTA é o que já funcionava, e não pode ter sido
    trocado pelo do contrato."""
    src = _tpl_de_servicos()
    i = src.index("_mgrupo('Proposta'")
    trecho = src[i:src.index("if(it.contrato_token){", i)]
    assert "abrirEnvio(it.id)" in trecho
    assert "'contrato'" not in trecho


# ══════════════════════════ as rotas, com banco de verdade ═════════════════════

@pytest.fixture()
def cliente(monkeypatch):
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_contrato_envio"
    with admin.connection() as c:
        c.autocommit = True
        c.execute("select pg_terminate_backend(pid) from pg_stat_activity "
                  "where datname=%s and pid <> pg_backend_pid()", (dbname,))
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    pool = ConnectionPool(url, min_size=1, max_size=3, open=True,
                          kwargs={"prepare_threshold": None})
    with pool.connection() as c:
        c.execute("create table contas (id bigserial primary key, nome text, chip_de bigint)")
        c.execute("create table membros (id bigserial primary key, conta_id bigint, nome text)")
        c.execute("insert into contas (id, nome) values (%s,'Prime Eventos')", (CONTA,))
        c.commit()
    with pool.connection() as c:
        ps._garantir_tabela(c)
    with pool.connection() as c:
        c.execute("""create table contratos (id bigserial primary key, conta_id bigint,
                       numero int, orcamento_id bigint, status text default 'enviado',
                       texto text default '', valor_centavos bigint default 0,
                       assinado_em timestamptz, assinado_por text, assinado_doc text,
                       assinado_ip text, rescindido_em timestamptz, rescisao_motivo text,
                       substitui_id bigint, criado_em timestamptz default now(),
                       token text, enviado_em timestamptz)""")
        # `orcamento_envios` NÃO entra aqui: `ps._garantir_tabela` acima já a cria,
        # e recriá-la esconderia justamente o dia em que ela sair de lá.
        # `eventos_agenda` a lista consulta pra saber o estado da data (é nicho
        # eventos); as duas colunas abaixo são as que ela lê.
        c.execute("""create table eventos_agenda (id bigserial primary key, conta_id bigint,
                       status text, pre_reserva_ate timestamptz)""")
        # `clientes` é de onde sai o NOME da linha (o campo do cadastro, não o
        # texto livre do orçamento).
        c.execute("create table clientes (id bigserial primary key, nome text)")
        c.commit()

    monkeypatch.setattr(ps, "get_pool", lambda: pool)
    monkeypatch.setattr(ps.scat, "garantir_tabela", lambda pool: None)
    monkeypatch.setattr(ps.emp, "obter_dados_empresa",
                        lambda pool, cid: {"nicho": "eventos", "nome_fantasia": "Prime Eventos",
                                           "razao_social": "PRIME LTDA", "telefone": "",
                                           "email_empresa": ""})
    monkeypatch.setattr(ps.pmail, "remetente", lambda pool, cid, e: {"caixa": "zaq", "endereco": "x@zaq"})
    monkeypatch.setattr(ps.pmail, "historico", lambda pool, cid, oid: [])

    enviados = []

    def _enviar(pool, conta_id, *, destino, assunto, html, texto, empresa, reply_to=""):
        enviados.append({"destino": destino, "assunto": assunto, "html": html, "texto": texto})
        return {"ok": True, "erro": "", "remetente": "x@zaq"}
    monkeypatch.setattr(ps.pmail, "enviar", _enviar)
    monkeypatch.setattr(ps.pmail, "registrar",
                        lambda *a, **k: None)

    def _logada(request):
        conta = [None] * 15
        conta[0], conta[2], conta[11], conta[12], conta[14] = CONTA, "Prime Eventos", True, True, True
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

    cl = TestClient(app)
    cl.post("/_entrar")
    cl.pool = pool
    cl.enviados = enviados
    yield cl
    pool.close()


def _orcamento(pool, *, email="cliente@x.com"):
    with pool.connection() as c:
        oid = c.execute(
            """insert into orcamentos (conta_id, cliente, email, numero, status, modo, token)
               values (%s,'Bianca Souza',%s,9,'aprovada','evento','tok-orc') returning id""",
            (CONTA, email)).fetchone()[0]
        c.commit()
    return oid


def _contrato(pool, orc_id, *, numero=7, assinado=False):
    with pool.connection() as c:
        cid = c.execute(
            """insert into contratos (conta_id, numero, orcamento_id, token, status, assinado_em)
               values (%s,%s,%s,'tok-ct',%s,%s) returning id""",
            (CONTA, numero, orc_id, "assinado" if assinado else "enviado",
             "2026-08-28 12:00-03" if assinado else None)).fetchone()[0]
        c.commit()
    return cid


def test_previa_do_contrato_traz_link_assunto_e_estado(cliente):
    orc = _orcamento(cliente.pool)
    _contrato(cliente.pool, orc)

    d = cliente.get(f"/painel/servicos/email/{orc}?alvo=contrato").json()

    assert "/contrato/tok-ct" in d["link"]
    assert d["assunto"] == "Contrato nº 7 — Prime Eventos"
    assert d["assinado"] is False
    assert "assinar" in d["mensagem"]


def test_previa_de_contrato_assinado_muda_o_pedido(cliente):
    orc = _orcamento(cliente.pool)
    _contrato(cliente.pool, orc, assinado=True)

    d = cliente.get(f"/painel/servicos/email/{orc}?alvo=contrato").json()

    assert d["assinado"] is True
    assert "assinado" in d["mensagem"] and "assine" not in d["mensagem"]


def test_envio_manda_o_que_a_previa_prometeu(cliente):
    """A PROPRIEDADE QUE JÁ FOI QUEBRADA: prévia e envio tinham cada uma a sua
    cópia do texto, e divergiram — a tela mostrava contrato e o e-mail levava o
    link da proposta."""
    orc = _orcamento(cliente.pool)
    _contrato(cliente.pool, orc)

    d = cliente.get(f"/painel/servicos/email/{orc}?alvo=contrato").json()
    r = cliente.post("/painel/servicos/enviar-email",
                     json={"id": orc, "alvo": "contrato"})
    assert r.status_code == 200

    saiu = cliente.enviados[-1]
    assert saiu["assunto"] == d["assunto"], "o e-mail saiu com assunto diferente da prévia"
    assert d["link"] in saiu["texto"], "o e-mail levou outro link"
    assert "/proposta/" not in saiu["texto"], "mandou o link da proposta no e-mail do contrato"


def test_envio_de_contrato_assinado_leva_a_via_e_nao_pede_assinatura(cliente):
    orc = _orcamento(cliente.pool)
    _contrato(cliente.pool, orc, assinado=True)

    cliente.post("/painel/servicos/enviar-email", json={"id": orc, "alvo": "contrato"})

    saiu = cliente.enviados[-1]
    assert "/contrato/tok-ct" in saiu["texto"]
    assert "assinado" in saiu["texto"]


def test_reenviar_contrato_assinado_nao_desassina(cliente):
    """`enviado_em` é a única coisa que o envio pode tocar. Se ele encostasse em
    `status`/`assinado_em`, mandar a via pro cliente apagaria a assinatura."""
    orc = _orcamento(cliente.pool)
    ct = _contrato(cliente.pool, orc, assinado=True)
    with cliente.pool.connection() as c:
        antes = c.execute("select status, assinado_em from contratos where id=%s", (ct,)).fetchone()

    cliente.post("/painel/servicos/enviar-email", json={"id": orc, "alvo": "contrato"})

    with cliente.pool.connection() as c:
        depois = c.execute("select status, assinado_em from contratos where id=%s", (ct,)).fetchone()
        enviado = c.execute("select enviado_em from contratos where id=%s", (ct,)).fetchone()[0]
    assert depois == antes, "o reenvio mexeu no estado da assinatura"
    assert enviado is not None, "o reenvio precisa registrar que saiu de novo"


def test_proposta_sem_contrato_recusa_o_alvo(cliente):
    orc = _orcamento(cliente.pool)
    assert cliente.get(f"/painel/servicos/email/{orc}?alvo=contrato").status_code == 404
    assert cliente.post("/painel/servicos/enviar-email",
                        json={"id": orc, "alvo": "contrato"}).status_code == 404


def test_alvo_proposta_continua_mandando_a_proposta(cliente):
    """Trilho: o caminho que já funcionava não pode ter virado contrato."""
    orc = _orcamento(cliente.pool)
    _contrato(cliente.pool, orc)

    cliente.post("/painel/servicos/enviar-email", json={"id": orc})

    saiu = cliente.enviados[-1]
    assert "/proposta/tok-orc" in saiu["texto"]
    assert "/contrato/" not in saiu["texto"]


def test_a_linha_do_funil_publica_quando_o_contrato_foi_mandado(cliente):
    """O menu mostra "mandado 28/08 14:20" — e esse campo saía do JSON antes."""
    orc = _orcamento(cliente.pool)
    ct = _contrato(cliente.pool, orc)
    with cliente.pool.connection() as c:
        c.execute("update contratos set enviado_em = timestamptz '2026-08-28 14:20-03' "
                  "where id=%s", (ct,))
        c.commit()

    itens = cliente.get("/painel/servicos/lista").json()["itens"]
    linha = next(i for i in itens if i["id"] == orc)

    assert linha["contrato_enviado_em"] == "28/08 14:20"
    assert "_contrato_enviado_em" not in linha, "campo interno vazando pro JSON"


def test_contrato_nunca_mandado_vem_vazio_e_nao_none(cliente):
    orc = _orcamento(cliente.pool)
    _contrato(cliente.pool, orc)
    itens = cliente.get("/painel/servicos/lista").json()["itens"]
    linha = next(i for i in itens if i["id"] == orc)
    assert linha["contrato_enviado_em"] == ""
