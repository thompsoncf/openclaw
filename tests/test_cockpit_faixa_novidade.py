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
        # a tabela do ✕ semanal (272). Aplicada de verdade pelo mesmo motivo: é ela
        # que guarda "dispensei esta semana" SEM marcar nada como lido.
        c.execute((_raiz / "272_novidade_semana_vista.sql").read_text(encoding="utf-8"))
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
    bloco = re.search(r"<form[^>]*action='([^']*novidades[^']*)'[^>]*>(.*?)</form>",
                      html, re.S)
    if not bloco:
        return None, {}
    acao, corpo = bloco.group(1), bloco.group(2)
    campos = dict(re.findall(r"<input[^>]*name=(\w+)[^>]*value=(\w+)", corpo))
    return acao, campos


def _link_da_faixa(html: str) -> str:
    """Pra onde o "Ver →" leva."""
    m = re.search(r"<div class=faixa><a href='([^']+)'", html)
    return m.group(1) if m else ""


def test_um_aviso_sozinho_aparece_pelo_TITULO_e_nao_como_contagem(cliente):
    """Agrupar por semana serve pra domar MUITOS avisos, não pra esconder um.

    Com um só, "1 novidade esta semana" seria pior que o título: obrigaria um toque
    a mais pra descobrir o que a pessoa já podia ler dali."""
    conta, vend, aviso = _cena(cliente.pool)
    _entrar(cliente, conta, vend)
    html = cliente.get("/cockpit").text
    assert "A Fila abre na ordem da conversa" in html
    assert "1 novidade" not in html
    assert _link_da_faixa(html).endswith(f"/novidades/{aviso}")


def test_o_x_da_faixa_dispensa_a_SEMANA_sem_marcar_nada_como_lido(cliente):
    """A regra que o dono deu em 16/09 continua de pé, agora que a faixa é semanal.

    Ele barrou o ✕ que dispensava a fila inteira porque marcar como lido o que
    ninguém leu DESTRÓI informação — "não lido" é o que a bolinha conta, e não
    existe desmarcar. Com a semana o risco seria maior: um toque apagaria cinco.

    Então o ✕ grava só "não me interrompa mais por esta semana" (migração 272), e
    `novidade_lida` continua vazia."""
    conta, vend, aviso = _cena(cliente.pool)
    _entrar(cliente, conta, vend)

    acao, campos = _form_da_faixa(cliente.get("/cockpit").text)
    assert acao and acao.endswith("/vista"), "o ✕ não aponta mais pra semana"

    r = cliente.post(acao, data=campos)
    assert r.status_code == 303

    with cliente.pool.connection() as c:
        lidas = c.execute("select count(*) from novidade_lida").fetchone()[0]
        semanas = c.execute("select count(*) from novidade_semana_vista "
                            " where conta_id=%s and membro_id=%s",
                            (conta, vend)).fetchone()[0]
    assert lidas == 0, "o ✕ marcou como lido o que ninguém leu"
    assert semanas == 1, "o ✕ não gravou a semana dispensada — a faixa volta amanhã"

    # ...e a faixa some da tela seguinte
    assert "A Fila abre na ordem da conversa" not in cliente.get(r.headers["location"]).text


def test_varios_avisos_da_mesma_semana_viram_UMA_interrupcao(cliente):
    """O achado de 17/09/2026: 30 avisos em 7 dias, o dono com 47 por ler e ZERO
    lidos, a vendedora com mais leads com 28 por ler e zero. A faixa dizia "1 de
    42" — ninguém toca 42 vezes.

    Era a regra 5 do CLAUDE.md funcionando bem demais: todo PR escreve o aviso
    dele, e cinco entregas por dia viram cinco interrupções por dia. A escolha do
    dono foi "um resumo por semana"."""
    conta, vend, novo = _cena(cliente.pool)
    with cliente.pool.connection() as c:
        for i in range(4):                      # mesma semana do `_cena`
            c.execute(
                """insert into novidades (chave, tipo, publico, pra_quem, titulo, resumo,
                                          link, corpo, publicado_em)
                   values (%s,'novidade','todos','{vendedor}',%s,'r','/cockpit','c',
                           now() - make_interval(hours => %s))""",
                (f"mesma-semana-{i}", f"Aviso {i}", i + 2))
        c.commit()
    _entrar(cliente, conta, vend)

    html = cliente.get("/cockpit").text
    assert "5 novidades" in html, "a faixa não agrupou a semana"
    assert "esta semana" in html
    assert "A Fila abre na ordem da conversa" not in html, (
        "com cinco na semana, a faixa não é pra mostrar o título de um deles")
    # o "Ver" leva pra lista da semana, não pra um aviso
    assert "/novidades/semana/" in _link_da_faixa(html)


def test_o_contador_conta_SEMANAS_e_o_x_dispensa_so_uma(cliente):
    """"1 de 42" era a conta que ninguém tocou. Agora o contador é de semanas, que
    é o número de vezes que a pessoa ainda vai ser interrompida."""
    conta, vend, _ = _cena(cliente.pool)
    with cliente.pool.connection() as c:
        c.execute(
            """insert into novidades (chave, tipo, publico, pra_quem, titulo, resumo,
                                      link, corpo, publicado_em)
               values ('da-semana-passada','novidade','todos','{vendedor}',
                       'Aviso da semana passada','r','/cockpit','c',
                       now() - interval '8 days')""")
        c.commit()
    _entrar(cliente, conta, vend)

    html = cliente.get("/cockpit").text
    assert "1 de 2" in html, "o contador não está contando semanas"

    acao, campos = _form_da_faixa(html)
    r = cliente.post(acao, data=campos)
    depois = cliente.get(r.headers["location"]).text
    # a semana passada toma o lugar, e agora é a última — sem contador
    assert "Aviso da semana passada" in depois
    assert "1 de 2" not in depois
    with cliente.pool.connection() as c:
        assert c.execute("select count(*) from novidade_lida").fetchone()[0] == 0


def test_a_tela_da_semana_lista_os_avisos_e_nao_marca_nenhum(cliente):
    """Ver a lista de títulos não é ter lido as cinco coisas. Marcar ali encheria a
    base de leituras que não aconteceram — e o que a bolinha conta é isso."""
    conta, vend, aviso = _cena(cliente.pool)
    with cliente.pool.connection() as c:
        c.execute(
            """insert into novidades (chave, tipo, publico, pra_quem, titulo, resumo,
                                      link, corpo, publicado_em)
               values ('outro-da-semana','novidade','todos','{vendedor}',
                       'O outro da semana','resumo do outro','/cockpit','c',
                       now() - interval '2 hours')""")
        c.commit()
    _entrar(cliente, conta, vend)

    semana = _link_da_faixa(cliente.get("/cockpit").text)
    assert "/novidades/semana/" in semana
    tela = cliente.get(semana).text
    assert "A Fila abre na ordem da conversa" in tela and "O outro da semana" in tela
    assert "2 por ler" in tela
    with cliente.pool.connection() as c:
        assert c.execute("select count(*) from novidade_lida").fetchone()[0] == 0, (
            "abrir a lista da semana marcou leitura que não aconteceu")
    # e daqui se chega no aviso inteiro, que é quem marca
    assert f"{semana.split('/novidades')[0]}/novidades/{aviso}" in tela


def test_semana_inexistente_nao_derruba_a_tela(cliente):
    conta, vend, _ = _cena(cliente.pool)
    _entrar(cliente, conta, vend)
    r = cliente.get("/cockpit/novidades/semana/2019-W01")
    assert r.status_code == 303 and r.headers["location"].endswith("/perfil")
