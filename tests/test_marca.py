"""Helper do selo da marca (finance/marca.py) — funções puras, sem banco."""
from finance import marca


def test_iniciais():
    assert marca.iniciais("Grupo Manoel Soares") == "GM"
    assert marca.iniciais("Padaria") == "P"
    assert marca.iniciais("") == "?"
    assert marca.iniciais("hortifruti-express") == "HE"   # hífen conta como espaço


def test_avatar_com_logo():
    """O padrão é CONTAIN: marca cortada é marca estragada — e desde que o upload
    parou de cortar a logo em quadrado, ela chega aqui na proporção original."""
    h = marca.avatar_html({"logo_url": "https://x/l.jpg"}, px=40)
    assert 'src="https://x/l.jpg"' in h and "width:40px" in h and "object-fit:contain" in h


def test_avatar_contain():
    h = marca.avatar_html({"logo_url": "https://x/l.jpg"}, ajuste="contain")
    assert "object-fit:contain" in h


def test_avatar_cover_quando_pedido():
    """Quem quiser preencher o quadrado (e aceitar o corte) ainda pede."""
    h = marca.avatar_html({"logo_url": "https://x/l.jpg"}, ajuste="cover")
    assert "object-fit:cover" in h


def test_avatar_sem_logo_usa_iniciais_e_cor():
    h = marca.avatar_html({"nome": "Grupo Manoel Soares", "cor": "#123abc"})
    assert ">GM</span>" in h and "background:#123abc" in h and "<img" not in h


def test_avatar_so_logo_sem_logo_vazio():
    assert marca.avatar_html({"nome": "X"}, so_logo=True) == ""


def test_avatar_cor_padrao_quando_ausente():
    assert "background:#2f7d32" in marca.avatar_html({"nome": "X"})


def test_cabecalho_tem_selo_nome_e_sub():
    h = marca.cabecalho_html({"nome": "ACME", "logo_url": "https://x/l.jpg"},
                             sub="Proposta")
    assert 'src="https://x/l.jpg"' in h and ">ACME</b>" in h and ">Proposta</small>" in h


def test_cabecalho_alinhar_right():
    assert "margin-left:auto" in marca.cabecalho_html({"nome": "X"}, alinhar="right")
    assert "justify-content:center" in marca.cabecalho_html({"nome": "X"}, alinhar="center")


def test_cabecalho_so_logo_sem_logo_vazio():
    assert marca.cabecalho_html({"nome": "X"}, so_logo=True) == ""


def test_escapa_nome_e_cor():
    # nome/cor vindos do banco NÃO podem injetar HTML no cabeçalho
    h = marca.cabecalho_html({"nome": '<script>x', "cor": '"><b>'})
    assert "<script>" not in h and "&lt;script&gt;x" in h
