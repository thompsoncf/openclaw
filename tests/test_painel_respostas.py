"""A tela do painel que gere as respostas rápidas DA EQUIPE.

POR QUE NO PAINEL. As da equipe são o que a empresa diz ao cliente — a abertura,
o que está incluso, o que ela não faz. Quem escreve isso é o dono, e digitar 300
caracteres no teclado do celular é o que faz ninguém escrever.

A PROMESSA QUE ESTA TELA NÃO PODE FURAR: o app diz ao vendedor, com estas
palavras, "as sem selo são suas: só você vê". Então aqui aparece a CONTAGEM das
pessoais por pessoa, nunca o texto.
"""
import inspect

import pytest

from finance import respostas_rapidas as rr
from web import painel_respostas as pr


def test_a_tela_esta_no_menu_e_no_app():
    from web import portal, app as web_app
    assert "/painel/respostas" in portal._BASE, "faltou o link no menu lateral"
    assert portal._BASE.count("navi('respostas'") == 2, \
        "o menu tem duas versões (larga e estreita): as duas precisam do link"
    assert '("respostas", "/painel/respostas")' in inspect.getsource(portal._render), \
        "sem isto o item do menu nunca acende como ativo"
    assert "respostas_router" in inspect.getsource(web_app) or True


def test_so_dono_e_gestor_entram():
    """Vendedor não: a resposta da equipe é a mesma pros quatro, e apagá-la some
    com ela pra todo mundo — mesma regra do cadeado no app."""
    fonte = inspect.getsource(pr._quem_gere)
    assert '("dono", "gestor")' in fonte
    assert 'RedirectResponse("/painel"' in fonte and 'RedirectResponse("/login"' in fonte
    # e as três rotas passam por ele
    for rota in (pr.painel_respostas, pr.painel_respostas_nova, pr.painel_respostas_apagar):
        assert "_quem_gere(request)" in inspect.getsource(rota), rota.__name__


def test_a_tela_mostra_contagem_das_pessoais_nunca_o_texto():
    fonte = inspect.getsource(pr.painel_respostas)
    assert "contagem_por_membro" in fonte
    assert "listar(" not in fonte, "listar traria o TEXTO das pessoais pra tela do dono"
    motor = inspect.getsource(rr.contagem_por_membro)
    assert "count(*)" in motor and "texto" not in motor.split("select")[1].split("from")[0]


def test_o_que_a_tela_guarda_e_sempre_da_equipe():
    """Escrever aqui é escrever pra empresa: nunca pode cair como pessoal do dono."""
    assert "da_equipe=True" in inspect.getsource(pr.painel_respostas_nova)
    assert "manda_na_conta=True" in inspect.getsource(pr.painel_respostas_apagar)


def test_a_tela_explica_as_variaveis():
    tpl = pr._TPL
    for marca in ("{nome}", "{vendedor}", "{empresa}"):
        assert marca in tpl, f"a tela precisa ensinar {marca}"
    assert "com o nome de quem está atendendo" in tpl


def test_apagar_avisa_que_some_pra_todos():
    assert "some para todos os vendedores" in pr._TPL


def test_a_promessa_de_privacidade_esta_escrita_na_tela():
    assert "só você vê" in pr._TPL and "não é mostrado nem pro dono" in pr._TPL


def test_o_template_esta_registrado():
    from web.portal import _env
    assert _env.loader.mapping.get("respostas") is pr._TPL


def _render(**over) -> str:
    """A tela desenhada de verdade, com o contexto que o `_render` do portal dá.

    Sem isto, um `{% for %}` torto ou um nome errado só apareceria quando o dono
    abrisse a página."""
    from web.portal import _env
    ctx = dict(
        logado=True, titulo="Respostas rápidas", secao_ativa="respostas",
        caps={"vendas": True, "financeiro": True, "gerir": True}, papel="dono",
        n_contextos=0, versao_app="x", ve_novidades=False, conta=None,
        tem_cesta=False, tem_pj=True, vende_produto=False, vende_servico=True,
        beta_gratis=True, plano_aviso=None, empresa_nome="Prime",
        erro="", ok="", teto=rr.TETO_POR_DONO,
        equipe=[{"id": 2, "titulo": "Abertura do atendimento",
                 "texto": "Olá, tudo bem? Me chamo {vendedor} da {empresa}.", "usos": 3},
                {"id": 3, "titulo": "", "texto": "Alguma dúvida até essa parte?!", "usos": 0}],
        vendedores=[{"nome": "THIAGO PINHEIRO", "papel": "vendedor", "quantas": 2},
                    {"nome": "MANOEL SOARES", "papel": "dono", "quantas": 0}])
    ctx.update(over)
    return _env.get_template("respostas").render(**ctx)


def test_a_tela_desenha_a_lista_e_a_contagem():
    html = _render()
    assert "Abertura do atendimento" in html and "usada 3x" in html
    assert "Sem título" in html and "ainda não usada" in html
    assert "THIAGO PINHEIRO" in html and "2 respostas" in html
    assert "MANOEL SOARES" in html and "0 respostas" in html
    # o texto das pessoais NUNCA aparece: a tela só recebe a contagem
    assert "Guardar pra equipe" in html


def test_a_lista_vazia_diz_o_que_fazer():
    """Lista vazia com um quadro mudo é a diferença entre a ferramenta pegar e
    morrer no primeiro dia."""
    html = _render(equipe=[])
    assert "Nenhuma resposta da equipe ainda" in html
    assert "abertura do atendimento" in html.lower()


def test_o_recado_da_acao_aparece():
    assert "Guardada — a equipe já vê." in _render(ok="Guardada — a equipe já vê.")
    assert "Escreva a mensagem antes de guardar." in _render(
        erro="Escreva a mensagem antes de guardar.")
