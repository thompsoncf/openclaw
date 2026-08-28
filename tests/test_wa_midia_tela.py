"""A mídia aparece na conversa — carregando só quando alguém olha.

O QUE ESTE ARQUIVO PROTEGE
O passo 1 guarda o ponteiro, o passo 2 busca no CDN e decifra. Este é o pedaço que
faz a foto virar bolha — e o que decide se o desenho todo cumpre a promessa de custo
zero. Se a imagem carregar sozinha em vez de esperar entrar na tela, uma conversa de
300 mensagens vira 300 downloads na abertura, e aí guardar no nosso disco teria sido
mais barato.

O QUE NÃO PODE ESCAPAR
 1. `loading="lazy"` na imagem e `preload="none"` no vídeo — é isto que faz o custo
    ser zero pro que ninguém abre;
 2. o PONTEIRO não vai pro navegador. A tela recebe só o tipo e o tamanho; o
    endereço no CDN e a chave que decifra ficam no servidor;
 3. o erro sabe a diferença entre "o WhatsApp apagou" (410) e "não consegui agora",
    porque as ações são diferentes: uma se pede de novo ao cliente, a outra se tenta
    de novo;
 4. mensagem sem mídia continua exatamente como era — é a esmagadora maioria.

E A ARMADILHA DA ASPA
Este painel é montado com string Python NÃO-raw. O `\\'` que o JS precisa dentro de
um atributo chega na página como `'` e quebra o script inteiro — a mesma armadilha
do `\\n` que o tests/test_painel_js_sintaxe.py já vigia. Foi por isso que o endereço
do "tentar de novo" foi pra um `data-src` em vez de ficar dentro do `onclick`.
"""
import inspect
import re

from web import painel_prospeccao as pp


def _js():
    """O JS da página de comunicação, como ele chega no navegador."""
    fonte = inspect.getsource(pp)
    i = fonte.index("function cxMidiaHtml")
    return fonte[i:i + 4000]


# ------------------------------------------------ o que faz o custo ser zero

def test_a_foto_so_carrega_quando_entra_na_tela():
    """Sem isto, abrir uma conversa de 300 mensagens dispara 300 downloads."""
    assert 'loading="lazy"' in _js()


def test_o_video_nao_baixa_sozinho():
    """Vídeo que carrega sozinho gasta o pacote de dados de quem está na rua — e a
    maioria nem é assistida."""
    js = _js()
    assert 'preload="none"' in js
    # o HTML emitido não pode ter o atributo (a palavra aparece no comentário logo
    # acima, explicando justamente por que ela não está lá)
    assert "autoplay" not in js[js.index("<video"):js.index("<video") + 200]


def test_o_endereco_e_a_chave_nao_vao_pro_navegador():
    """O `src` aponta pra NOSSA rota, que resolve o ponteiro do lado de cá. Mandar
    `directPath`/`mediaKey` pro navegador seria entregar a chave do arquivo — e ele
    nem conseguiria usar, porque o CDN do WhatsApp não atende browser."""
    js = _js()
    assert "'/painel/prospeccao/midia/'+m.id" in js
    assert "directPath" not in js and "mediaKey" not in js
    assert "mmg.whatsapp.net" not in js


def test_o_thread_manda_tipo_e_tamanho_mas_nunca_o_ponteiro():
    fonte = inspect.getsource(pp.prospeccao_comunicacao_thread)
    assert "midia_tipo" in fonte and "midia_meta" in fonte
    assert "midia_ref" in fonte, "o case que decide se há mídia lê midia_ref"
    # ...mas o que vai pro payload é só tipo + meta
    assert 'item["midia"] = {"tipo": midia_tipo, **(midia_meta or {})}' in fonte
    # e o que sai junto é só o id da mensagem — o ponteiro fica no servidor
    depois = fonte.split('item["midia"]')[1][:200]
    assert "directPath" not in depois and "mediaKey" not in depois


# ------------------------------------------------------- os quatro desenhos

def test_cada_tipo_ganha_o_seu_desenho():
    js = _js()
    assert "<img" in js, "imagem"
    assert "<video controls" in js, "vídeo com controles"
    assert "cx-doc" in js and "target=" in js, "documento abre em aba"
    assert "fig" in js, "figurinha tem tamanho próprio"


def test_documento_mostra_nome_e_tamanho():
    """Um PDF na conversa se identifica pelo nome — 'documento' não diz nada."""
    js = _js()
    assert "d.nome" in js and "cxTam(d.bytes)" in js


def test_tamanho_legivel():
    assert pp is not None
    js = inspect.getsource(pp)
    i = js.index("function cxTam(")
    corpo = js[i:i + 400]
    assert "1048576" in corpo and "MB" in corpo and "KB" in corpo


# --------------------------------------------------------------- os erros

def test_expirado_e_falha_dizem_coisas_diferentes():
    js = _js()
    assert "r.status===410" in js
    assert "não está mais no servidor do WhatsApp" in js
    assert "Não consegui carregar agora" in js
    assert "tentar de novo" in js


def test_tentar_de_novo_fura_o_cache_do_erro():
    """Sem o cache-buster o navegador devolve o próprio erro cacheado e o botão não
    tenta nada — o vendedor clica, nada muda, e ele conclui que está quebrado."""
    js = inspect.getsource(pp)
    i = js.index("function cxMidiaDeNovo")
    assert "Date.now()" in js[i:i + 500]


def test_o_endereco_do_retry_nao_mora_dentro_do_onclick():
    """A armadilha da aspa: `\\'` em atributo, num template Python não-raw, chega na
    página como `'` e quebra o script inteiro. Mesma família do `\\n`."""
    js = inspect.getsource(pp)
    i = js.index("function cxMidiaErro")
    trecho = js[i:i + 1200]
    assert 'data-src="' in trecho
    assert "cxMidiaDeNovo(this);" in trecho, "o handler lê o data-, não recebe a URL"


# ------------------------------------------------- o que não pode mudar

def test_mensagem_sem_midia_nao_ganha_nada():
    """É a esmagadora maioria das linhas: o caminho de sempre tem que sair intacto."""
    js = _js()
    i = js.index("function cxMidiaHtml")
    assert re.search(r"if\(!d\|\|!m\.id\)return '';", js[i:i + 300]), \
        "sem mídia (ou sem id) a função devolve string vazia e a bolha fica como era"


def test_a_bolha_continua_mostrando_texto_e_selo():
    """A legenda da foto e o ✓✓ de entrega não podem sumir por causa da imagem."""
    fonte = inspect.getsource(pp)
    i = fonte.index("h+='<div class=\"'+cls+'\">'+cab+cxMidiaHtml(m)+corpo")
    linha = fonte[i:i + 220]
    assert "cxTick(m)" in linha and "+corpo+" in linha
