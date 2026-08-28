"""A foto também aparece no Cockpit — que é a tela que mais importa pra isso.

O QUE ACONTECEU (28/08/2026)
Os passos 1 a 3 subiram e a foto passou a aparecer no painel de Comunicação, no
desktop: `GET /painel/prospeccao/midia/139779` respondeu 200 com 145.063 bytes em
739 ms. Mas o dono abriu primeiro o COCKPIT — o app do vendedor no celular — e lá
viu só `📷 Foto`, texto puro.

Foi falha de escopo minha. O Cockpit é outra tela, com outro renderizador
(`d.innerHTML = rot(m.who) + txt(m.texto)`), e eu não tinha olhado. E é justamente
a tela que sustenta o objetivo do trabalho todo: tirar o vendedor do WhatsApp
pessoal. Foto que não aparece no celular dele é exatamente o motivo de ele voltar
pro aparelho.

O PORTÃO AQUI É OUTRO, e é a parte séria
No painel de prospecção quem autoriza é o `_acesso` (sessão do painel, papel com
'vendas'). No Cockpit é o `lead_do_vendedor`, que revalida a posse do lead. São
dois mundos de permissão diferentes — por isso a rota de mídia do Cockpit é
própria, e não um atalho pra do outro lado.

O id da mensagem é sequencial e adivinhável. O que impede um vendedor de ler a
foto do cliente de outro é a combinação: a mensagem tem que pertencer à conversa
DAQUELE lead, e o lead tem que ser DAQUELE vendedor.
"""
import inspect

from finance import cockpit as ck
from web import painel_cockpit as pc


# --------------------------------------------------------------- o portão

def test_a_rota_de_midia_existe_e_e_do_cockpit():
    rotas = [r.path for r in pc.router.routes if "midia" in r.path]
    assert rotas == ["/cockpit/lead/{lead_id}/midia/{mensagem_id}"], \
        "a mídia do Cockpit não pode depender da rota do painel: os portões são outros"


def test_a_mensagem_tem_que_ser_da_conversa_daquele_lead():
    """Sem o `cv.prospeccao_id=%s`, o id da mensagem — sequencial e adivinhável —
    seria a única coisa entre um vendedor e a foto do cliente de outro."""
    fonte = inspect.getsource(pc.cockpit_midia)
    assert "cv.prospeccao_id=%s" in fonte
    assert "cv.conta_id=%s" in fonte


def test_e_o_lead_tem_que_ser_daquele_vendedor():
    """A consulta diz que a mensagem é daquele lead; o `lead_do_vendedor` diz que o
    lead é daquele vendedor. As duas coisas, não uma."""
    fonte = inspect.getsource(pc.cockpit_midia)
    assert "ck.lead_do_vendedor(" in fonte
    assert "_sessao(request)" in fonte


def test_sem_sessao_nao_passa():
    fonte = inspect.getsource(pc.cockpit_midia)
    assert "status_code=401" in fonte


def test_expirado_e_falha_continuam_separados():
    fonte = inspect.getsource(pc.cockpit_midia)
    assert "except _wm.Expirou" in fonte and "status_code=410" in fonte
    assert "status_code=502" in fonte


# ------------------------------------------------------ o ponteiro fica no servidor

def test_o_cockpit_manda_tipo_e_tamanho_mas_nunca_o_ponteiro():
    fonte = inspect.getsource(ck.lead_do_vendedor)
    assert 'item["midia"] = {"tipo": midia_tipo, **(midia_meta or {})}' in fonte
    depois = fonte.split('item["midia"]')[1][:250]
    assert "directPath" not in depois and "mediaKey" not in depois


def test_o_polling_repassa_a_midia():
    """Sem isto, a foto que chega com a tela aberta só apareceria ao recarregar."""
    fonte = inspect.getsource(pc.cockpit_lead_mensagens)
    assert '"midia": m["midia"]' in fonte


# ------------------------------------------------- as duas cópias da bolha

def test_a_bolha_existe_nas_duas_cargas():
    """O Cockpit desenha a conversa DUAS vezes: no HTML da primeira carga e no JS do
    polling. Uma cópia só faz a foto aparecer ao abrir e sumir ao chegar (ou o
    contrário) — e ninguém entende por quê."""
    fonte = inspect.getsource(pc)
    assert "_midia_html(lead_id, m)" in fonte, "primeira carga (servidor)"
    assert "rot(m.who)+mid(m)+txt(m.texto)" in fonte, "polling (JS)"


def test_as_duas_copias_carregam_do_mesmo_jeito():
    """`loading=lazy` e `preload=none` são o que faz o custo ser zero pra foto que
    ninguém abre. Valer só numa das cópias é o mesmo que não valer."""
    fonte = inspect.getsource(pc)
    servidor = inspect.getsource(pc._midia_html)
    assert "loading=lazy" in servidor and "preload=none" in servidor
    i = fonte.index("function mid(m)")
    js = fonte[i:i + 1200]
    assert "loading=lazy" in js and "preload=none" in js


def test_os_quatro_tipos_nas_duas_copias():
    servidor = inspect.getsource(pc._midia_html)
    fonte = inspect.getsource(pc)
    js = fonte[fonte.index("function mid(m)"):][:1200]
    for pedaco in ("documento", "video", "figurinha", "<img"):
        assert pedaco in servidor, f"faltou {pedaco} no servidor"
        assert pedaco in js, f"faltou {pedaco} no JS"


def test_mensagem_sem_midia_nao_ganha_nada():
    """É a esmagadora maioria das linhas do Cockpit."""
    assert pc._midia_html(760, {"id": 1, "texto": "oi"}) == ""
    assert pc._midia_html(760, {"id": 1, "midia": {}}) == ""
    assert pc._midia_html(760, {"midia": {"tipo": "imagem"}}) == "", "sem id não dá src"


# ------------------------------------------------------------- o que a bolha mostra

def test_a_foto_aponta_pra_rota_do_cockpit():
    h = pc._midia_html(760, {"id": 139779, "midia": {"tipo": "imagem"}})
    assert "/cockpit/lead/760/midia/139779" in h
    assert "<img" in h and "loading=lazy" in h


def test_o_documento_mostra_nome_e_tamanho():
    h = pc._midia_html(760, {"id": 9, "midia": {"tipo": "documento",
                                                "nome": "contrato.pdf", "bytes": 491520}})
    assert "contrato.pdf" in h and "480 KB" in h and "target=_blank" in h


def test_o_video_nao_baixa_sozinho():
    h = pc._midia_html(760, {"id": 9, "midia": {"tipo": "video", "segundos": 74}})
    assert "preload=none" in h and "autoplay" not in h


def test_tamanho_legivel():
    assert pc._tam_br(0) == "0 B"
    assert pc._tam_br(491520) == "480 KB"
    assert pc._tam_br(1572864) == "1,5 MB"


def test_nome_de_arquivo_nao_escapa_do_html():
    """O nome vem do cliente. Sem escapar, um `<img onerror=...>` no nome do arquivo
    viraria script rodando na tela do vendedor."""
    h = pc._midia_html(760, {"id": 9, "midia": {
        "tipo": "documento", "nome": '<img src=x onerror=alert(1)>'}})
    assert "<img src=x" not in h
    assert "&lt;img" in h
