"""Na clínica, "cliente" e "lead" viram "paciente" (pedido do dono em 26/09/2026,
docs/mockups/clinica_prontuario.html, seção 14).

O nome de quem compra vem do perfil do nicho (regra 6 do CLAUDE.md): os templates
dizem {{ voc.cliente }} / {{ voc.lead }}, e `finance.raio_x_perfil.vocabulario_pessoa`
decide a palavra. Esta trava garante que (1) nenhum texto de tela desses arquivos
volta a escrever a palavra à mão, (2) a clínica lê "paciente" e (3) nenhum outro
nicho passa a ler "paciente".
"""
import ast
import io
import re
from pathlib import Path

import pytest

from finance import nichos
from finance import raio_x_perfil as rxp

RAIZ = Path(__file__).resolve().parent.parent

#: os arquivos de tela que a conta da clínica vê
ARQUIVOS = ("web/portal.py", "web/painel_prospeccao.py", "web/painel_servicos.py", "web/painel_origens.py",
            "web/painel_follow_up.py", "web/painel_raio_x.py", "web/painel_equipe.py", "web/painel_agenda.py")

#: o que pode continuar escrito: a clínica como cliente DO ZAQ (a conexão do WhatsApp)
PERMITIDO = ("Como este cliente conecta o WhatsApp", "do cliente, direto na Meta")

_PALAVRA = re.compile(r"\b(clientes|cliente|leads|lead)\b", re.IGNORECASE)
_ATTR = re.compile(r'\b(?:placeholder|title|aria-label|alt)="([^"]*)"', re.IGNORECASE)
_CONFIRM = re.compile(r"(?:confirm|alert)\('([^']*)'\)")
_JINJA = re.compile(r"(\{\{.*?\}\}|\{%.*?%\}|\{#.*?#\})", re.S)
_BLOCO = re.compile(r"(<script\b.*?</script>|<style\b.*?</style>)", re.S | re.IGNORECASE)
_TAG = re.compile(r"(<[^<>]*>)", re.S)


def _textos_visiveis(modelo: str):
    """O texto que a pessoa lê num modelo Jinja: entre as tags, os atributos que
    aparecem (placeholder, title, aria-label, alt) e os confirm/alert."""
    s = _JINJA.sub("⦗", modelo)
    for parte in _BLOCO.split(s):
        if _BLOCO.fullmatch(parte or ""):
            continue
        for tp in _TAG.split(parte):
            if _TAG.fullmatch(tp or ""):
                yield from _ATTR.findall(tp)
                yield from _CONFIRM.findall(tp)
            else:
                yield tp


def _modelos(caminho: Path):
    """As strings literais que são modelo Jinja, sem docstring e sem JS cru."""
    arvore = ast.parse(caminho.read_text(encoding="utf-8"))
    docstrings = set()
    for no in ast.walk(arvore):
        if isinstance(no, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and no.body:
            primeiro = no.body[0]
            if isinstance(primeiro, ast.Expr) and isinstance(primeiro.value, ast.Constant):
                docstrings.add(id(primeiro.value))
    for no in ast.walk(arvore):
        if (isinstance(no, ast.Constant) and isinstance(no.value, str) and id(no) not in docstrings
                and ("{%" in no.value or "{{" in no.value) and not no.value.lstrip().startswith("(function")):
            yield no.lineno, no.value


@pytest.mark.parametrize("arquivo", ARQUIVOS)
def test_texto_de_tela_nao_escreve_cliente_nem_lead_a_mao(arquivo):
    achados = []
    for linha, modelo in _modelos(RAIZ / arquivo):
        for txt in _textos_visiveis(modelo):
            if any(p in txt for p in PERMITIDO):
                continue
            for m in _PALAVRA.finditer(txt):
                achados.append(f"{arquivo}, modelo da linha {linha}: “{txt.strip()[:80]}”")
    assert not achados, ("Use {{ voc.cliente }} / {{ voc.lead }} (o perfil do nicho decide a palavra):\n"
                         + "\n".join(achados[:20]))


def test_a_clinica_le_paciente():
    v = rxp.perfil("clinica")["vocab"]
    assert (v["cliente"], v["clientes"], v["lead"], v["leads"]) == ("paciente", "pacientes", "paciente", "pacientes")
    # o que o perfil já tinha continua lá
    assert v["compromisso"] == "avaliação"


def test_nenhum_outro_nicho_le_paciente():
    for slug in nichos.NICHOS:
        v = rxp.perfil(slug)["vocab"]
        if rxp.perfil_por_nicho(slug) == "clinica":
            continue
        assert (v["cliente"], v["lead"]) == ("cliente", "lead"), slug
    assert rxp.perfil(None)["vocab"]["cliente"] == "cliente"


def test_a_base_diz_pacientes_na_clinica_e_clientes_no_resto():
    from web import portal
    t = portal._env.get_template("clientes")
    ctx = dict(clientes=[], total=0, busca="", papel_filtro="", dup_n=0)
    clinica = t.render(voc=rxp.perfil("clinica")["vocab"], **ctx)
    assert "Pacientes/Fornecedores" in clinica and "+ novo paciente" in clinica
    assert "Clientes/Fornecedores" not in clinica
    outro = t.render(**ctx)                     # sem conta: o global do _env (padrão)
    assert "Clientes/Fornecedores" in outro and "+ novo cliente" in outro


def test_o_render_poe_o_voc_do_perfil_da_conta():
    """`_render` põe o `voc` do perfil no contexto: é o que faz a tela da clínica
    dizer paciente sem cada rota passar nada."""
    src = (RAIZ / "web/portal.py").read_text(encoding="utf-8")
    assert 'ctx["voc"] = ctx["raio_x_perfil"].get("vocab")' in src
    assert '_env.globals["voc"]' in src


# ------------------------------------------------------------------ 2ª leva: Cockpit e janela

def test_o_cockpit_fala_paciente_na_clinica():
    """O app do celular monta HTML em Python: a conta liga o vocabulário ao entrar
    (`_sessao`/`_gerencia`) e o texto pergunta por `_p`. O JS lê `window.VOC`."""
    from web import painel_cockpit as pc
    tok = pc._VOC.set(rxp.perfil("clinica")["vocab"])
    try:
        assert (pc._p("lead"), pc._P("leads"), pc._p("cliente")) == ("paciente", "Pacientes", "paciente")
        assert pc._erro({"erro": "escopo"}) == "Esse paciente não é seu."
        pagina = pc._page("x", "").body.decode()
        assert '"Lead": "Paciente"' in pagina and '"cliente": "paciente"' in pagina
    finally:
        pc._VOC.reset(tok)
    # fora da clínica (e sem conta): o de sempre
    assert (pc._p("lead"), pc._erro({"erro": "escopo"})) == ("lead", "Esse lead não é seu.")


def test_o_cockpit_nao_escreve_lead_a_mao_nos_textos_trocados():
    src = (RAIZ / "web/painel_cockpit.py").read_text(encoding="utf-8")
    for velho in ("O cliente apareceu?", "Nenhum lead aberto agora", "Leads da equipe\"",
                  "Esse lead não tem WhatsApp", "Enviar na conversa do lead", "De onde veio o cliente"):
        assert velho not in src, velho


def test_a_janela_do_contato_le_o_voc_da_pagina():
    from web import janela_lead as jl
    from web import portal
    assert "function jVoc(k)" in jl.JS and "O que o '+jVoc('cliente')+' quer" in jl.JS
    base = portal._env.get_template("base").render(voc=rxp.perfil("clinica")["vocab"])
    assert '"Lead":"Paciente"' in base.replace(" ", "")
