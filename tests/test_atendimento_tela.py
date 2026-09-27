"""A tela da vista Atendimento (docs/mockups/funil_atendimento.html) — sem banco.

A rota é só da gerência e só dos nichos com a vista; o quadro de Vendas ganha a chave
Vendas | Atendimento e a pílula "Quem atende"; a barra de Relatórios ganha o Desafio;
e o desenho das colunas e da régua sai do que finance/atendimento.py devolve."""
import inspect
from datetime import datetime, timezone

from finance import atendimento as at
from web import painel_prospeccao as pp
from web import painel_relatorios as prl
from web.portal import _env


def test_a_rota_e_da_gerencia_e_dos_nichos_com_a_vista():
    rotas = {r.path for r in pp.router.routes}
    assert "/painel/prospeccao/atendimento" in rotas
    src = inspect.getsource(pp.prospeccao_atendimento)
    assert 'if not ctx["gerencia"]' in src
    assert "_perfil_atendimento(pool" in src
    assert "at.PERFIS" in inspect.getsource(pp._perfil_atendimento)


def _miolo():
    tpl = pp._ATENDIMENTO_TPL
    ini = tpl.index("{%- set _q")
    fim = tpl.index("</p>", tpl.index("Ninguém arrasta card aqui")) + len("</p>")
    return tpl[ini:fim]


def _card(**kw):
    x = {"id": 1, "ia": False, "quem": "Jacqueline", "nome": "Renata", "chip_nome": "CP Zarb",
         "etapa": "chegou", "tempo": "esperando 18 min", "lento": True}
    x.update(kw)
    return x


def _d(perfil="eventos"):
    rot = at.rotulos(perfil)
    cols = []
    for e in at.ETAPAS + ("parou",):
        cards = []
        if e == "chegou":
            cards = [_card(), _card(id=2, ia=True, quem="Zaq", nome="Bruno", chip_nome="CP Thiago",
                                    tempo="esperando 1 min", lento=False)]
        if e == "parou":
            cards = [_card(id=3, ia=True, quem="Zaq", nome="Iara", etapa="respondido")]
        cols.append({"chave": e, "titulo": rot[e][0], "sub": rot[e][1], "n": len(cards), "cards": cards})
    return {"periodo": "mes", "periodo_rot": "setembro", "periodos": at.PERIODOS, "quem": "",
            "chip": "", "chips": [(34, "CP Zarb"), (36, "CP Thiago")], "total": 3,
            "regua": [{"quem": "Zaq", "ia": True, "chegou": 2, "respondido": 2, "respondido_pct": 100,
                       "qualificado": 1, "qualificado_pct": 50, "ofertada": 0, "ofertada_pct": 0,
                       "marcada": 0, "marcada_pct": 0, "resp_min": 0.6},
                      {"quem": "equipe", "ia": False, "chegou": 1, "respondido": 0, "respondido_pct": 0,
                       "qualificado": 0, "qualificado_pct": 0, "ofertada": 0, "ofertada_pct": 0,
                       "marcada": 0, "marcada_pct": 0, "resp_min": None}],
            "colunas": cols, "tem_ia": True, "perfil": perfil, "rot_parou": rot}


def _render(d, tem_desafio=True):
    return _env.from_string(_miolo()).render(d=d, tem_desafio=tem_desafio,
                                              voc={"cliente": "cliente", "lead": "lead"})


def test_a_tela_tem_a_regua_as_colunas_e_quem_atende():
    html = _render(_d())
    assert 'class="on" href="/painel/prospeccao/atendimento"' in html      # a chave
    assert "3 chegaram · setembro" in html
    assert "🤖 Zaq" in html and "👤 equipe" in html and "0,6 min" in html
    for t in ("Chegou", "Respondido", "Qualificado", "Visita ofertada", "Visita marcada", "Parou"):
        assert t in html, t
    assert "👤 Jacqueline" in html and "esperando 18 min" in html
    assert "parou em respondido" in html
    assert "ver o Desafio completo" in html
    assert 'href="?quem=ia&amp;periodo=mes"' in html and "CP Thiago" in html


def test_sem_desafio_nao_ha_link_e_recorrente_fala_de_reuniao():
    html = _render(_d("recorrente"), tem_desafio=False)
    assert "Desafio" not in html
    assert "Reunião ofertada" in html and "segmento e porte" in html
    assert "Visita" not in html and "festa" not in html.lower()


def test_o_quadro_de_vendas_ganha_a_chave_e_a_pilula():
    tpl = pp._KANBAN_TPL
    assert '{% if atend_ok %}<div class="vseg kbvista"' in tpl
    assert 'href="/painel/prospeccao/atendimento">Atendimento</a>' in tpl
    assert "{% if tri_barra and tri_barra.ia.nome %}<div class=\"vseg kbquem\"" in tpl
    src = inspect.getsource(pp.prospeccao_kanban)
    assert 'quem_urls = {"": _kb_url(trilha=""), "ia": _kb_url(trilha="ia"), "vend": _kb_url(trilha="vend")}' in src
    assert 'atend_ok = bool(ctx["gerencia"] and _perfil_atendimento(pool, conta_id))' in src


def test_o_desafio_na_barra_de_relatorios():
    from web import portal
    assert '{% if tem_desafio %}<a class="aba" href="/painel/prospeccao/desafio-ia"' in inspect.getsource(portal)
    assert "tem_desafio=_tem_desafio(request, conta[0])" in inspect.getsource(prl.painel_relatorios)
    src = inspect.getsource(prl._tem_desafio)
    assert '("dono", "gestor")' in src, "o Desafio é da gerência, como a tela dele"


def test_os_templates_compilam():
    _env.parse(pp._ATENDIMENTO_TPL)
    _env.parse(pp._KANBAN_TPL)
