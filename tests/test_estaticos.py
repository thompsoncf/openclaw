"""CSS e JS servidos como arquivo, com endereço que muda quando o conteúdo muda.

POR QUE ISSO EXISTE. As telas do painel carregavam a folha e o script INTEIROS
dentro do HTML. Na Agenda eram 38 KB de CSS e 41 KB de JS indo junto em toda
navegação — trocar de mês, clicar num nome, voltar pro "Hoje" —, sem o navegador
ter como reaproveitar nada, porque vinham colados numa página que muda.

O acordo aqui é de duas pontas, e só vale se as duas valerem:

    1. o endereço carrega o RESUMO do conteúdo, então mudar o código muda o
       endereço e ninguém fica com versão velha;
    2. por isso — e só por isso — dá pra prometer um ano de cache `immutable`.

Prometer (2) sem garantir (1) seria servir código velho por um ano. Estes testes
prendem o par.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from web import estaticos as es


@pytest.fixture()
def cliente():
    app = FastAPI()
    app.include_router(es.router)
    return TestClient(app)


def test_o_endereco_carrega_o_resumo_do_conteudo(cliente):
    a = es.registrar("x.css", "body{color:red}")
    b = es.registrar("x.css", "body{color:blue}")
    assert a != b, "dois conteúdos diferentes não podem morar no mesmo endereço"
    assert cliente.get(a).text == "body{color:red}"
    assert cliente.get(b).text == "body{color:blue}"


def test_mesmo_conteudo_e_sempre_o_mesmo_endereco(cliente):
    """Importa porque o registro roda na importação do módulo: um reload em
    desenvolvimento não pode multiplicar arquivo na memória, e um deploy que não
    mexeu no CSS não pode invalidar o cache de todo mundo à toa."""
    assert es.registrar("y.js", "var a=1;") == es.registrar("y.js", "var a=1;")


def test_o_cache_de_um_ano_so_vale_com_o_resumo_no_nome(cliente):
    r = cliente.get(es.registrar("z.css", "b{}"))
    assert r.status_code == 200
    cc = r.headers["cache-control"]
    assert "immutable" in cc and "max-age=31536000" in cc


@pytest.mark.parametrize("nome,tipo", [("a.css", "text/css"),
                                       ("a.js", "application/javascript")])
def test_cada_tipo_sai_com_o_seu_content_type(cliente, nome, tipo):
    """Navegador não aplica folha servida como text/plain — a tela abriria sem
    estilo nenhum e ninguém saberia por quê."""
    r = cliente.get(es.registrar(nome, "/*x*/"))
    assert r.headers["content-type"].startswith(tipo)


def test_arquivo_desconhecido_nao_fica_guardado(cliente):
    """404 com cache seria o pior desfecho possível: o endereço some quando o
    conteúdo muda, e um 404 guardado deixaria a tela sem estilo até a pessoa
    limpar o navegador na mão."""
    r = cliente.get("/estatico/nao-existe-00000000.css")
    assert r.status_code == 404
    assert r.headers["cache-control"] == "no-store"


def test_tipo_nao_suportado_falha_na_importacao_e_nao_em_producao():
    """Registrar acontece na importação do módulo. Errar o tipo tem que estourar
    ali — em produção seria uma tela sem estilo, calada."""
    with pytest.raises(ValueError):
        es.registrar("planilha.xlsx", "nada")


# --------------------------------------------------- a Agenda, que é o caso real

def test_a_agenda_serve_css_e_js_por_arquivo_e_nao_por_dentro_da_pagina():
    import re
    from web import painel_agenda as pa
    assert re.match(r'<link rel="stylesheet" href="/estatico/agenda-[0-9a-f]{8}\.css">$',
                    pa._CSS), pa._CSS
    assert re.match(r'<script src="/estatico/agenda-[0-9a-f]{8}\.js" defer></script>$',
                    pa._JS_TAG), pa._JS_TAG


def test_so_os_DADOS_do_mes_continuam_dentro_da_pagina():
    """A linha que separa o que sai do que fica: tudo que tem {{ }} depende do
    carregamento e não pode virar arquivo cacheado. Se alguém escrever um {{ }}
    dentro do JS de novo, ele vai pro arquivo estático e sai literal no navegador
    — este teste é o aviso antes disso acontecer."""
    from web import painel_agenda as pa
    assert "{{" not in pa._JS_CRU and "{%" not in pa._JS_CRU
    assert "{{" not in pa._CSS_CRU and "{%" not in pa._CSS_CRU
    # e o preâmbulo que ficou inline é pequeno: é dado do mês, não código
    import re
    inline = re.findall(r"<script>(.*?)</script>", pa._AGENDA_TPL, re.S)
    assert inline and len(inline[0]) < 2000, f"o inline voltou a crescer: {len(inline[0])} bytes"
    for var in ("EVENTOS_DIA", "MES_ATUAL", "AGORA_ISO", "PRE_RESERVA_DIAS"):
        assert var in inline[0], f"{var} precisa vir do servidor, a cada carregamento"


def test_os_dados_vem_antes_do_codigo():
    """O código lê EVENTOS_DIA assim que carrega. Inverter a ordem daria
    ReferenceError e mataria o bloco inteiro — do jeito que já aconteceu nesta
    base por outro motivo (ver tests/test_painel_js_sintaxe.py)."""
    from web import painel_agenda as pa
    t = pa._AGENDA_TPL
    assert t.index("var EVENTOS_DIA") < t.index('<script src="/estatico/agenda-')
