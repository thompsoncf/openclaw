"""O bloco "Visão do negócio" mora numa tela só: o /painel.

Ele já esteve incluído TAMBÉM no topo do /painel/empresa, e o resultado era a mesma
tela — mesmos KPIs, mesmo funil, mesmo fluxo, mesmo mês — aparecendo duas vezes no app.
Quem abria a aba Empresa via o dashboard do painel de novo, sem nada de novo.

Duas travas, e a segunda é a que importa a longo prazo:

1. o include não voltar pro `_EMPRESA` (e continuar existindo no `_PAINEL`);
2. a rota `painel_empresa` não voltar a chamar `_painel_dashboard`. Fora do bloco, a
   Empresa só usa o `dre` — o resto do dashboard (resumo de títulos, fluxo projetado de
   4 semanas, MRR e a query do funil de orçamentos) era trabalho jogado fora a cada
   abertura da aba. Sem esta segunda trava, alguém devolve a chamada "pra reaproveitar o
   dre" e o custo volta sozinho, sem o include denunciar nada na tela.

Textual de propósito: renderizar /painel/empresa de verdade pediria sessão PJ logada com
cadastro completo, e o que precisa ser travado aqui é o que está escrito no fonte.
"""
import inspect
import re

from web import portal

_INCLUDE = '{% include "dash_bloco" %}'
# O que se procura é o INCLUDE, não a palavra: tanto o template quanto a rota trazem
# comentários que explicam por que o bloco não está mais ali, e citar o nome no comentário
# não pode reprovar o teste.
_INCLUI_DASH = re.compile(r'\{%-?\s*include\s+["\']dash_bloco["\']')


def test_o_painel_continua_com_o_bloco():
    """A trava só vale se o bloco ainda existir em ALGUM lugar — senão ela passaria
    verde depois de alguém apagar o dashboard inteiro."""
    assert _INCLUI_DASH.search(portal._PAINEL), _INCLUDE + " sumiu do /painel"
    assert "dash_bloco" in portal._env.list_templates()


def test_a_empresa_nao_inclui_o_bloco():
    assert not _INCLUI_DASH.search(portal._EMPRESA)


def test_a_empresa_nao_monta_o_dashboard_inteiro():
    fonte = inspect.getsource(portal.painel_empresa)
    assert "_painel_dashboard(" not in fonte, (
        "a rota da Empresa voltou a montar o dashboard do /painel; ela só precisa do "
        "dre (emp.dre_mes)")
    assert "dre_mes" in fonte, "o card 'DRE do mês' da Empresa depende disto"


def test_a_empresa_nao_passa_mais_dash_res_fluxo():
    """Contexto que ninguém consome vira pegadinha pro próximo: alguém escreve
    `{{ res.a_receber_centavos }}` no template e funciona, e a duplicata volta pela
    porta dos fundos."""
    fonte = inspect.getsource(portal.painel_empresa)
    for morto in ("dash=dash", "res=res", "fluxo=fluxo"):
        assert morto not in fonte, f"{morto} não tem consumidor no template da Empresa"
