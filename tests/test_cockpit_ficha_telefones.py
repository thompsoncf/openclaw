"""Os dois números do lead no app do vendedor, e a grade de atalhos.

`prospeccao` guarda `whatsapp` e `telefone` em colunas SEPARADAS, e quem entra por
`whatsapp_inbound` nasce com o número em `whatsapp` e `telefone` NULL. Duas consequências
apareciam na tela do vendedor — que é justamente o app de quem atende esses leads:

1. a ficha tinha só o campo "Telefone", então nascia **em branco** com o número logo ali,
   e não havia como ver nem corrigir o WhatsApp por ali;
2. o atalho "Ligar" saía **cinza** (o `tel_link` só olha `telefone`) na maioria dos leads,
   ocupando o melhor lugar da tela — e, como a `.grade` é de duas colunas, o quinto
   atalho ainda nascia sozinho numa terceira linha.

O teste do dado está em test_cockpit.py (salvar_ficha). Aqui é a TELA.
"""
import inspect
import re

from web import painel_cockpit as pc


class _Req:
    """Request só com o que a tela lê (`_flash` mexe na sessão)."""
    def __init__(self):
        self.session = {}


def _lead_falso(**extra):
    d = {"empresa": "Padaria", "cidade": "Teresina", "uf": "PI", "doc_fmt": "",
         "mensagens": [], "ia": False, "status": "novo", "etapas": [],
         "zap_link": "https://wa.me/5586999990001", "tel_link": ""}
    d.update(extra)
    return d


def _html(d):
    return pc._lead_vendedor(_Req(), 7, d).body.decode()


# ── a grade de atalhos ────────────────────────────────────────────────────
def test_a_tela_do_vendedor_nao_tem_mais_o_botao_ligar():
    assert "Ligar" not in _html(_lead_falso())


def test_a_grade_fica_com_quatro_atalhos_pares():
    """`.grade` é `grid-template-columns:1fr 1fr`. Número ímpar de atalhos deixa o
    último meia-largura, com um buraco do lado — foi o que motivou o ajuste."""
    assert ".grade{display:grid;grid-template-columns:1fr 1fr" in pc._CSS
    html = _html(_lead_falso())
    for rot in ("WhatsApp", "Ficha", "Orçamento", "Visita"):
        assert rot in html, f"sumiu o atalho {rot}"
    # não-guloso até o primeiro </div>: os atalhos são <a>/<span>, não têm div dentro
    grade = re.search(r"<div class=grade>(.*?)</div>", html, re.S).group(1)
    assert grade.count("</a>") + grade.count("</span>") == 4


def test_o_whatsapp_desligado_vira_atalho_apagado_e_nao_some():
    """Sem número, o atalho continua ocupando o lugar dele (como `.off`): a grade não
    pode reflowar e trocar a posição dos outros três de um lead pro outro."""
    html = _html(_lead_falso(zap_link=""))
    assert "class=off" in html and "WhatsApp" in html


def test_o_gestor_continua_com_o_ligar():
    """A retirada é do app do vendedor. A tela do gestor (`_lead_gestor`) mostra um lead
    que o gestor pode não estar conversando por WhatsApp — lá o botão faz sentido, e o
    ícone `i-ligar` continua sendo usado por ela."""
    assert "Ligar" in inspect.getsource(pc._lead_gestor)
    assert 'id="i-ligar"' in pc._ICONES


# ── a ficha ───────────────────────────────────────────────────────────────
def test_a_ficha_tem_os_dois_campos_separados():
    fonte = inspect.getsource(pc.cockpit_ficha_tela)
    assert 'campo("whatsapp"' in fonte, "a ficha do vendedor não mostra o WhatsApp"
    assert 'campo("telefone"' in fonte, "o telefone continua sendo um campo próprio"
    assert 'd.get("whatsapp")' in fonte, "o campo tem que vir PREENCHIDO com o número"


def test_a_rota_de_salvar_aceita_o_whatsapp():
    """Sem isto o campo aparece na tela, o vendedor digita, e o valor é descartado em
    silêncio no POST — o pior dos dois mundos."""
    assert "whatsapp" in inspect.signature(pc.cockpit_ficha).parameters
