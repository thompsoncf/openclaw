"""Comprovante de pagamento: bucket PRIVADO, e quem entrega é o Zaq.

POR QUE NÃO REUSA O UPLOAD QUE JÁ EXISTIA. `finance/upload_foto` sobe pro Supabase
num bucket PÚBLICO e devolve URL aberta com cache de um ano. Para foto de produto
está certo — a foto existe pra ser vista.

Comprovante bancário é o oposto: nome do cliente, banco, valor e às vezes CPF.
Numa URL pública, qualquer pessoa com o link lê o documento — e link se encaminha,
se cola em grupo, se indexa. Não existe "despublicar" depois.

Então o acordo aqui tem três pernas, e só vale se as três valerem:

    1. o arquivo vai pra um bucket PRIVADO
    2. o banco guarda o CAMINHO, nunca uma URL
    3. quem entrega é uma rota do Zaq, que confere sessão e conta antes

Estes casos prendem isso, mais a regra de um comprovante por parcela.
"""
import pytest

from finance import comprovantes as cp


class _Resp:
    def __init__(self, status=200, content=b"", headers=None):
        self.status_code = status
        self.content = content
        self.text = ""
        self.headers = headers or {}


class _Cliente:
    """Dublê do httpx.Client. Guarda o que foi pedido pra os casos olharem."""
    ultimo = {}

    def __init__(self, *a, **kw):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, url, content=None, headers=None):
        _Cliente.ultimo = {"metodo": "POST", "url": url, "headers": headers or {},
                           "bytes": len(content or b"")}
        return _Resp(_Cliente.resposta_post)

    def get(self, url, headers=None):
        _Cliente.ultimo = {"metodo": "GET", "url": url, "headers": headers or {}}
        return _Resp(200, b"%PDF-1.4 fake", {"content-type": "application/pdf"})

    def request(self, metodo, url, headers=None):
        _Cliente.ultimo = {"metodo": metodo, "url": url}
        return _Resp(200)


@pytest.fixture()
def storage(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://xxx.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "chave-de-servico")
    monkeypatch.setenv("SUPABASE_BUCKET_DOCS", "documentos")
    _Cliente.resposta_post = 200
    _Cliente.ultimo = {}
    monkeypatch.setattr(cp.httpx, "Client", _Cliente)
    return _Cliente


# ------------------------------------------------------------- o que aceita

@pytest.mark.parametrize("tipo", ["application/pdf", "image/jpeg", "image/png", "image/webp"])
def test_aceita_o_que_sai_de_banco_e_de_celular(tipo):
    assert cp.validar(b"x" * 10, tipo) == tipo


def test_recusa_o_que_nao_e_documento():
    """Planilha e .exe não são comprovante. E a mensagem vai direto pra tela, então
    tem que estar em português."""
    with pytest.raises(ValueError) as e:
        cp.validar(b"x", "application/vnd.ms-excel")
    assert "PDF ou imagem" in str(e.value)


def test_recusa_arquivo_grande_e_arquivo_vazio():
    with pytest.raises(ValueError) as e:
        cp.validar(b"x" * (cp.MAX_BYTES + 1), "application/pdf")
    assert "10 MB" in str(e.value)
    with pytest.raises(ValueError):
        cp.validar(b"", "application/pdf")


def test_o_content_type_com_charset_nao_atrapalha():
    """Navegador manda 'image/jpeg; charset=binary' e a comparação crua recusaria
    um comprovante perfeitamente válido."""
    assert cp.validar(b"x", "image/jpeg; charset=binary") == "image/jpeg"


# --------------------------------------------------- as três pernas do acordo

def test_sobe_pro_bucket_PRIVADO_e_nunca_pro_das_fotos(storage, monkeypatch):
    """O default é bucket próprio. Um erro de env não pode fazer comprovante cair
    no bucket público sem ninguém perceber."""
    monkeypatch.delenv("SUPABASE_BUCKET_DOCS", raising=False)
    cp.subir(b"%PDF", "application/pdf", conta_id=34, orcamento_id=14, parcela_idx=0)
    url = storage.ultimo["url"]
    assert "/object/documentos/" in url
    assert "produtos" not in url
    # e NÃO usa o caminho público do Supabase
    assert "/object/public/" not in url


def test_devolve_o_CAMINHO_e_nao_uma_url(storage):
    """Se isto virar URL, o banco guarda um endereço aberto e a rota de leitura
    deixa de ser a única porta."""
    caminho = cp.subir(b"%PDF", "application/pdf", conta_id=34, orcamento_id=14,
                       parcela_idx=2)
    assert not caminho.startswith("http")
    assert caminho.startswith("comprovantes/34/14-2-")
    assert caminho.endswith(".pdf")


def test_a_leitura_passa_pela_chave_de_servico(storage):
    """É o que faz o bucket poder ser privado."""
    conteudo, tipo = cp.ler("comprovantes/34/x.pdf")
    assert conteudo.startswith(b"%PDF")
    assert storage.ultimo["headers"]["Authorization"] == "Bearer chave-de-servico"
    assert "/object/public/" not in storage.ultimo["url"]


def test_o_caminho_leva_a_conta_no_comeco(storage):
    """Defesa em profundidade além do WHERE do banco, e um dia dá pra apagar tudo
    de uma conta por prefixo."""
    assert cp.subir(b"x", "image/png", conta_id=99, orcamento_id=1,
                    parcela_idx=0).startswith("comprovantes/99/")


# ------------------------------------------------------------ quando dá errado

def test_bucket_que_nao_existe_diz_o_nome(storage):
    """O erro de instalação mais provável. "Falha no upload (404)" mandaria o dono
    abrir um chamado; dizer o nome do bucket resolve em um minuto."""
    storage.resposta_post = 404
    with pytest.raises(ValueError) as e:
        cp.subir(b"x", "application/pdf", conta_id=1, orcamento_id=1, parcela_idx=0)
    assert "documentos" in str(e.value)


def test_sem_storage_configurado_a_tela_sabe_ANTES(monkeypatch):
    """Botão que engole comprovante é pior que botão nenhum: o dono anexaria, veria
    "ok", e o arquivo não estaria em lugar nenhum no dia da auditoria."""
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    assert cp.configurado() is False


def test_apagar_o_antigo_nunca_levanta(storage, monkeypatch):
    """Só roda ao SUBSTITUIR. Derrubar a substituição por causa da limpeza do
    arquivo velho seria trocar um problema por um pior."""
    def _explode(*a, **kw):
        raise RuntimeError("supabase fora")
    monkeypatch.setattr(cp.httpx, "Client", _explode)
    cp.apagar("comprovantes/1/x.pdf")      # não levanta
