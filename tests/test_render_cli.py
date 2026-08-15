"""Inventario de projetos do render_cli (paginacao + arvore Projeto/Ambiente/Servico).

Sem banco e sem rede: a API do Render e' simulada trocando o `_req`. O que
esta' sob teste e' a NOSSA logica de paginar e montar a arvore, nao a deles.

O que estes testes travam:

1. PAGINACAO. Toda lista do Render vem em envelopes com `cursor`, e a proxima
   pagina comeca no cursor do ULTIMO item. Errar isso ou entra em loop infinito
   ou perde silenciosamente tudo depois do centesimo item — e o segundo modo de
   falhar e' pior, porque parece que funcionou.

2. SERVICO SOLTO. No Render da' pra ter servico fora de projeto (os `-bcu3` de
   voces sao assim, criados na mao). Um "inventario completo" que so' andasse
   pela arvore mentiria por omissao justamente nos servicos que importam.
"""
import pytest

from scripts import render_cli as cli


class _APIFalsa:
    """Responde no formato do Render: [{"<chave>": {...}, "cursor": "..."}]."""

    def __init__(self, rotas: dict):
        self.rotas = rotas
        self.chamadas = []

    def __call__(self, _s, _metodo, caminho, params=None):
        params = params or {}
        self.chamadas.append((caminho, dict(params)))
        itens = self.rotas.get(caminho)
        itens = itens(params) if callable(itens) else (itens or [])
        # aplica o cursor como o Render aplica: retoma DEPOIS do item apontado
        cursor = params.get("cursor")
        if cursor:
            ids = [i.get("cursor") for i in itens]
            itens = itens[ids.index(cursor) + 1:] if cursor in ids else []
        limite = int(params.get("limit", 100))

        class _R:
            def json(self_):
                return itens[:limite]
        return _R()


def _env(chave, obj, cursor):
    return {chave: obj, "cursor": cursor}


@pytest.fixture()
def api(monkeypatch):
    def instalar(rotas):
        falsa = _APIFalsa(rotas)
        monkeypatch.setattr(cli, "_req", falsa)
        return falsa
    return instalar


# ------------------------------------------------------------------ paginacao

def test_listar_junta_todas_as_paginas(api):
    """250 itens com limite 100 = 3 paginas. Nenhum pode se perder."""
    todos = [_env("service", {"id": f"srv-{i}", "name": f"s{i}"}, f"c{i}")
             for i in range(250)]
    falsa = api({"/services": todos})

    achados = cli._listar(None, "/services", "service")

    assert len(achados) == 250
    assert achados[0]["id"] == "srv-0"
    assert achados[-1]["id"] == "srv-249"
    # 3 paginas: a terceira volta incompleta (50) e encerra o laco
    assert len(falsa.chamadas) == 3
    assert falsa.chamadas[1][1]["cursor"] == "c99"


def test_listar_para_sem_cursor_em_vez_de_repetir(api):
    """Pagina cheia mas sem cursor: tem que parar, nao repetir pra sempre."""
    cheia = [_env("service", {"id": f"srv-{i}"}, None) for i in range(100)]
    falsa = api({"/services": cheia})

    assert len(cli._listar(None, "/services", "service")) == 100
    assert len(falsa.chamadas) == 1


def test_listar_desembrulha_e_tolera_objeto_cru(api):
    """Se o envelope mudar de nome, ainda devolve algo em vez de dicts vazios."""
    api({"/x": [{"id": "cru-1"}]})
    assert cli._listar(None, "/x", "coisa") == [{"id": "cru-1"}]


def test_listar_lista_vazia(api):
    api({"/services": []})
    assert cli._listar(None, "/services", "service") == []


# --------------------------------------------------------------------- arvore

@pytest.fixture()
def conta(api):
    """Uma conta com 1 projeto, 2 ambientes e um servico solto fora deles."""
    return api({
        "/projects": [_env("project", {"id": "prj-1", "name": "Zaq"}, "c1")],
        "/environments": lambda p: (
            [_env("environment", {"id": "evm-prod", "name": "producao"}, "c1"),
             _env("environment", {"id": "evm-dev", "name": "dev"}, "c2")]
            if p.get("projectId") == "prj-1" else []),
        "/services": lambda p: {
            "evm-prod": [_env("service", {"id": "srv-web", "type": "web_service",
                                          "name": "openclaw-web"}, "c1")],
            "evm-dev": [_env("service", {"id": "srv-dev", "type": "web_service",
                                         "name": "openclaw-dev"}, "c1")],
        }.get(p.get("environmentId"), [
            # sem filtro = a conta inteira, incluindo o servico solto
            _env("service", {"id": "srv-web", "type": "web_service",
                             "name": "openclaw-web"}, "c1"),
            _env("service", {"id": "srv-dev", "type": "web_service",
                             "name": "openclaw-dev"}, "c2"),
            _env("service", {"id": "srv-cron", "type": "cron_job",
                             "name": "openclaw-monitor-saldos-bcu3"}, "c3"),
        ]),
    })


def test_arvore_liga_servico_ao_projeto_pelo_ambiente(conta):
    arvore, dono = cli._arvore_projetos(None)

    assert len(arvore) == 1
    proj, ramos = arvore[0]
    assert proj["name"] == "Zaq"
    assert [amb["name"] for amb, _ in ramos] == ["producao", "dev"]
    assert dono["srv-web"] == "Zaq / producao"
    assert dono["srv-dev"] == "Zaq / dev"
    # o solto nao pertence a ambiente nenhum
    assert "srv-cron" not in dono


def test_projetos_mostra_o_servico_solto(conta, capsys):
    """O -bcu3 criado na mao nao pode sumir do inventario."""
    cli.cmd_projetos(None, None)
    saida = capsys.readouterr().out

    assert "Zaq" in saida and "producao" in saida
    assert "(sem projeto)" in saida
    assert "openclaw-monitor-saldos-bcu3" in saida


def test_services_sem_flag_nao_chama_projetos(conta):
    """O caminho padrao continua sendo 1 request — a arvore custa varias."""
    cli.cmd_services(None, type("A", (), {"projetos": False})())
    assert [c[0] for c in conta.chamadas] == ["/services"]


def test_services_com_flag_anota_projeto(conta, capsys):
    cli.cmd_services(None, type("A", (), {"projetos": True})())
    saida = capsys.readouterr().out

    assert "Zaq / producao" in saida
    assert "(sem projeto)" in saida          # o cron solto fica marcado
    assert "/projects" in [c[0] for c in conta.chamadas]


def test_conta_vazia_avisa_em_vez_de_imprimir_nada(api, capsys):
    api({"/projects": [], "/services": []})
    cli.cmd_projetos(None, None)
    assert "Nenhum projeto e nenhum servico" in capsys.readouterr().out
