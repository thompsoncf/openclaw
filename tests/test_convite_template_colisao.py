"""Regressão: o convite de EQUIPE (/equipe/convite/{token}) não pode renderizar o
convite de AGENDA. Os dois módulos registravam template no mesmo nome 'convite' no
loader compartilhado — como a agenda é importada depois, ela sobrescrevia o da
equipe, e o link de convite de equipe abria a tela de confirmar reunião.

Agora o da equipe é 'convite_equipe'. Este teste garante que os dois coexistem
distintos e que painel_equipe não volta a usar o nome 'convite'.
"""
import web.painel_agenda as pa  # noqa: F401  (registra o template 'convite' da agenda)
import web.painel_equipe as pe  # noqa: F401  (registra 'convite_equipe')
from web.portal import _env


def test_templates_convite_nao_colidem():
    mapa = _env.loader.mapping
    assert "convite_equipe" in mapa          # equipe tem nome próprio
    assert "convite" in mapa                 # agenda mantém o seu
    assert mapa["convite_equipe"] != mapa["convite"]
    # o de equipe é mesmo o de criar senha da equipe
    eq_tpl = mapa["convite_equipe"].lower()
    assert "senha" in eq_tpl and "equipe" in eq_tpl
    # os dois compilam
    _env.get_template("convite_equipe")
    _env.get_template("convite")


def test_painel_equipe_nao_usa_template_convite_generico():
    """Se alguém reintroduzir _render('convite', ...) na equipe, a colisão volta."""
    src = open("web/painel_equipe.py", encoding="utf-8").read()
    assert '_render("convite"' not in src
    assert '_env.loader.mapping["convite"]' not in src
    assert '_env.loader.mapping["convite_equipe"]' in src
