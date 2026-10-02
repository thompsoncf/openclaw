"""A planta dos stands (web/loja_stands.PLANTA_DEFS_JS) — posições MEDIDAS na planta
oficial do evento (PDF v2, págs. 10–12; 01/10/2026).

O dono viu stand fora do lugar na transcrição em grade (S143–S154, S127–S132). Agora
cada stand tem o seu retângulo; estes testes seguram o que não pode regredir: os 162
stands estão na planta, cada um uma vez, nenhum em cima do outro, e as 3 telas
(página pública, painel, app) desenham com a MESMA função.

Sem banco: só lê as constantes.
"""
import json
import re

from web.loja_stands import PLANTA_CSS, PLANTA_DEFS_JS


def _planta():
    """{pavilhão: {"h": altura, "stands": {código: [x, y, w, h]}, "decor": [...]}}"""
    out = {}
    for pav, h, stands, decor in re.findall(
            r"^\s*(\w+):\{w:1000,h:([\d.]+),\s*stands:(\{.*?\}),\s*decor:(\[.*?\])\},?\s*$",
            PLANTA_DEFS_JS, re.M | re.S):
        out[pav] = {"h": float(h), "stands": json.loads(stands), "decor": json.loads(decor)}
    return out


def _esperados():
    return {
        "inferior": [f"i{n:02d}" for n in range(1, 43)] + [f"G{n}" for n in range(43, 75)],
        "superior": [f"S{n}" for n in range(75, 155)],
        "outlet_car": [f"C{n}" for n in range(155, 163)],
    }


def test_a_planta_tem_os_162_stands_cada_um_no_seu_pavilhao():
    planta = _planta()
    assert set(planta) == {"inferior", "superior", "outlet_car"}
    for pav, codigos in _esperados().items():
        assert sorted(planta[pav]["stands"]) == sorted(codigos), pav
    assert sum(len(p["stands"]) for p in planta.values()) == 162


def test_nenhum_stand_fica_em_cima_de_outro_nem_fora_da_planta():
    for pav, p in _planta().items():
        ret = p["stands"]
        for cod, (x, y, w, h) in ret.items():
            assert w > 0 and h > 0, cod
            assert x >= 0 and y >= 0 and x + w <= 1000 and y + h <= p["h"], (pav, cod)
        cods = sorted(ret)
        for i, a in enumerate(cods):
            ax, ay, aw, ah = ret[a]
            for b in cods[i + 1:]:
                bx, by, bw, bh = ret[b]
                # vizinhos dividem a aresta; 0,2 de folga é o arredondamento (1 casa)
                sobre_x = min(ax + aw, bx + bw) - max(ax, bx)
                sobre_y = min(ay + ah, by + bh) - max(ay, by)
                assert not (sobre_x > 0.2 and sobre_y > 0.2), (pav, a, b)


def test_os_blocos_que_estavam_deslocados_ficam_onde_a_planta_poe():
    s = _planta()["superior"]["stands"]

    def dir_(c):
        return s[c][0] + s[c][2]

    # S130–S132 fica embaixo de S108–S110 (não do bloco inteiro S107–S110)
    assert abs(s["S130"][0] - s["S108"][0]) < 1 and abs(dir_("S132") - dir_("S110")) < 1
    # S133–S136 exatamente embaixo de S123–S126
    assert abs(s["S133"][0] - s["S123"][0]) < 1 and abs(dir_("S136") - dir_("S126")) < 1
    # S143–S150 começa à direita do S107 e passa do S110; S151–S154 vai até depois da coluna S137–S142
    assert s["S107"][0] < s["S143"][0] < s["S108"][0] + s["S108"][2]
    assert dir_("S150") > dir_("S110")
    assert dir_("S154") > dir_("S142")
    # S127–S129 nasce embaixo do S105/S106 e termina no corredor, antes do S107
    assert s["S105"][0] < s["S127"][0] < dir_("S106") < dir_("S129") < s["S107"][0]
    # S75 começa em cima do S103; a coluna S137–S142 nasce no canto do S96
    assert abs(s["S75"][0] - s["S103"][0]) < 1
    assert abs(s["S137"][0] - dir_("S96")) < 1 and abs(s["S137"][1] - (s["S96"][1] + s["S96"][3])) < 1

    i = _planta()["inferior"]["stands"]
    # G67/G68 deitados entre i41 e G69; i42 embaixo do i40 (o "L")
    assert i["G67"][2] > i["G67"][3] and i["i41"][0] < i["G67"][0] < i["G69"][0]
    assert abs(i["i42"][0] - i["i40"][0]) < 1 and i["i42"][1] > i["i40"][1]


def test_a_praca_de_alimentacao_e_os_carros_estao_no_outlet_car():
    decor = _planta()["outlet_car"]["decor"]
    icones = [d["ic"] for d in decor if d.get("ic")]
    assert icones.count("mesa") == 7 and icones.count("barraca") == 7      # tenda 20x10
    assert icones.count("carro") == 8 and icones.count("moto") == 6        # um carro por espaço
    assert "palco" in icones
    assert any(d["k"] == "tenda" for d in decor)
    textos = {d.get("t") for d in decor}
    assert {"Praça de Alimentação", "Tenda 20x10", "Outlet Car", "Outlet Chic", "Entrada"} <= textos
    # todo ícone usado existe no desenho (plantaIcone)
    for nome in set(icones) | {d["ic"] for p in _planta().values() for d in p["decor"] if d.get("ic")}:
        assert f"if (nome === '{nome}')" in PLANTA_DEFS_JS, nome


def test_a_planta_cabe_dentro_de_um_template_jinja():
    # o painel injeta estas constantes DENTRO de um template (sem {% raw %}):
    # abre-chaves de Jinja no meio do JS/CSS quebraria a página do gestor
    for nome, texto in (("PLANTA_DEFS_JS", PLANTA_DEFS_JS), ("PLANTA_CSS", PLANTA_CSS)):
        for marca in ("{{", "{%", "{#"):
            assert marca not in texto, (nome, marca)


def test_as_tres_telas_desenham_com_a_mesma_funcao():
    import web.loja_stands as pub
    import web.painel_cockpit as app
    import web.painel_eventos_stands as painel

    assert "function plantaMontar(" in PLANTA_DEFS_JS
    assert "plantaMontar(grid, pav," in pub._TPL
    assert "plantaMontar(grid, pav," in painel._TPL
    assert "plantaMontar(grid,p," in app._STANDS_JS
    # o cenário (paredes, rótulos, ícones) também é um só
    assert ".plc-faixa" in PLANTA_CSS and ".plc-faixa" in pub._TPL and ".plc-faixa" in painel._TPL
    # e ninguém voltou a desenhar em grade de 24 colunas
    for fonte in (pub._TPL, painel._TPL, app._STANDS_JS):
        assert "repeat(24" not in fonte and "gridTemplateColumns" not in fonte


def test_no_mapa_3d_o_clique_chega_no_stand():
    # 01/10/2026: com cada stand virando filho direto do chão (translateZ próprio), a
    # moldura do zoom (.floor-zoom, preserve-3d) passou a ganhar o clique no Mapa 3D —
    # no computador nenhum stand selecionava. A moldura não recebe ponteiro; o chão sim.
    import re as _re

    import web.loja_stands as pub

    zoom = _re.search(r"\.floor-zoom\{[^}]*\}", pub._TPL).group(0)
    chao = _re.search(r"\.floor-grid\{[^}]*\}", pub._TPL).group(0)
    assert "pointer-events:none" in zoom
    assert "pointer-events:auto" in chao
    # o cenário nunca rouba o toque do stand
    assert "pointer-events:none" in _re.search(r"\.plc\{[^}]*\}", PLANTA_CSS).group(0)

