"""A linha do orçamento de evento que o catálogo não explica mais.

POR QUE ESTE TESTE EXISTE. Em 18/09/2026, lendo a aba de Serviços da Prime
(conta 34), o orçamento nº 22 — Renata Tatiana, R$ 9.650, já ENVIADA — tinha
OITO itens gravados em `itens` e `modulos` NULO.

A folha que o cliente recebe lê `itens` e estava certa. O editor não: ele
montava as linhas cruzando `modulos` com o catálogo ATIVO, então abria vazio. E
`coletarBody` monta `itens` a partir do que está NA TELA — um "Salvar no funil"
em cima daquela tela vazia gravaria `itens: []` e zeraria a folha que o cliente
tem na mão. Regra 0 do CLAUDE.md: informação do cliente não se perde.

O mesmo caminho vale pro serviço EXCLUÍDO do catálogo depois (a exclusão é soft,
mas `servicos_catalogo.listar` só devolve `ativo`), e pro slug que o servidor
filtra em `salvar` (`modulos = [i for i in dados.modulos if i in validos]`).

A regra que conserta isso é `window.ZAQ_PAREAR`, em `web/painel_servicos.py`:
dado `modulos`, `itens` e o catálogo, ela diz de onde sai cada linha. É pura e
mora fora do IIFE da tela justamente pra poder ser exercitada aqui — o CI desta
base só roda pytest, então o pytest é que chama o node.
"""
from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from web.painel_servicos import _JS_PAREAR_CRU

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node não está disponível")


def parear(mods, itens, catalogo):
    """Roda `window.ZAQ_PAREAR` no node e devolve o resultado como lista de dicts."""
    script = (
        "var window = {};\n"
        + _JS_PAREAR_CRU
        + "\nvar a = JSON.parse(process.argv[1]);\n"
        "process.stdout.write(JSON.stringify("
        "window.ZAQ_PAREAR(a.mods, a.itens, a.catalogo)));\n"
    )
    r = subprocess.run(
        [NODE, "-e", script, json.dumps({"mods": mods, "itens": itens,
                                         "catalogo": catalogo})],
        capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


CAT = [{"slug": "espaco", "nome": "LOCAÇÃO DO ESPAÇO"},
       {"slug": "leds", "nome": "LOCAÇÃO LEDS"}]


def test_caminho_normal_pareia_por_indice():
    """Com `modulos` e `itens` do mesmo tamanho, o índice casa e nada é órfão."""
    r = parear(["espaco", "leds"],
               [{"nome": "LOCAÇÃO DO ESPAÇO"}, {"nome": "LOCAÇÃO LEDS"}], CAT)
    assert [p["slug"] for p in r] == ["espaco", "leds"]
    assert [p["orfao"] for p in r] == [False, False]


def test_orcamento_22_sem_modulos_nao_perde_nenhum_item():
    """O caso exato que motivou o conserto: `modulos` nulo e oito itens.

    Antes, a tela abria com ZERO linhas. Agora cada item vira uma linha — as que
    o catálogo reconhece pelo nome com o slug de verdade, as outras como órfãs,
    mas TODAS presentes. É a contagem que importa: é ela que `coletarBody` grava
    de volta em `itens`.
    """
    itens = [{"nome": n} for n in [
        "LOCAÇÃO ESPAÇO - PACOTE PRIME - SEXTA A DOMINGO - 2027",
        "LOCAÇÃO SUÍTE", "LOCAÇÃO UTENSÍLIOS/LOUÇAS", "LOCAÇÃO LEDS",
        "LOCAÇÃO CLIMATIZADORES", "MONITOR ÁREA KIDS", "LOCAÇÃO COZINHA",
        "LOCAÇÃO FREEZER"]]
    r = parear(None, itens, CAT)
    assert len(r) == 8
    # o único que o catálogo conhece volta com o slug de verdade
    assert [p for p in r if p["slug"] == "leds"][0]["orfao"] is False
    # os outros sete continuam na tela, como órfãos, com o item salvo junto
    orfaos = [p for p in r if p["orfao"]]
    assert len(orfaos) == 7
    assert all(p["item"] is not None for p in orfaos)
    assert all(p["slug"].startswith("orfao:") for p in orfaos)


def test_servico_excluido_do_catalogo_vira_orfao_e_fica():
    """Serviço apagado do catálogo depois de a proposta existir.

    O slug ainda está em `modulos`, mas o catálogo ativo não tem mais. A linha
    não pode sumir: ela é o que o cliente contratou.
    """
    r = parear(["espaco", "apagado"],
               [{"nome": "LOCAÇÃO DO ESPAÇO"}, {"nome": "SERVIÇO DE DJ"}], CAT)
    assert len(r) == 2
    assert r[1]["slug"] == "apagado"          # o slug REAL é preservado
    assert r[1]["orfao"] is True
    assert r[1]["item"]["nome"] == "SERVIÇO DE DJ"


def test_tamanhos_diferentes_nao_pareiam_por_indice():
    """`modulos` menor que `itens` (o servidor filtrou um slug): nome, não índice.

    Parear por índice aqui daria o slug do espaço para a linha dos leds — o
    preço de um item viraria o do outro na próxima abertura.
    """
    r = parear(["leds"],
               [{"nome": "SERVIÇO SUMIDO"}, {"nome": "LOCAÇÃO LEDS"}], CAT)
    assert r[0]["orfao"] is True and r[0]["slug"].startswith("orfao:")
    assert r[1]["slug"] == "leds" and r[1]["orfao"] is False


def test_nome_repetido_no_catalogo_nao_chuta_slug():
    """Dois serviços com o mesmo nome não dizem qual é qual.

    Casar pelo primeiro colocaria o slug do errado em `modulos`, e o item
    voltaria com o preço do outro. Vira órfão: a linha aparece com o valor
    GRAVADO, que é o que o cliente aprovou.
    """
    cat = [{"slug": "a", "nome": "BUFFET"}, {"slug": "b", "nome": "BUFFET"}]
    r = parear(None, [{"nome": "BUFFET"}], cat)
    assert r[0]["orfao"] is True
    assert r[0]["slug"].startswith("orfao:")


def test_proposta_antiga_sem_itens_continua_como_era():
    """Antes da coluna `itens`, o slug era tudo que existia. Nada muda pra ela."""
    r = parear(["espaco", "sumiu"], [], CAT)
    assert [p["slug"] for p in r] == ["espaco", "sumiu"]
    assert [p["orfao"] for p in r] == [False, True]
    assert all(p["item"] is None for p in r)


def test_id_sintetico_nunca_vai_pro_banco():
    """O id inventado aqui é de tela. `coletarBody` filtra tudo que começa com
    `orfao:` antes de montar `modulos` — este teste fixa o prefixo de que aquele
    filtro depende."""
    r = parear(None, [{"nome": "QUALQUER COISA"}], CAT)
    assert r[0]["slug"].startswith("orfao:")
