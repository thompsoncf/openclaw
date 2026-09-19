"""Os três ajustes de layout do funil de Serviços, pedidos em 19/09/2026.

O dono abriu a aba redesenhada (#738) e apontou três coisas, olhando a Prime:

1. A tela trava em 1120px enquanto o Raio-X preenche a área de conteúdo.
2. A data da festa aparece DUAS VEZES na mesma linha, a meio centímetro de
   distância: no bloco "04 SET 27" e no subtítulo "Casamento · 04/09/2027 · 150
   convidados".
3. A data de criação estava enterrada entre o nº e o vendedor
   ("nº 27 · gerada 16/09/2026 · vendido por THIAGO PINHEIRO") — o dado certo no
   lugar errado.

Os três valem SÓ no nicho de eventos: a tela da conta recorrente não foi medida
nesta mudança (seção 6 do CLAUDE.md). Metade deste arquivo é sobre isso.
"""
from __future__ import annotations

import pytest

from finance import icones_servico as ics
from finance import vendas as v
from web import painel_servicos as ps     # noqa: F401 — registra "servicos" no loader
from web.portal import _env

_EVENTO = {"tipo": "Casamento", "data": "2027-09-04", "convidados": 150}


def _render(**over):
    ctx = dict(empresa_nome="MANOEL SOARES", tem_pj=True, vende_servico=True,
               servico_avulso=True, pode_contrato=True, ve_todos=True,
               tipo_padrao="pf", tipos_evento=["Casamento"],
               tipos_contrato=["Locação de espaço"], local_padrao="RUA X, 1",
               icones_paleta=ics.paleta())
    ctx.update(over)
    return _env.get_template("servicos").render(**ctx)


# ------------------------------------------------------- 1. a largura da tela

def test_o_evento_nao_tem_mais_trava_de_largura():
    """Como o `.rx` do Raio-X, que não tem `max-width` nenhum."""
    assert ".sv-wrap.evento{max-width:none}" in ps._CSS_CRU


def test_o_recorrente_continua_com_a_largura_de_sempre():
    """A ZAQ não foi medida nesta mudança — a trava base fica onde estava."""
    assert "max-width:960px" in ps._CSS_CRU


# --------------------------------------------- 2. a data que aparecia duas vezes

def test_no_funil_de_evento_o_subtitulo_nao_repete_a_data():
    """O bloco da esquerda já diz 04 SET 27; o subtítulo dizia 04/09/2027."""
    r = v.titulo_do_funil(cadastro="RAYANE FELIX ABREU", modo="evento",
                          evento=_EVENTO, numero=27, com_data=False)
    assert r["sub"] == "Casamento · 150 convidados"
    assert "04/09/2027" not in r["sub"]


def test_quem_nao_mostra_a_data_em_outro_lugar_continua_recebendo_ela():
    """`com_data` nasce ligado: nenhum chamador existente muda de comportamento
    por causa deste parâmetro."""
    r = v.titulo_do_funil(cadastro="RAYANE FELIX ABREU", modo="evento",
                          evento=_EVENTO, numero=27)
    assert r["sub"] == "Casamento · 04/09/2027 · 150 convidados"


def test_sem_data_o_subtitulo_nao_fica_com_separador_solto():
    """Tirar a data não pode deixar " · · " nem ponto no começo."""
    r = v.titulo_do_funil(cadastro="Fulana", modo="evento",
                          evento={"tipo": "Casamento"}, com_data=False)
    assert r["sub"] == "Casamento"
    r2 = v.titulo_do_funil(cadastro="Fulana", modo="evento",
                           evento={"data": "2027-09-04"}, com_data=False)
    assert r2["sub"] == ""


def test_o_com_data_nao_mexe_no_recorrente():
    """No recorrente o subtítulo é o contato, e nunca teve data pra tirar."""
    for flag in (True, False):
        r = v.titulo_do_funil(empresa="Aladdin Consultoria", cliente="Thompson",
                              modo="recorrente", com_data=flag)
        assert r["sub"] == "Thompson"


def test_a_tela_pede_o_subtitulo_sem_data_quando_desenha_o_bloco():
    """O elo entre as duas pontas: é `data_linha` (o bloco) que decide.

    Sem esta linha a função continuaria devolvendo a data e a repetição voltava
    calada — não quebra nada, só volta a ficar feio.
    """
    import inspect
    fonte = inspect.getsource(ps.painel_servicos_lista)
    assert 'com_data=not it["data_linha"]' in fonte


def test_o_bloco_de_data_e_montado_ANTES_do_titulo():
    """A ordem das duas atribuições, porque uma lê a outra.

    Na primeira versão o título vinha primeiro e consultava `it["data_linha"]`
    antes de a chave existir: `KeyError`, e `/painel/servicos/lista` respondia
    500 — o funil inteiro em branco. Escapou porque o teste acima lê o CÓDIGO e
    o código estava escrito certo; errada estava a ordem.
    (Quem pega isso de verdade é a rota, em
    `test_painel_servicos_sinal.py::test_a_linha_nao_repete_a_data_da_festa_no_subtitulo`.
    Este aqui aponta o dedo pro lugar quando quebra.)
    """
    import inspect
    fonte = inspect.getsource(ps.painel_servicos_lista)
    assert fonte.index('it["data_linha"] = vendas.data_da_linha') < \
        fonte.index('com_data=not it["data_linha"]')


# ------------------------------------------- 3. a data de criação em coluna

def test_a_data_de_criacao_tem_coluna_propria_no_evento():
    js = ps._JS_CRU
    assert "oc-criada" in js
    assert "Criada em" in js


def test_a_coluna_mostra_a_data_completa_e_nao_o_tempo_decorrido():
    """Pedido do dono, em 19/09: "eu preciso saber o dia que foi criado, é muito
    mais relevante". `it.data` vem do servidor já como %d/%m/%Y — com ano, que
    no funil de eventos convive com 2026, 2027 e 2028 ao mesmo tempo."""
    js = ps._JS_CRU
    assert "'<div class=\"rot\">Criada em</div><div class=\"dt\">'+esc(it.data)+'</div>'" in js
    # e nada de "há N dias" na linha do funil
    assert "há 3 dias" not in js
    assert "_ha(" not in js


def test_a_data_de_criacao_sai_do_meio_da_frase_so_no_evento():
    """No recorrente ela continua em `sub1`, onde sempre esteve."""
    assert "((!SERVICO_AVULSO && it.data)?('gerada '+esc(it.data)):'')" in ps._JS_CRU


def test_a_coluna_tem_estilo_e_nao_nasce_invisivel():
    css = ps._CSS_CRU
    assert ".oc-criada{" in css
    assert ".oc-criada .dt{" in css and ".oc-criada .rot{" in css
    # no celular ela entra no fluxo em vez de ficar pendurada no canto — e ANTES
    # da barra de ações, pra que o verde continue sendo o fim da linha
    assert ".oc-criada{text-align:left" in css
    assert "order:1" in css and ".oc-hist .oc-acoes{order:2}" in css


def test_o_servidor_manda_a_data_formatada_com_ano():
    """A coluna só desenha o que o servidor mandar. `%d/%m/%Y` é o formato, e o
    ano faz parte dele — um `%d/%m` aqui deixaria a coluna ambígua."""
    import inspect
    fonte = inspect.getsource(ps.painel_servicos_lista)
    assert 'r[7].strftime("%d/%m/%Y")' in fonte


# --------------------------------------------------- e nada vaza pro recorrente

@pytest.mark.parametrize("marca", ["oc-criada"])
def test_a_coluna_nao_aparece_na_marcacao_do_recorrente(marca):
    """Ela é criada pelo JS só sob `SERVICO_AVULSO`; o HTML não a traz pra
    nenhum dos dois, e é o teste acima que garante o gate."""
    assert marca not in _render(servico_avulso=False, pode_contrato=False)
