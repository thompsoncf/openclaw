"""Regressão: o filtro "⚠ sem conta" não pode se disfarçar de "Todos".

O painel Financeiro tem duas dimensões de filtro que dividem a MESMA barra de
abas: a natureza (Todos / Pessoal / Empresa / A definir) e o atalho
"⚠ sem conta". O `sem_conta=1` força `natureza='empresa'` no servidor, mas não
preenche o parâmetro `natureza` — então a aba "Todos" continuava pintada como
ativa enquanto a lista mostrava só lançamentos de empresa sem conta contábil.
O usuário lia "Todos" e concluía que os lançamentos PESSOAIS tinham sumido.

Aqui a gente pega o HTML REAL da barra de abas (do template 'dash', não uma
cópia) e confere que:
  • sem filtro     -> "Todos" ativo, sem aviso;
  • com sem_conta=1 -> "Todos" NÃO ativo e o aviso do filtro aparece.
"""
import jinja2

from web.portal import _env

# Marca de "aba ativa" no template (fundo verde + negrito).
_ATIVA = "background:var(--verde)"


def _fonte_dash() -> str:
    return _env.loader.mapping["dash"]


def _render(trecho: str, **ctx) -> str:
    """Renderiza um pedaço do template real, isolado do resto da página."""
    return jinja2.Environment(autoescape=True).from_string(trecho).render(**ctx)


def _linha_aba_todos() -> str:
    for linha in _fonte_dash().splitlines():
        if linha.rstrip().endswith(">Todos</a>"):
            return linha
    raise AssertionError("não achei a aba 'Todos' no template dash")


def _bloco_aviso_filtro() -> str:
    # o aviso mora em UMA linha do template (tem {% if %} aninhado no href, então
    # casar por regex até o primeiro {% endif %} truncaria o bloco no meio).
    for linha in _fonte_dash().splitlines():
        if linha.lstrip().startswith("{% if sem_conta_sel %}"):
            return linha
    raise AssertionError("não achei o aviso de filtro 'sem conta' no template dash")


def test_aba_todos_fica_ativa_quando_nao_ha_filtro():
    html = _render(_linha_aba_todos(), mes_sel="2026-08", membro_sel=None,
                   natureza_sel="", sem_conta_sel=False)
    assert _ATIVA in html


def test_aba_todos_nao_fica_ativa_com_filtro_sem_conta():
    """O caso do bug: natureza vazia + sem_conta ligado pintava "Todos" de ativo
    enquanto pessoal/a-definir estavam escondidos."""
    html = _render(_linha_aba_todos(), mes_sel="2026-08", membro_sel=None,
                   natureza_sel="", sem_conta_sel=True)
    assert _ATIVA not in html


def test_aba_todos_nao_fica_ativa_com_natureza_escolhida():
    html = _render(_linha_aba_todos(), mes_sel="2026-08", membro_sel=None,
                   natureza_sel="pessoal", sem_conta_sel=False)
    assert _ATIVA not in html


def test_aviso_do_filtro_aparece_so_com_sem_conta_ligado():
    bloco = _bloco_aviso_filtro()
    ligado = _render(bloco, mes_sel="2026-08", membro_sel=None, sem_conta_sel=True)
    desligado = _render(bloco, mes_sel="2026-08", membro_sel=None, sem_conta_sel=False)

    assert desligado.strip() == ""
    # diz o que está escondido e oferece a saída em 1 clique
    assert "pessoais" in ligado
    assert "ver todos" in ligado
    assert 'href="/painel/financeiro?mes=2026-08"' in ligado
