"""O que o app baixa antes de conseguir pintar.

Três coisas estavam no caminho crítico de toda abertura: uma folha de estilo de
33 KB reenviada dentro de cada HTML, três fontes buscadas no Google (host de
terceiro, `<link rel=stylesheet>` bloqueia a renderização) e, no iPhone, nenhum
splash — o manifest tem `background_color`, mas o iOS ignora o manifest.
"""
from fastapi.testclient import TestClient

from web.app import app
from web import painel_cockpit as pc
from web import tema

cliente = TestClient(app)


def test_nenhuma_fonte_vem_de_terceiro():
    """Fonte no fonts.googleapis.com é um <link rel=stylesheet> cross-origin: bloqueia
    a pintura e o service worker não consegue guardar (resposta opaca)."""
    assert "googleapis" not in tema.FONTES and "gstatic" not in tema.FONTES
    assert "@font-face" in tema.FONTES
    assert tema.FONTES.count("/estatico/fontes/") == 3     # as três famílias
    assert "font-display:swap" in tema.FONTES              # texto legível no 1º quadro

    html = pc._page("x", "<i>y</i>").body.decode()
    assert "googleapis" not in html and "gstatic" not in html


def test_fontes_servidas_por_nos():
    for nome in ("bricolage", "inter", "jetbrains"):
        r = cliente.get(f"/estatico/fontes/{nome}.woff2")
        assert r.status_code == 200
        assert r.content[:4] == b"wOF2", f"{nome} não é woff2"
        assert "immutable" in r.headers.get("cache-control", "")


def test_fonte_desconhecida_nao_lê_disco():
    """O nome é validado contra uma lista, não montado por concatenação — senão
    `../../` sairia da pasta."""
    assert cliente.get("/estatico/fontes/qualquer.woff2").status_code == 404
    assert cliente.get("/estatico/fontes/..%2f..%2fapp.woff2").status_code == 404


def test_css_sai_do_html_e_vira_arquivo_versionado():
    html = pc._page("x", "<i>y</i>").body.decode()
    # a folha grande não pode mais viajar dentro do documento
    assert ":root{" not in html and ".tabs a.on" not in html
    assert f"/cockpit/app.css?v={pc._CSS_VER}" in html

    r = cliente.get("/cockpit/app.css")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/css")
    assert b".tabs a.on" in r.content and b":root{" in r.content

    # a versão sai do conteúdo: CSS novo → URL nova, sem ninguém virar número à mão
    import hashlib
    assert pc._CSS_VER == hashlib.sha1(pc._CSS_TEXTO.encode()).hexdigest()[:10]


def test_html_por_navegacao_ficou_pequeno():
    """Era 38.880 bytes, 85% deles a mesma folha repetida. O app é form + redirect:
    o vendedor paga isso a cada troca de aba."""
    html = pc._page("Meus leads", "<div class=x>conteúdo</div>").body.decode()
    assert len(html) < 12_000, f"o HTML voltou a inchar: {len(html)} bytes"


def test_splash_cobre_os_iphones_em_uso():
    html = pc._page("x", "<i>y</i>").body.decode()
    assert html.count("apple-touch-startup-image") == len(pc._SPLASH) == 8
    # o iOS casa por tamanho CSS + densidade, e só retrato
    assert "(device-width:390px) and (device-height:844px)" in html
    assert "(-webkit-device-pixel-ratio:3)" in html
    assert "(orientation:portrait)" in html


def test_splash_serve_png_e_recusa_o_resto():
    r = cliente.get("/cockpit/splash/390x844@3.png")
    assert r.status_code == 200 and r.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert cliente.get("/cockpit/splash/1x1@1.png").status_code == 404
    assert cliente.get("/cockpit/splash/..%2f..%2fapp.png").status_code == 404


def test_o_z_se_desenha_na_abertura_e_uma_vez_por_sessao():
    """O logo é um caminho contínuo, então ele se escreve na tela — dasharray, sem
    imagem e sem biblioteca. A cortina vive no HTML e não no splash do iOS: o
    `apple-touch-startup-image` é PNG (não anima) e o iPhone guarda com teimosia as
    imagens de um app já instalado, então trocá-las não chega em quem já instalou."""
    html = pc._page("x", "<i>y</i>").body.decode()

    assert "id=abertura" in html and "class=abertura id=abertura hidden" in html
    # nasce ESCONDIDA: sem JS ninguém vê cortina e o app abre direto
    assert "hidden>" in html.split("id=abertura")[1][:20]

    # trocar de aba é navegação inteira neste app — sem a trava de sessão o Z
    # tomaria a tela a cada toque na barra de abas
    assert "sessionStorage.getItem('zaqAberto')" in html
    assert "sessionStorage.setItem('zaqAberto'" in html

    # o mesmo traço serve à cortina e ao indicador
    assert html.count("M170 150 h150 L190 362 h150") == 2
    assert "class=zdraw" in html


def test_o_indicador_das_abas_nao_segura_nada():
    """O Z pequeno substitui o fio: mesma função, cara da marca. Ele roda em laço
    enquanto a tela nova não chega e some junto com o documento."""
    assert ".zprog.on .zdraw" in pc._CSS_TEXTO
    assert "infinite" in pc._CSS_TEXTO.split(".zprog.on .zdraw")[1][:120]
    # e continua respeitando quem pediu menos movimento
    assert "prefers-reduced-motion" in pc._CSS_TEXTO
    reduzido = pc._CSS_TEXTO.split("prefers-reduced-motion")[1][:400]
    assert ".zprog.on .zdraw" in reduzido and "animation:none" in reduzido


def test_service_worker_so_cacheia_o_que_e_imutavel():
    """HTML segue rede-primeiro de propósito: fila, conversa e contadores mudam a
    cada minuto, e servir tela velha do disco seria pior que esperar."""
    sw = cliente.get("/cockpit/sw.js")
    assert sw.status_code == 200
    corpo = sw.text
    assert "app\\.css" in corpo and "estatico\\/fontes\\/" in corpo
    assert "splash" in corpo and "icon\\.svg" in corpo
    assert "cockpit-v3" in corpo          # chave nova descarta o cache da versão anterior
