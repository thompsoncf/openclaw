"""O funil enxuto, PARTE 2: o topo. Pedido do dono em 24/09/2026, mockup aprovado em
docs/mockups/prospeccao_layout.html ("o topo em três faixas curtas"). A parte 1
(o cartão e as colunas) está em test_funil_enxuto.py.

O que este teste protege:

  * **a régua "Festa em"** (uma barra por mês, com o ano como divisa) só existe em
    conta que vende data — quem vende mensalidade não vê a palavra festa (§6);
  * **a busca** acha por nome sem acento e por telefone, e o Enter procura em
    todos os meses SEM trocar o período guardado de quem está olhando;
  * **o filtro de vendedor** saiu do SQL, e por isso o seletor sabe quantos cards
    cada um tem — mas o VENDEDOR continua vendo só os leads dele (é permissão);
  * **os críticos por vendedor** usam o MESMO estado do selo do card e só
    aparecem pra gerência;
  * **o mês quase vazio** explica o quadro vazio e leva pro último lote;
  * **Captar e Etapas abrem em gaveta**, e o reload de 60 s respeita as duas e a
    busca digitada;
  * **no celular** as abas de etapa dizem quantos esperam resposta (●N).
"""
import inspect
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from starlette.datastructures import QueryParams

from finance import evento_lead as evl
from tests.test_funil_por_mes import (CONTA, KEY_LONGE, KEY_PERTO, MES_LONGE,  # noqa: F401
                                      MES_PERTO, _lead, pool, vende_data)
from web import painel_prospeccao as pp

TPL = pp._KANBAN_TPL
FONTE = inspect.getsource(pp)


def _html(monkeypatch, pool, *, gerencia=True, membro_id=1, req=None, **kw) -> str:
    monkeypatch.setattr(pp, "get_pool", lambda: pool)
    monkeypatch.setattr(pp, "_acesso", lambda r: (
        {"conta_id": CONTA, "membro_id": membro_id, "gerencia": gerencia,
         "pode_atribuir": gerencia}, None))
    req = req or SimpleNamespace(session={}, query_params=QueryParams(""))
    kw.setdefault("entrou", "tudo")
    r = pp.prospeccao_kanban(req, **{"vendedor": "", "mes": "", "vista": "", "fora": None, **kw})
    assert r.status_code == 200
    return bytes(r.body).decode("utf-8")


def _vendedor(pool, nome) -> int:
    with pool.connection() as c:
        vid = c.execute("insert into membros (conta_id, nome, papel) values (%s,%s,'vendedor') returning id",
                        (CONTA, nome)).fetchone()[0]
        c.commit()
    return vid


def _de(pool, lead_id, vendedor_id=None, whatsapp=None):
    with pool.connection() as c:
        c.execute("update prospeccao set vendedor_id=coalesce(%s, vendedor_id), "
                  "whatsapp=coalesce(%s, whatsapp) where id=%s", (vendedor_id, whatsapp, lead_id))
        c.commit()


# ------------------------------------------------------------------ a régua
def test_a_regua_e_proporcional_e_poe_o_ano_como_divisa():
    hoje = date(2026, 9, 25)
    cont = {"2026-11": 14, "2026-12": 7, "2027-01": 2, None: 30}
    itens = evl.regua(cont, "", hoje)
    # mesmos itens, mesma ordem e mesmas chaves do trilho: só ganha o desenho
    assert [i["chave"] for i in itens] == [i["chave"] for i in evl.trilho(cont, "")]
    todos, nov, dez, jan, sem = itens
    assert todos["h"] == evl.REGUA_ALTURA and todos["todos"]
    assert nov["h"] == evl.REGUA_ALTURA, "o mês mais cheio é a barra inteira"
    assert dez["h"] == round(evl.REGUA_ALTURA * 7 / 14)
    assert jan["h"] >= 2, "mês com lead nunca some"
    assert (nov["rotulo"], nov["titulo"]) == ("Nov", "Nov 26")
    assert nov["ano"] == "" and dez["ano"] == "", "o ano corrente não ganha divisa"
    assert jan["ano"] == "2027", "a virada do ano é a divisa — 'Jan' sozinho seria ambíguo"
    assert sem["sem"] and sem["h"] == round(evl.REGUA_ALTURA * 30 / 53)


def test_a_regua_so_em_quem_vende_data(monkeypatch, pool, vende_data):
    _lead(pool, "Com Data", evento_em=MES_LONGE)
    html = _html(monkeypatch, pool)
    regua = html.split('id="trilho"')[1].split("</div>")[0]
    assert 'class="regua"' in html and "Festa em" in regua
    assert "<i style=\"height:" in regua
    assert f'href="/painel/prospeccao?mes={KEY_LONGE}"' in regua


def test_conta_que_nao_vende_data_nao_ve_festa(monkeypatch, pool):
    _lead(pool, "Com Data", evento_em=MES_LONGE)
    html = _html(monkeypatch, pool)
    assert 'id="trilho"' not in html and "Festa em" not in html
    assert "festa em 30 dias" not in html


# ------------------------------------------------------------------ a busca
def test_a_busca_acha_pelo_nome_sem_acento_e_pelo_telefone(monkeypatch, pool):
    g = _lead(pool, "Geórgia Lima")
    b = _lead(pool, "Bruno Teles")
    _de(pool, b, whatsapp="(86) 99912-3456")
    html = _html(monkeypatch, pool, q="georgia")
    assert f'data-id="{g}"' in html and f'data-id="{b}"' not in html
    html = _html(monkeypatch, pool, q="9912-34")
    assert f'data-id="{b}"' in html and f'data-id="{g}"' not in html
    # menos de 4 dígitos não é telefone: "12" não pode achar meio funil
    html = _html(monkeypatch, pool, q="12")
    assert f'data-id="{b}"' not in html
    assert f'data-tel="86999123456"' in _html(monkeypatch, pool), "o JS busca pelo data-tel"


def test_o_enter_procura_em_todos_os_meses_sem_trocar_o_periodo(monkeypatch, pool):
    velho = _lead(pool, "Do Mês Passado", criado_em=datetime.now(timezone.utc) - timedelta(days=40))
    req = SimpleNamespace(session={}, query_params=QueryParams(""))
    html = _html(monkeypatch, pool, entrou="", q="passado", req=req)
    assert f'data-id="{velho}"' in html, "a busca vai em todos os meses"
    assert "Buscando <b>“passado”</b> em todos os meses" in html
    assert "funil_entrou" not in req.session, "uma busca não muda o período guardado"
    assert f'data-id="{velho}"' not in _html(monkeypatch, pool, entrou="", req=req)


def test_a_busca_no_js_usa_a_mesma_regra_do_servidor():
    js = FONTE.split("function kbBuscaFiltra()")[1][:900]
    assert "_kbNorm(" in js and "dg.length>=4" in js and "data-tel" in js
    assert "normalize('NFD')" in FONTE
    assert "function kbBuscaTecla(ev)" in FONTE and "'q='+encodeURIComponent(v)" in FONTE
    assert "e.key!=='/'" in FONTE, "o atalho / pra buscar"


# ------------------------------------------------------------------ vendedor e críticos
def test_o_seletor_de_vendedor_diz_quantos_cada_um_tem(monkeypatch, pool):
    ja, th = _vendedor(pool, "Jacqueline Prime"), _vendedor(pool, "Thiago Pinheiro")
    for n in range(2):
        _de(pool, _lead(pool, f"J{n}"), ja)
    t = _lead(pool, "T0")
    _de(pool, t, th)
    _lead(pool, "Livre")
    html = _html(monkeypatch, pool, vendedor=str(ja))
    assert f'data-id="{t}"' not in html, "o filtro continua filtrando o quadro"
    sel = html.split('class="vendf"')[1].split("</select>")[0]
    assert "Todos · 4" in sel
    assert "Jacqueline Prime · 2" in sel and "Thiago Pinheiro · 1" in sel
    assert "Sem responsável · 1" in sel


def test_o_vendedor_continua_vendo_so_os_leads_dele(monkeypatch, pool):
    ja, th = _vendedor(pool, "Jacqueline Prime"), _vendedor(pool, "Thiago Pinheiro")
    j = _lead(pool, "Da Jacqueline")
    _de(pool, j, ja)
    t = _lead(pool, "Do Thiago")
    _de(pool, t, th)
    html = _html(monkeypatch, pool, gerencia=False, membro_id=ja, vendedor=str(th))
    assert f'data-id="{j}"' in html and f'data-id="{t}"' not in html
    assert 'class="vendf"' not in html and 'id="kbcrit"' not in html


def _com_follow_up(monkeypatch, estados: dict):
    """Liga o selo do follow-up com os estados dados ({lead_id: estado})."""
    from finance import follow_up as fu
    import web.portal as portal
    monkeypatch.setattr(pp, "_tem_follow_up", lambda conta: True)
    monkeypatch.setattr(portal, "nicho_da_conta", lambda conta: "eventos")
    monkeypatch.setattr(fu, "config", lambda c, conta_id: {"follow_up_modo": "ligado"})
    monkeypatch.setattr(fu, "leads", lambda c, conta_id, pf, cfg=None: [
        {"id": i, "estado": e, "atraso_h": 100, "acao": ""} for i, e in estados.items()])


def test_os_criticos_por_vendedor_saem_do_mesmo_estado_do_card(monkeypatch, pool):
    ja, th = _vendedor(pool, "Jacqueline Prime"), _vendedor(pool, "Thiago Pinheiro")
    leads = {}
    for n in range(3):
        lid = _lead(pool, f"J{n}")
        _de(pool, lid, ja)
        leads[lid] = "critico"
    t = _lead(pool, "T0")
    _de(pool, t, th)
    leads[t] = "critico"
    leads[_lead(pool, "Atrasado")] = "atrasado"          # não é crítico
    perd = _lead(pool, "Perdido", status="perdido")
    _de(pool, perd, th)
    leads[perd] = "critico"                                # perdido não se cobra
    _com_follow_up(monkeypatch, leads)
    # o selo do follow-up só liga com a linha da conta no ctx (ver `_conta_row`)
    req = SimpleNamespace(session={}, query_params=QueryParams(""))
    monkeypatch.setattr(pp, "get_pool", lambda: pool)
    monkeypatch.setattr(pp, "_acesso", lambda r: (
        {"conta_id": CONTA, "membro_id": 1, "gerencia": True, "pode_atribuir": True,
         "conta": ("x",)}, None))
    r = pp.prospeccao_kanban(req, vendedor=str(th), mes="", vista="", entrou="tudo", fora=None)
    html = bytes(r.body).decode("utf-8")
    linha = html.split('id="kbcrit"')[1].split("</div>")[0]
    # todos os vendedores, mesmo com um só escolhido, do maior pro menor
    assert linha.index("<b>3</b> Jacqueline") < linha.index("<b>1</b> Thiago")
    assert f'href="/painel/prospeccao?vendedor={ja}"' in linha
    assert 'class="on"' in linha.split("Thiago")[0].rsplit("<a", 1)[1], "o escolhido acende"
    assert 'href="/painel/follow-up"' in linha
    assert "Crítico" in html, "o selo do card é o mesmo estado"


def test_sem_follow_up_nao_tem_linha_de_criticos(monkeypatch, pool):
    _lead(pool, "Qualquer")
    assert 'id="kbcrit"' not in _html(monkeypatch, pool)


# ------------------------------------------------------------------ o mês quase vazio
def test_mes_quase_vazio_explica_e_leva_pro_ultimo_lote(monkeypatch, pool):
    _lead(pool, "Deste Mês")
    for n in range(12):
        _lead(pool, f"Lote {n}", criado_em=datetime.now(timezone.utc) - timedelta(days=35))
    html = _html(monkeypatch, pool, entrou="")
    nota = html.split('id="kbnota"')[1].split("</div>")[0]
    assert "tem 1 lead." in nota and "(12)" in nota
    assert "?entrou=" in nota and "Ver tudo (13)" in nota
    # com o mês de verdade cheio, nada de aviso
    for n in range(4):
        _lead(pool, f"Mais {n}")
    assert 'id="kbnota"' not in _html(monkeypatch, pool, entrou="")


# ------------------------------------------------------------------ gavetas, reload, celular
def test_captar_e_etapas_abrem_em_gaveta():
    assert 'id="captar" class="fsec kbgav"' in TPL
    assert '<details class="etcfg" id="etcfg">' in TPL, "continua <details>: o freio olha o [open]"
    assert 'onclick="etAbre()"' in TPL and 'onclick="capToggle()"' in TPL
    assert "function kbGavFecha()" in FONTE and 'id="kbgav-fundo"' in TPL
    assert "scrollIntoView" not in FONTE.split("function capToggle()")[1][:400]


def test_o_reload_nao_apaga_a_busca_digitada():
    ciclo = FONTE.split("setInterval(function(){\n  if(document.hidden) return;")[1][:1400]
    assert "kbb.value.trim()!==KB_BUSCA" in ciclo
    assert ciclo.index("KB_BUSCA) return;") < ciclo.index("location.reload();")
    assert "cap.style.display !== 'none'" in ciclo and ".etcfg[open]" in ciclo


def test_no_celular_a_aba_diz_quantos_esperam_resposta():
    abas = TPL.split('<div class="kbtabs" id="kbtabs">')[1].split("</div>")[0]
    assert "selectattr('tipo', 'equalto', 'esperando')" in abas and '<i class="e"' in abas
    assert "●{{ _tesp }}" in abas
    recontar = FONTE.split("function kbRecontar()")[1][:1400]
    assert ".kbcard[data-esp]" in recontar, "o ●N anda junto quando um card muda de coluna"
    assert ".kbtabs{position:sticky;top:0" in TPL


def test_o_titulo_numa_linha_so():
    tit = TPL.split('<div class="kbtit')[1].split("{% if busca %}<div class=\"kbbusca-faixa\"")[0]
    for peca in ('id="kbbusca"', 'class="vseg"', "etAbre()", 'class="cap-btn"', 'id="kb-total-n"'):
        assert peca in tit, peca
    assert "Editar etapas do funil" not in tit


def test_a_busca_volta_escapada_na_pagina(monkeypatch, pool):
    """O template do funil roda SEM autoescape (o `select_autoescape()` do portal
    decide pela extensão, e "prospeccao" não tem): o `q` da URL voltaria cru, e um
    link com <script> no `q` rodaria na sessão de quem clicasse."""
    _lead(pool, "Alguém")
    html = _html(monkeypatch, pool, q='"><script>alert(1)</script>')
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
