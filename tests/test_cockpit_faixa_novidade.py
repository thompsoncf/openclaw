"""O ✕ da faixa de novidade, exercitado COMO O DEDO EXERCITA: a tela é renderizada,
o formulário é lido do HTML que saiu, e o POST vai pelo mesmo caminho do navegador.

POR QUE ESTE ARQUIVO EXISTE
Em 16/09/2026 o dono não conseguia fechar o aviso da Fila. Eu medi o alvo de toque
(29 x 22 px, contra os 44 x 44 do mínimo), consertei o tamanho — e CONTINUOU sem
fechar. O tamanho era um defeito real, mas não era ESTE defeito, e eu só descobri
isso porque o dono testou de novo.

O buraco no meu jeito de testar era o pedaço do meio: havia teste da consulta
(`finance.novidades`) e teste do HTML renderizado, e NENHUM que fosse do botão até
a linha no banco. O ✕ mora exatamente nesse pedaço.

Então aqui o teste faz o percurso inteiro, sem atalho: GET na Fila, acha o
`<form>` da faixa no HTML de verdade, manda o POST com os campos que o HTML
declara, e confere que a linha entrou em `novidade_lida` e que a faixa sumiu da
tela seguinte.
"""
import os
import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from psycopg_pool import ConnectionPool
from starlette.middleware.sessions import SessionMiddleware

from tests.test_cockpit import _BASE_SQL as _SQL
from web import painel_cockpit as pc

CONTA_NOME = "Prime Eventos"


@pytest.fixture()
def cliente(monkeypatch):
    dbname = "zaq_cockpit_faixa"
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1,
                           open=True, kwargs={"autocommit": True, "prepare_threshold": None})
    with admin.connection() as c:
        c.execute("select pg_terminate_backend(pid) from pg_stat_activity "
                  "where datname=%s and pid <> pg_backend_pid()", (dbname,))
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    pool = ConnectionPool(url, min_size=1, max_size=4, open=True,
                          kwargs={"prepare_threshold": None})
    with pool.connection() as c:
        c.execute(_SQL)
        from web.painel_servicos import _criar_orcamentos
        _criar_orcamentos(c)
        # as migrações DE VERDADE das novidades: é o `pra_quem` da 199 que decide
        # se o aviso é do vendedor, e reescrever isso à mão aqui seria testar a
        # minha cópia em vez do que roda em produção
        from tests.test_novidades import BASE as _MIG
        c.execute("alter table contas add column if not exists criado_em timestamptz "
                  "not null default now()")
        for m in ("174_novidades.sql", "199_novidades_pra_quem.sql"):
            c.execute((_MIG / m).read_text(encoding="utf-8"))
        # o interruptor da faixa (migração 266). Aplicado DE VERDADE: o default
        # `true` é o que faz a faixa continuar aparecendo pra quem não escolheu,
        # e escrever a coluna à mão aqui testaria a minha cópia, não a migração.
        from pathlib import Path as _P
        _raiz = _P(__file__).resolve().parents[1] / "db" / "migracoes"
        c.execute((_raiz / "266_avisos_na_fila.sql").read_text(encoding="utf-8"))
        c.commit()

    monkeypatch.setattr(pc, "get_pool", lambda: pool)
    monkeypatch.setattr(pc, "_selo", lambda conta_id: "")
    from finance import webpush
    monkeypatch.setattr(webpush, "chave_publica", lambda: None)

    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="teste-da-faixa")
    app.include_router(pc.router)

    from fastapi import Request

    @app.post("/_entrar")
    def _entrar(request: Request, conta_id: int, membro_id: int, papel: str = "vendedor"):
        request.session["conta_id"] = conta_id
        request.session["membro_id"] = membro_id
        request.session["papel"] = papel
        return {"ok": True}

    c = TestClient(app, follow_redirects=False)
    c.pool = pool
    yield c
    pool.close()


def _cena(pool, *, nicho="eventos", pra_quem="{vendedor}", publico="servico"):
    """Uma conta de serviço, um vendedor e um aviso NÃO LIDO mirado nele."""
    with pool.connection() as c:
        n = c.execute("insert into nichos (nome, slug, tipo) values "
                      "('Eventos',%s,'servico') returning id", (nicho,)).fetchone()[0]
        conta = c.execute("insert into contas (nome, nicho_id, criado_em) values "
                          "(%s,%s, now() - interval '90 days') returning id",
                          (CONTA_NOME, n)).fetchone()[0]
        vend = c.execute("insert into membros (conta_id, nome, email, papel, ativo) values "
                         "(%s,'Pedro','pedro@x.com','vendedor',true) returning id",
                         (conta,)).fetchone()[0]
        aviso = c.execute(
            """insert into novidades (chave, tipo, publico, pra_quem, titulo, resumo,
                                      link, corpo, publicado_em)
               values ('teste-faixa','novidade',%s,%s,
                       'A Fila abre na ordem da conversa','A lista mudou de ordem.',
                       '/cockpit','corpo', now() - interval '1 hour') returning id""",
            (publico, pra_quem)).fetchone()[0]
        c.commit()
    return conta, vend, aviso


def _entrar(c, conta, vend):
    r = c.post(f"/_entrar?conta_id={conta}&membro_id={vend}")
    assert r.status_code == 200


def _form_da_faixa(html: str):
    """O `<form>` do ✕ como o NAVEGADOR o lê: a ação e os campos que estão no HTML.

    Lido do HTML de propósito, e não escrito à mão no teste: escrever à mão faria o
    teste passar mesmo que a tela mandasse o formulário pra outro lugar — que é
    justamente o tipo de defeito que ele existe pra pegar."""
    bloco = re.search(r"<form[^>]*action='([^']*novidades[^']*lida)'[^>]*>(.*?)</form>",
                      html, re.S)
    if not bloco:
        return None, {}
    acao, corpo = bloco.group(1), bloco.group(2)
    campos = dict(re.findall(r"<input[^>]*name=(\w+)[^>]*value=(\w+)", corpo))
    return acao, campos


def test_o_x_da_faixa_marca_lida_e_a_faixa_some(cliente):
    conta, vend, aviso = _cena(cliente.pool)
    _entrar(cliente, conta, vend)

    r = cliente.get("/cockpit")
    assert r.status_code == 200
    html = r.text
    assert "A Fila abre na ordem da conversa" in html, "o aviso nem apareceu na faixa"

    acao, campos = _form_da_faixa(html)
    assert acao, "a faixa não trouxe o formulário do ✕"
    assert acao.endswith(f"/novidades/{aviso}/lida")

    # o POST vai pelo MESMO caminho do navegador: a ação e os campos que o HTML disse
    r2 = cliente.post(acao, data=campos)
    assert r2.status_code == 303, f"o ✕ não redirecionou: {r2.status_code}"

    with cliente.pool.connection() as c:
        marcou = c.execute("select count(*) from novidade_lida where novidade_id=%s "
                           "and conta_id=%s and coalesce(membro_id,0)=%s",
                           (aviso, conta, vend)).fetchone()[0]
    assert marcou == 1, "o ✕ não gravou a leitura — a faixa volta na próxima tela"

    # ...e a prova que o dono queria: a faixa SOME
    depois = cliente.get(r2.headers["location"]).text
    assert "A Fila abre na ordem da conversa" not in depois


def test_o_x_marca_UM_e_o_contador_mostra_a_fila(cliente):
    """O chamado de 16/09: "não consigo tirar esse aviso clicando no X".

    O botão funcionava — o dono tocou quatro vezes entre 13:39 e 13:41 e o banco
    registrou as quatro. O que ele não tinha como saber é que havia 26 avisos por
    ler: fechava um e o seguinte tomava o lugar, com o mesmo formato.

    Cheguei a fazer o ✕ dispensar TODOS de uma vez. O dono barrou, e estava certo:
    marcar 26 como lidos destrói informação, e não existe desmarcar. Então o ✕
    marca UM — e quem resolve o chamado é o CONTADOR, que mostra a fila sem apagar
    nada. Quem cala a fila é o interruptor da Empresa e o prazo."""
    conta, vend, novo = _cena(cliente.pool)
    with cliente.pool.connection() as c:
        velhos = [c.execute(
            """insert into novidades (chave, tipo, publico, pra_quem, titulo, resumo,
                                      link, corpo, publicado_em)
               values (%s,'novidade','todos','{vendedor}',%s,'r','/cockpit','c',
                       now() - make_interval(days => %s)) returning id""",
            (f"velho-{i}", f"Aviso velho {i}", i + 2)).fetchone()[0] for i in range(3)]
        c.commit()
    _entrar(cliente, conta, vend)

    html = cliente.get("/cockpit").text
    # a fila deixa de ser invisível: são 4 dentro do prazo, e a faixa diz isso
    assert "1 de 4" in html, "a faixa não conta quantos avisos estão na fila"
    acao, campos = _form_da_faixa(html)
    assert "faixa" not in campos, "o ✕ voltou a dispensar a fila — isso apaga leitura"

    r = cliente.post(acao, data=campos)
    assert r.status_code == 303
    with cliente.pool.connection() as c:
        lidas = {x[0] for x in c.execute(
            "select novidade_id from novidade_lida where conta_id=%s", (conta,)).fetchall()}
    assert lidas == {novo}, "o ✕ tem que marcar UM: os outros três continuam por ler"
    # ...e o próximo toma o lugar, agora DIZENDO que ainda são três
    depois = cliente.get(r.headers["location"]).text
    assert "1 de 3" in depois
    assert set(velhos) - lidas == set(velhos)


def test_aviso_velho_nao_interrompe_mais(cliente):
    """O prazo de 14 dias (`novidades.DIAS_NA_FAIXA`). Dos oito vendedores em
    produção, QUATRO estavam em 27 de 27 — nunca tocaram a faixa. Aviso que
    ninguém toca virou mobília, e mobília ensina a não olhar aquele pedaço da
    tela. O prazo faz a pilha se resolver sozinha, sem apagar nada: o velho
    continua não lido, só para de pular na frente."""
    conta, vend, _novo = _cena(cliente.pool)
    with cliente.pool.connection() as c:
        c.execute("delete from novidades")           # só o velho na cena
        velho = c.execute(
            """insert into novidades (chave, tipo, publico, pra_quem, titulo, resumo,
                                      link, corpo, publicado_em)
               values ('velho','novidade','todos','{vendedor}','Aviso de um mês',
                       'r','/cockpit','c', now() - interval '30 days') returning id""").fetchone()[0]
        c.commit()
    _entrar(cliente, conta, vend)
    html = cliente.get("/cockpit").text
    assert "class=faixa" not in html, "aviso de 30 dias não pode interromper"
    # mas ele NÃO foi apagado: continua não lido e continua sendo dele
    with cliente.pool.connection() as c:
        assert c.execute("select count(*) from novidade_lida").fetchone()[0] == 0
    assert cliente.post(f"/cockpit/novidades/{velho}/lida",
                        data={"volta": "perfil"}).status_code == 303


def test_o_dono_desliga_a_faixa_e_nada_e_apagado(cliente):
    """O parâmetro da tela Empresa (migração 266), que foi ideia do dono. Ele cala
    a INTERRUPÇÃO, não o aviso: nada vira lido, e a tela de Novidades continua
    com tudo."""
    from finance import novidades as nv
    conta, vend, novo = _cena(cliente.pool)
    _entrar(cliente, conta, vend)
    assert "class=faixa" in cliente.get("/cockpit").text

    nv.definir_faixa(cliente.pool, conta, False)
    assert "class=faixa" not in cliente.get("/cockpit").text
    with cliente.pool.connection() as c:
        assert c.execute("select count(*) from novidade_lida").fetchone()[0] == 0, (
            "desligar a faixa não pode marcar nada como lido")
    # e volta atrás: é reversível, que é o motivo de ele ser melhor que fechar tudo
    nv.definir_faixa(cliente.pool, conta, True)
    assert "class=faixa" in cliente.get("/cockpit").text


def test_sem_a_coluna_a_faixa_continua_aparecendo(cliente):
    """FALHA ABERTA, ao contrário da maioria dos portões desta base: um parâmetro
    que não pôde ser lido não pode CALAR um aviso. O pior caso é a faixa aparecer
    pra quem desligou; o contrário seria sumir pra quem contava com ela."""
    from finance import novidades as nv
    conta, vend, _ = _cena(cliente.pool)
    with cliente.pool.connection() as c:
        c.execute("alter table contas drop column if exists avisos_na_fila")
        c.commit()
    assert nv.faixa_ligada(cliente.pool, conta) is True
    _entrar(cliente, conta, vend)
    assert "class=faixa" in cliente.get("/cockpit").text


def test_o_entendi_do_aviso_marca_um_so(cliente):
    """O MESMO endereço serve o ✕ da faixa e o "Entendi" da tela do aviso. Os dois
    marcam UM — e este teste existe pra que ninguém volte a fazer um deles limpar
    a pilha calado, que é perder aviso que a pessoa ainda ia ler."""
    conta, vend, novo = _cena(cliente.pool)
    with cliente.pool.connection() as c:
        outro = c.execute(
            """insert into novidades (chave, tipo, publico, pra_quem, titulo, resumo,
                                      link, corpo, publicado_em)
               values ('outro','mudanca','todos','{vendedor}','Outro','r','/cockpit','c',
                       now() - interval '3 days') returning id""").fetchone()[0]
        c.commit()
    _entrar(cliente, conta, vend)
    r = cliente.post(f"/cockpit/novidades/{outro}/lida", data={"volta": "perfil"})
    assert r.status_code == 303
    with cliente.pool.connection() as c:
        lidas = {x[0] for x in c.execute(
            "select novidade_id from novidade_lida where conta_id=%s", (conta,)).fetchall()}
    assert lidas == {outro}, "o Entendi marcou mais do que o aviso que estava aberto"
    assert novo not in lidas


def test_o_x_de_um_aviso_que_nao_e_dele_nao_marca_nada(cliente):
    """O ✕ manda o id pela URL, e id é adivinhável. Marcar por id sem conferir que
    ESTE vendedor enxerga ESTE aviso deixaria qualquer um sujar a tabela — e a
    conferência é justamente o `if any(...)` que, se falhasse calado, também
    faria o ✕ legítimo não funcionar."""
    conta, vend, _ = _cena(cliente.pool)
    with cliente.pool.connection() as c:
        alheio = c.execute(
            """insert into novidades (chave, tipo, publico, pra_quem, titulo, resumo,
                                      link, corpo, publicado_em)
               values ('so-do-dono','novidade','servico','{dono}','Do dono','r',
                       '/painel','c', now() - interval '1 hour') returning id""").fetchone()[0]
        c.commit()
    _entrar(cliente, conta, vend)
    r = cliente.post(f"/cockpit/novidades/{alheio}/lida", data={"volta": "fila"})
    assert r.status_code == 303
    with cliente.pool.connection() as c:
        assert c.execute("select count(*) from novidade_lida where novidade_id=%s",
                         (alheio,)).fetchone()[0] == 0
