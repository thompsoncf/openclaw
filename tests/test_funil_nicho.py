"""O funil enxuto, PARTE 3: os ajustes por nicho (§6). Mockup aprovado em 24/09/2026
(docs/mockups/prospeccao_layout.html, "Na ZAQ, que não vende festa"); as partes 1
e 2 estão em test_funil_enxuto.py e test_funil_topo.py.

Medido na produção em 25/09/2026, só leitura:
  * Prime (34): 472 das 485 conversas de lead no chip PRINCIPAL — o "📱 CP Zarb"
    aparecia em quase todo card, e o selo existe pra apontar o OUTRO número;
  * ZAQ (3): 33 leads com conversa de WhatsApp e o campo "whatsapp" vazio — o card
    não tinha o 💬, e a conversa só se achava pela Comunicação.

O que este teste protege:
  * o selo do chip só aparece quando a conversa é num chip que NÃO é o principal;
  * o canal acende pela CONVERSA, não pelo campo do cadastro;
  * a pergunta da data da festa só chega na página de quem vende data;
  * os grupos da coluna não cobram "Sem data" de quem não vende data.
"""
from datetime import datetime, timedelta, timezone

from finance import evento_lead as evl
from tests.test_kanban_chat import (CONTA, _alvo, _campanha, _conversa,  # noqa: F401
                                    _kanban_html, _lead, _trecho_card, pool)


def _dois_chips(pool, rotulo_principal="CP Zarb"):
    with pool.connection() as c:
        chip2 = c.execute("insert into contas (chip_de, nome) values (%s,'CP Thiago') returning id",
                          (CONTA,)).fetchone()[0]
        c.execute("insert into canais_config (conta_id, canal, rotulo) values (%s,'whatsapp',%s)",
                  (CONTA, rotulo_principal))
        c.commit()
    return chip2


# ------------------------------------------------------------------ o selo do chip
def test_conversa_no_chip_principal_nao_ganha_selo(monkeypatch, pool):
    _dois_chips(pool)
    lid = _lead(pool, empresa="Nayra Cruz")
    _alvo(pool, _campanha(pool), lid)
    _conversa(pool, lid, "whatsapp")                    # chip_id nulo = o principal
    trecho = _trecho_card(_kanban_html(monkeypatch, pool), "Nayra Cruz")
    assert "📣 Black Friday Padarias" in trecho, "a campanha continua no card"
    assert "📱" not in trecho and "CP Zarb" not in trecho, (
        "o selo do chip principal voltou — na Prime ele aparecia em 472 de 485 cards")


def test_conversa_no_outro_chip_continua_com_selo(monkeypatch, pool):
    chip2 = _dois_chips(pool)
    lid = _lead(pool, empresa="Aliny Carvalho")
    with pool.connection() as c:
        c.execute("insert into conversas (conta_id, prospeccao_id, canal, chip_id, ultima_msg_em) "
                  "values (%s,%s,'whatsapp',%s,now())", (CONTA, lid, chip2))
        c.commit()
    assert "📱 CP Thiago" in _trecho_card(_kanban_html(monkeypatch, pool), "Aliny Carvalho")


# ------------------------------------------------------------------ o canal pela conversa
def test_o_canal_acende_pela_conversa_mesmo_sem_o_campo(monkeypatch, pool):
    """O caso da ZAQ: conversa de verdade, campo "whatsapp" do cadastro vazio."""
    zap = _lead(pool, empresa="Evolui Suplementos")
    cz = _conversa(pool, zap, "whatsapp")
    mail = _lead(pool, empresa="Dra Gabriela")
    cm = _conversa(pool, mail, "email")
    html = _kanban_html(monkeypatch, pool)
    assert f"kbAbrirChat(event,{cz},'conversas',this)\" title=\"Abrir a conversa de WhatsApp\">💬" in \
        _trecho_card(html, "Evolui Suplementos")
    assert f"kbAbrirChat(event,{cm},'emails',this)\" title=\"Abrir a conversa de e-mail\">✉️" in \
        _trecho_card(html, "Dra Gabriela")


def test_so_o_campo_sem_conversa_continua_selo_apagado(monkeypatch, pool):
    _lead(pool, empresa="Pet Shop Parnaíba", whatsapp="86999990000")
    trecho = _trecho_card(_kanban_html(monkeypatch, pool), "Pet Shop Parnaíba")
    assert '<span title="WhatsApp">💬</span>' in trecho and "kbAbrirChat" not in trecho.split("kbch")[1]


# ------------------------------------------------------------------ a pergunta da data
def test_a_pergunta_da_data_so_vai_pra_quem_vende_data(monkeypatch, pool):
    html = _kanban_html(monkeypatch, pool)            # conta sem nicho de eventos
    assert "KB_PERGUNTA_DATA" not in html and "function kbPerguntarData" not in html
    assert evl.PERGUNTA_DATA not in html


def test_quem_vende_data_continua_com_a_pergunta(monkeypatch, pool):
    import finance.vendas as v
    monkeypatch.setattr(v, "vende_data", lambda pool, conta_id: True)
    html = _kanban_html(monkeypatch, pool)
    assert "function kbPerguntarData" in html
    assert "var KB_PERGUNTA_DATA=" in html


# ------------------------------------------------------------------ os grupos da coluna
def _cards_de_entrada():
    agora = datetime(2026, 9, 25, 12, tzinfo=timezone.utc)
    # atividade recente em todos: ninguém cai na dobra dos parados (outro grupo)
    cards = [{"id": i, "criado_em": agora - timedelta(days=d), "ult_em": agora - timedelta(days=1)}
             for i, d in enumerate((2, 3, 40, 41))]
    return cards, agora


def test_quem_nao_vende_data_nao_ve_sem_data_nos_grupos():
    cards, agora = _cards_de_entrada()
    grupos = evl.agrupar(cards, agora, vende_data=False)
    rotulos = [g["rotulo"] for g in grupos]
    assert all("Sem data" not in r for r in rotulos), rotulos
    assert rotulos == ["Entrou em set", "Entrou em ago"]
    por_semana = evl.agrupar(cards[:2], agora, por_semana=True, vende_data=False)
    assert por_semana[0]["rotulo"] in ("", "Entrou na semana de 21/09")


def test_quem_vende_data_continua_vendo_sem_data():
    cards, agora = _cards_de_entrada()
    rotulos = [g["rotulo"] for g in evl.agrupar(cards, agora)]
    assert rotulos == ["Sem data · entrou em set", "Sem data · entrou em ago"]


def test_a_mesma_chave_e_a_mesma_ordem_nos_dois_perfis():
    cards, agora = _cards_de_entrada()
    a = evl.agrupar(cards, agora, vende_data=True)
    b = evl.agrupar(cards, agora, vende_data=False)
    assert [(g["tipo"], g["chave"], g["n"]) for g in a] == [(g["tipo"], g["chave"], g["n"]) for g in b]


def test_a_rota_passa_o_perfil_pro_agrupar():
    import inspect
    from web import painel_prospeccao as pp
    fonte = inspect.getsource(pp.prospeccao_kanban)
    assert "vende_data=modo_evento" in fonte
    assert "pergunta_data=(_evl.PERGUNTA_DATA if modo_evento else \"\")" in fonte
