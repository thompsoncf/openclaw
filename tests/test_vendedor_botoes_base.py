"""Os BOTÕES que o vendedor liberado enxerga na Base e em Campanhas.

Contexto: o #523 abriu as rotas de campanha mas deixou os botões escondidos atrás
de `{% if gerencia %}` no template. Rota aberta com botão escondido é o mesmo que
rota fechada — o vendedor não tinha por onde clicar. Estes testes travam os dois
lados juntos pra não repetir o erro.

O template mora numa string Python (não há arquivo .html), então o jeito de
afirmar sobre ele é inspecionar o fonte. É um guard de regressão, não um teste de
renderização: ele pega alguém reintroduzindo o gate errado.
"""
import io
import re

FONTE = io.open("web/painel_prospeccao.py", encoding="utf-8").read()


def _gate_do_trecho(marca: str) -> str | None:
    """Qual `{% if ... %}` está imediatamente antes do trecho."""
    i = FONTE.find(marca)
    assert i > 0, f"trecho sumiu do template: {marca[:50]}"
    antes = FONTE[max(0, i - 260):i]
    achados = re.findall(r"\{%\s*if\s+(\w+)\s*%\}", antes)
    return achados[-1] if achados else None


# ---------------------------------------------------------------- liberados

def test_criar_campanha_visivel_pro_vendedor_liberado():
    """O botão que faltava no print. É a razão de existir do #523."""
    assert _gate_do_trecho("＋ Criar</button>") == "gere_campanha"


def test_jogar_na_campanha_visivel():
    """O select + Jogar → : as rotas add-campanha/tirar-campanha já estavam
    abertas, mas o bloco INTEIRO estava dentro do if de gerência.

    Aqui não dá pra usar o _gate_do_trecho: entre o gate externo e o botão há um
    `{% if ver_camp %}` (tirar × jogar), que é a condição de dentro. O que
    importa é o gate de FORA — por isso a asserção é sobre o par de linhas."""
    bloco = '{% if gere_campanha %}\n        {% if ver_camp %}'
    assert bloco in FONTE, "o bloco de campanha voltou a exigir gerência"
    assert "{% if gerencia %}\n        {% if ver_camp %}" not in FONTE


def test_botoes_de_enriquecimento_visiveis():
    for onclick in ("baseMarcarSemCnpj()", "baseEnriquecer('cnpj')",
                    "baseEnriquecer('decisor')", "baseExplorium()"):
        assert _gate_do_trecho(f'onclick="{onclick}"') == "gere_campanha", onclick


# ------------------------------------------------- o que NÃO pode ter aberto

def test_filtro_por_vendedor_continua_so_da_gerencia():
    """Serve pra ver a carteira dos OUTROS. É gerência, não venda — e foi o único
    item que o dono pediu explicitamente pra deixar de fora."""
    assert _gate_do_trecho('<select class="fld" name="vendedor"') == "gerencia"


def test_excluir_campanha_continua_so_da_gerencia():
    """Apagar campanha devolve os leads pro funil e afeta o trabalho de todos."""
    assert _gate_do_trecho("campanhas/{{ c.id }}/excluir") == "gerencia"


# ------------------------------------------------------- gates das rotas

def test_rotas_pagas_usam_o_gate_novo():
    """cnpj e decisor tinham bloqueio DURO por gerência (não era só escopo, como
    eu tinha afirmado no #523). Agora respondem ao mesmo gate dos botões."""
    assert "Só o dono/gestor busca CNPJ" not in FONTE
    assert "Só o dono/gestor busca decisor" not in FONTE
    # 5 rotas passaram a responder ao gate novo: cnpj, decisor e os 3 do Explorium.
    # A contagem é o que garante que nenhuma ficou pra trás — e também que eu não
    # abri de mais: as outras rotas com "Só o dono/gestor." (Instagram/Meta, linhas
    # ~2788 e ~2827) continuam de gerência e NÃO entram nesta conta.
    assert FONTE.count("Peça ao dono pra liberar campanhas pra você.") == 5
    assert FONTE.count('"Só o dono/gestor."') == 2


def test_consulta_paga_fica_no_escopo_do_vendedor():
    """A trava que impede o vendedor de gastar a fatura da empresa: existe
    'marcar todos' no cabeçalho, então sem escopo ele enriqueceria a base inteira
    com consulta paga. O caminho grátis já fazia isso; os pagos não faziam porque
    só gerência chegava neles."""
    assert FONTE.count('_esc = "" if ctx["gerencia"] else " and vendedor_id=%s"') == 2


def test_lead_do_explorium_nasce_do_vendedor():
    """Sem vendedor_id o lead importado cai sem dono — o vendedor paga a consulta
    e não enxerga o que comprou."""
    assert 'None if ctx["gerencia"] else ctx["membro_id"]))' in FONTE
    assert "enriquecido_em, criado_por, vendedor_id)" in FONTE
