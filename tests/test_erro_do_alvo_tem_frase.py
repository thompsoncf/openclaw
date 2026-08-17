"""A ficha do lead dizia "erro" e mais nada.

`campanha_alvos` guarda a falha em dois campos: `wa_erro_codigo` (do provedor) e
`wa_erro_msg` (texto). A tela lia SÓ a mensagem — e a maior parte das falhas chega
por webhook de status, que traz o `ErrorCode` sem frase nenhuma junto. Resultado:
o motivo estava gravado o tempo todo e o dono via um tooltip vazio.

Medido em produção: 40 alvos em erro numa conta, TODOS com código e nenhum com
frase. Nenhum deles dizia por que falhou.

`rotulo_erro_alvo` fecha isso: mensagem do provedor quando existe (é mais
específica que qualquer texto nosso), senão a tradução do código, senão o código
cru — que ainda é uma informação, ao contrário do vazio.
"""
import pytest

from finance import prospec_convite as pc


# ------------------------------------------------- a mensagem do provedor manda

def test_mensagem_do_provedor_ganha_do_rotulo():
    """Ela é mais específica que o texto genérico do código."""
    r = pc.rotulo_erro_alvo("63024", "O número +55 86 9xxx não está no WhatsApp")
    assert r == "O número +55 86 9xxx não está no WhatsApp"


def test_mensagem_em_branco_nao_vale_como_mensagem():
    """'' e '   ' são o caso comum do webhook — tem que cair no código."""
    for vazio in ("", "   ", None):
        assert pc.rotulo_erro_alvo("63024", vazio) == pc.ERRO_ALVO_ROT["63024"]


# --------------------------------------------------- código vira frase legível

@pytest.mark.parametrize("codigo", sorted(pc.ERRO_ALVO_ROT))
def test_codigo_conhecido_vira_frase(codigo):
    r = pc.rotulo_erro_alvo(codigo)
    assert r == pc.ERRO_ALVO_ROT[codigo]
    assert codigo not in r          # é frase pro dono, não o número cru


def test_o_caso_que_apareceu_na_conta():
    """63024 era 37 dos 40 alvos mudos."""
    assert "não tem WhatsApp" in pc.rotulo_erro_alvo("63024")


# ------------------------------------------- código desconhecido não some nem mente

def test_codigo_sem_traducao_mostra_o_numero_em_vez_de_chutar():
    """63049 e 63032 apareceram na conta e o projeto não sabe o significado exato.
    Inventar um motivo é pior que dizer 'recusou, o código é este'."""
    for codigo in ("63049", "63032", "99999"):
        r = pc.rotulo_erro_alvo(codigo)
        assert codigo in r and "recusou" in r


def test_sem_codigo_e_sem_mensagem_devolve_vazio():
    """Aí não há o que dizer mesmo — e a tela não põe tooltip."""
    assert pc.rotulo_erro_alvo(None, None) == ""
    assert pc.rotulo_erro_alvo("", "") == ""


def test_nunca_devolve_so_espaco():
    """Um tooltip com espaço em branco é o mesmo vazio, disfarçado."""
    for cod, msg in (("63024", None), ("63049", ""), (None, "  texto  "), ("", None)):
        r = pc.rotulo_erro_alvo(cod, msg)
        assert r == r.strip()


# ----------------------------------------------- o motivo aparece SEM hover

# Estes testes renderizam a CÉLULA QUE SUBIU: a linha é extraída de
# web/painel_prospeccao.py em vez de copiada pra cá. Uma cópia envelhece em
# silêncio — passaria verde enquanto a tela real regredisse.

def _celula():
    """A linha do template da coluna WhatsApp, direto do arquivo."""
    from pathlib import Path
    fonte = Path(__file__).resolve().parent.parent / "web" / "painel_prospeccao.py"
    for linha in fonte.read_text(encoding="utf-8").splitlines():
        if "l.wa_erro" in linha and "<td" in linha:
            return linha.strip()
    raise AssertionError("não achei a célula da coluna WhatsApp no painel")


def _render(**lead):
    """Mesmo env do portal: DictLoader com nome SEM extensão, que é o que faz
    `select_autoescape()` resolver pra False. É por isso que o template escapa
    com `|e` na mão — se alguém ligar o autoescape, estes testes seguem válidos."""
    from jinja2 import DictLoader, Environment, select_autoescape
    env = Environment(loader=DictLoader({"prospeccao_campanha": _celula()}),
                      autoescape=select_autoescape())
    lead.setdefault("fone", "(86) 99999-0000")
    lead.setdefault("wa_rot", "💬 erro")
    lead.setdefault("wa_erro", "")
    return env.get_template("prospeccao_campanha").render(l=lead)


def test_motivo_aparece_como_texto_e_nao_so_no_title():
    """O dono decide recolocar o lead na fila por este motivo, e cada tentativa é
    uma mensagem cobrada. No celular não existe hover: se só houver `title`, a
    decisão é tomada no escuro."""
    html = _render(wa_erro="Este número não tem WhatsApp.")
    assert 'class="wa-why"' in html
    # fora de qualquer atributo — texto de verdade na linha
    assert ">Este número não tem WhatsApp.</div>" in html


def test_lead_sem_erro_nao_ganha_linha_nova():
    """Quem foi entregue não pode virar linha dupla na tabela."""
    assert "wa-why" not in _render(wa_rot="✅ entregue", wa_erro="")


def test_texto_do_provedor_e_escapado():
    """`wa_erro_msg` vem do Twilio/Meta pelo webhook — é texto EXTERNO. Antes ele
    só ia pro `title` (escapado); agora é renderizado visível, então o escape virou
    questão de segurança, não de estética. O autoescape está desligado nestes
    templates, quem protege é o `|e` do template."""
    html = _render(wa_erro='<img src=x onerror=alert(1)>')
    assert "<img" not in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html


def test_aspas_na_mensagem_nao_quebram_o_title():
    """Aspas soltas fechariam o atributo e o resto viraria markup."""
    html = _render(wa_erro='falhou: "numero" invalido')
    assert 'title="falhou: &#34;numero&#34; invalido"' in html or "&quot;" in html
    assert html.count("<div") == html.count("</div>")
