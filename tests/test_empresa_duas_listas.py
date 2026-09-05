"""Contas a pagar da aba Empresa vira duas listas: liberadas e esperando.

Pedido do dono em 04/09/2026: *"coloca lá na aba Empresa, em contas a pagar, uma
lista de contas autorizadas depois que ele liberar lá no relatório"* — e, quando
perguntei, escolheu **duas listas separadas** em vez de filtrar. Aprovou o mockup
em `docs/mockups/empresa_duas_listas.html`.

Antes era uma lista só, com os três estados misturados: pra saber o que dava pra
pagar era preciso ler o selo linha por linha. No estado real da Prime naquele dia
— 32 contas a pagar, sendo 1 liberada, 1 recusada e 30 aguardando — isso é ler 32
selos pra achar uma conta.

Cada bloco responde uma pergunta:

    ✅ Liberadas — pode pagar     o que dá pra pagar agora
    ⏳ Esperando liberação        o que ainda depende de você
    A receber                     o que entra (sem liberação nenhuma)

O que este teste protege:

  * **os botões seguem o bloco.** Liberar e recusar só existem em "esperando" —
    no bloco de cima eles não têm o que fazer, e apareciam mesmo assim;
  * **a recusada fica com quem espera**, não com quem pode pagar. Ela não foi
    liberada, e é isso que a lista de cima promete;
  * **as a receber ficam de fora das duas.** Liberação não existe pra elas —
    ninguém autoriza dinheiro entrando —, e como nascem `autorizado` cairiam em
    "liberadas" com o rótulo mentindo;
  * **o "dar baixa" continua em todos os blocos**, inclusive no que espera: a
    regra do dono, de 03/09/2026, é "só avisa, não trava".
"""
import re

import pytest
from jinja2 import DictLoader, Environment

from web import portal as pt


def _monta(liberadas=0, esperando=0, recusadas=0, receber=0, com_conciliar=False):
    """Roda o trecho REAL do template com blocos montados como a rota monta."""
    from datetime import date
    tpl = pt._EMPRESA
    i = tpl.index("{% macro tit_linha")
    j = tpl.index('{% else %}<div class="mut" style="font-size:.85rem">'
                  "Nenhum título em aberto")
    env = Environment(loader=DictLoader({"t": tpl[i:j]}))
    env.filters["brl"] = pt.brl
    env.filters["n2"] = lambda v: f"{v:.2f}"

    def t(desc, apro, tipo="pagar", cent=10000, motivo=None, conc=None):
        return {"id": 1, "descricao": desc, "contraparte": "FORNECEDOR X",
                "valor_centavos": cent, "aprovacao": apro, "tipo": tipo,
                "vencimento": date(2026, 9, 15), "atrasado": False,
                "prazo": "em 11 dias", "cliente_nome": None, "cliente_id": None,
                "criado_nome": None, "aprovado_nome": None,
                "aprovacao_motivo": motivo, "sem_fornecedor": False,
                "conciliar": conc, "cobranca_link_url": None}

    conc = {"lancamento_id": 7, "resumo": "10/08, R$ 100,00",
            "titulo": "Ligar", "confirmar": "x"} if com_conciliar else None
    lib = [t(f"LIB {n}", "autorizado", conc=conc) for n in range(liberadas)]
    esp = ([t(f"ESP {n}", "aguardando", conc=conc) for n in range(esperando)]
           + [t(f"REC {n}", "recusado", motivo="não foi") for n in range(recusadas)])
    rec = [t(f"RECEB {n}", "autorizado", tipo="receber") for n in range(receber)]
    blocos = [
        {"titulo": "✅ Liberadas — pode pagar", "cor": "ok", "decide": False,
         "itens": lib, "centavos": 10000 * len(lib), "dica": ""},
        {"titulo": "⏳ Esperando liberação", "cor": "esp", "decide": True,
         "itens": esp, "centavos": 10000 * len(esp),
         "dica": "libere várias de uma vez no relatório de Contas a pagar" if esp else ""},
        {"titulo": "A receber", "cor": "rec", "decide": False, "itens": rec,
         "centavos": 10000 * len(rec),
         "dica": "sem liberação: ninguém autoriza dinheiro entrando" if rec else ""},
    ]
    return env.get_template("t").render(tit_blocos=blocos, pode_liberar=True)


def _bloco(html, cor):
    """O pedaço do HTML que pertence a um bloco."""
    i = html.index(f'class="tit-bloco {cor}"')
    resto = html[i:]
    prox = resto.find('class="tit-bloco ', 10)
    return resto[:prox] if prox > 0 else resto


# ═══════════════ os três blocos ═══════════════
def test_os_tres_blocos_aparecem_com_contagem_e_total():
    html = _monta(liberadas=2, esperando=3, receber=1)
    assert "✅ Liberadas — pode pagar" in html
    assert "⏳ Esperando liberação" in html
    assert "A receber" in html
    assert "2 · R$ 200,00" in html and "3 · R$ 300,00" in html


def test_bloco_vazio_nao_aparece():
    """Cabeçalho de lista vazia é ruído — e "0 liberadas" não é informação nova."""
    html = _monta(liberadas=0, esperando=2, receber=0)
    assert "Liberadas" not in html
    assert "A receber" not in html
    assert "Esperando liberação" in html


# ═══════════════ os botões seguem o bloco ═══════════════
def test_liberar_e_recusar_so_existem_no_bloco_que_espera():
    """No bloco de cima eles não têm o que fazer — e apareciam mesmo assim."""
    html = _monta(liberadas=1, esperando=1)
    assert "✓ liberar" not in _bloco(html, "ok")
    assert "✕ recusar" not in _bloco(html, "ok")
    assert "✓ liberar" in _bloco(html, "esp")
    assert "✕ recusar" in _bloco(html, "esp")


def test_a_caixa_de_marcar_tambem_so_existe_no_que_espera():
    html = _monta(liberadas=1, esperando=1)
    assert 'class="tit-ck"' not in _bloco(html, "ok")
    assert 'class="tit-ck"' in _bloco(html, "esp")


def test_dar_baixa_existe_em_todos_os_blocos():
    """Regra do dono, 03/09/2026: "só avisa, não trava". Quem quiser pagar uma
    conta não liberada consegue — e fica registrado."""
    html = _monta(liberadas=1, esperando=1, receber=1)
    for cor in ("ok", "esp", "rec"):
        assert "dar baixa ✓" in _bloco(html, cor), cor


def test_ja_foi_paga_existe_nos_dois_blocos_de_pagar():
    """A conciliação não depende de liberação: o dinheiro já saiu."""
    html = _monta(liberadas=1, esperando=1, com_conciliar=True)
    assert "já foi paga" in _bloco(html, "ok")
    assert "já foi paga" in _bloco(html, "esp")


# ═══════════════ onde cada estado cai ═══════════════
def test_a_recusada_fica_com_quem_espera():
    """Ela não foi liberada — e "pode pagar" é o que a lista de cima promete."""
    html = _monta(liberadas=1, recusadas=1)
    assert "REC 0" in _bloco(html, "esp")
    assert "REC 0" not in _bloco(html, "ok")


def test_a_receber_nao_cai_em_liberadas():
    """Elas nascem `autorizado` porque liberação não existe pra dinheiro entrando.
    Sem bloco próprio, cairiam em "liberadas" e o rótulo estaria mentindo."""
    html = _monta(liberadas=1, receber=1)
    assert "RECEB 0" in _bloco(html, "rec")
    assert "RECEB 0" not in _bloco(html, "ok")
    assert "✓ liberar" not in _bloco(html, "rec")


# ═══════════════ como a rota separa ═══════════════
def _corpo_da_rota() -> str:
    fonte = open(pt.__file__, encoding="utf-8").read()
    corpo = fonte.split('titulos = emp.listar_titulos(pool, conta[0], status="aberto")')[1]
    return corpo[:corpo.index("titulos_pagos = ")]


def test_a_rota_separa_por_aprovacao_e_por_tipo():
    corpo = _corpo_da_rota()
    assert 'aprovacao") == "autorizado"' in corpo
    assert 'aprovacao") != "autorizado"' in corpo
    assert 'tipo"] != "pagar"' in corpo, "as a receber precisam de bloco próprio"


def test_a_ordem_e_a_de_uso():
    """Primeiro o que dá pra pagar hoje, depois o que depende do dono, por último
    o que entra."""
    corpo = _corpo_da_rota()
    assert corpo.index("Liberadas") < corpo.index("Esperando liberação") \
        < corpo.index('"A receber"')


def test_so_o_bloco_que_espera_decide():
    corpo = _corpo_da_rota()
    decides = re.findall(r'"decide": (True|False)', corpo)
    assert decides == ["False", "True", "False"], decides


# ═══════════════ a linha em si ═══════════════
def test_o_nome_do_fornecedor_nao_sai_duas_vezes():
    """31 de 31 títulos a pagar da Prime repetiam o nome: uma vez ao lado da
    descrição e outra no 👤 de baixo, porque o cadastro vinculado tem o mesmo
    nome da contraparte digitada."""
    assert ("t.contraparte|lower|trim != (t.cliente_nome or '')|lower|trim"
            in pt._EMPRESA)


def test_a_linha_e_um_macro_e_nao_tres_copias():
    """Três blocos com a marcação copiada seria três lugares pra corrigir cada
    conserto — e dois deles seriam esquecidos."""
    assert pt._EMPRESA.count("{% macro tit_linha") == 1
    # só na região dos ABERTOS: a lista de títulos pagos é outra seção, com
    # marcação própria, e não faz parte destes três blocos.
    abertos = pt._EMPRESA[:pt._EMPRESA.index("{% for t in titulos_pagos %}")]
    assert abertos.count('<div class="tit-lin">') == 1, \
        "a linha foi copiada por bloco — seriam três lugares pra cada conserto"


# ═══════════════ a pílula de filtro (05/09/2026) ═══════════════
#
# Os três blocos empilhados e sempre abertos foram a reclamação seguinte do
# dono: "não gostei das divisões, dá uma melhorada". Com 28+ contas em
# "Esperando liberação", ver "A receber" era rolar por tudo antes. A pílula
# troca QUAL bloco fica visível — o bloco em si (cabeçalho, cada `tit_linha`)
# não muda nada, só um aparece por vez.

def _render_filtro(liberadas=0, esperando=0, receber=0):
    """Como `_monta`, mas o trecho extraído começa ANTES do macro — inclui a
    faixa de pílulas, que `_monta` propositalmente não pega (ela testa só a
    linha e os blocos, sem se importar com o que decide a visibilidade)."""
    tpl = pt._EMPRESA
    i = tpl.index("{#- O FILTRO.")
    j = tpl.index('{% else %}<div class="mut" style="font-size:.85rem">'
                  "Nenhum título em aberto")
    env = Environment(loader=DictLoader({"t": tpl[i:j]}))
    env.filters["brl"] = pt.brl
    env.filters["n2"] = lambda v: f"{v:.2f}"

    from datetime import date

    def t(desc, apro, tipo="pagar"):
        return {"id": 1, "descricao": desc, "contraparte": "", "valor_centavos": 10000,
                "aprovacao": apro, "tipo": tipo, "vencimento": date(2026, 9, 15),
                "atrasado": False, "prazo": "em 11 dias", "cliente_nome": None,
                "cliente_id": None, "criado_nome": None, "aprovado_nome": None,
                "aprovacao_motivo": None, "sem_fornecedor": False, "conciliar": None,
                "cobranca_link_url": None, "periodicidade": None,
                "valor_variavel": False, "proxima": None}

    lib = [t(f"LIB {n}", "autorizado") for n in range(liberadas)]
    esp = [t(f"ESP {n}", "aguardando") for n in range(esperando)]
    rec = [t(f"RECEB {n}", "autorizado", tipo="receber") for n in range(receber)]
    blocos = [
        {"titulo": "✅ Liberadas — pode pagar", "cor": "ok", "decide": False,
         "itens": lib, "centavos": 10000 * len(lib), "dica": ""},
        {"titulo": "⏳ Esperando liberação", "cor": "esp", "decide": True,
         "itens": esp, "centavos": 10000 * len(esp), "dica": ""},
        {"titulo": "A receber", "cor": "rec", "decide": False, "itens": rec,
         "centavos": 10000 * len(rec), "dica": ""},
    ]
    return env.get_template("t").render(
        tit_blocos=blocos, titulos=lib + esp + rec, pode_liberar=True,
        RITMOS=[], RITMO_SELO={})


def _tag_do_bloco(html, cor):
    m = re.search(r'<div class="tit-bloco %s"[^>]*>' % cor, html)
    assert m, f"bloco {cor} não achado"
    return m.group(0)


def test_filtro_so_aparece_com_mais_de_um_bloco():
    """Um bloco só não precisa de pílula pra escolher — não tem o que escolher."""
    assert '<div class="tit-filtro">' not in _render_filtro(esperando=2)
    assert '<div class="tit-filtro">' in _render_filtro(liberadas=1, esperando=2)


def test_esperando_liberacao_comeca_visivel_por_padrao():
    """É o único bloco que pede uma decisão — os outros dois são só consulta."""
    html = _render_filtro(liberadas=1, esperando=2, receber=1)
    assert "display:none" not in _tag_do_bloco(html, "esp")
    assert "display:none" in _tag_do_bloco(html, "ok")
    assert "display:none" in _tag_do_bloco(html, "rec")


def test_sem_esperando_o_primeiro_bloco_com_itens_comeca_visivel():
    """Sem nada esperando decisão, mostra o que existe — não uma tela vazia."""
    html = _render_filtro(liberadas=1, receber=1)
    assert "display:none" not in _tag_do_bloco(html, "ok")
    assert "display:none" in _tag_do_bloco(html, "rec")


def test_pilula_mostra_a_contagem_de_cada_bloco():
    html = _render_filtro(liberadas=1, esperando=3, receber=2)
    assert "<b>1</b>✅ Liberadas — pode pagar" in html
    assert "<b>3</b>⏳ Esperando liberação" in html
    assert "<b>2</b>A receber" in html
    assert "<b>6</b>Tudo" in html


def test_a_pilula_tudo_sempre_existe_quando_ha_filtro():
    html = _render_filtro(liberadas=1, esperando=1)
    assert 'data-alvo=""' in html


def test_o_template_compila():
    Environment().parse(pt._EMPRESA)
