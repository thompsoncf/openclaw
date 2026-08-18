"""A seção "Já baixados" da aba Empresa, e a regra do botão de apagar.

A tela listava só `status='aberto'` (`portal.py`, painel_empresa). Consequência: título
baixado sumia do app inteiro — e o baixado por engano, ou aquele cujo lançamento foi
apagado no financeiro, ficava preso pra sempre, sem caminho nenhum pra remover. Foi assim
que uma conta de teste acumulou 3 títulos 'pago' órfãos que só saíram por SQL.

O que este arquivo trava:

1. a seção existe e vem RECOLHIDA (histórico não é operação);
2. o "apagar" só aparece pro título **sem** lançamento no caixa — quando há lançamento,
   a tela manda pro financeiro em vez de oferecer um botão que o backend recusaria;
3. a rota `/apagar` continua ligada ao `apagar_titulo` (o botão do aberto já existia e o
   fio já esteve invisível uma vez — dá pra cortar sem ninguém ver).
"""
import inspect
import re

from web import portal


def _bloco_pagos() -> str:
    """O trecho do template entre o <details> dos pagos e o fim dele."""
    m = re.search(r'<details class="tit-pagos".*?</details>', portal._EMPRESA, re.S)
    assert m, "a seção 'Já baixados' sumiu do template"
    return m.group(0)


def test_a_secao_de_baixados_existe_e_nasce_recolhida():
    bloco = _bloco_pagos()
    assert "Já baixados" in bloco
    # <details> sem `open`: recolhida. O dia a dia é o que está EM ABERTO.
    assert not re.search(r"<details[^>]*\bopen\b", bloco)


def test_o_apagar_so_aparece_sem_lancamento_no_caixa():
    """A regra de negócio na tela tem que casar com a do backend (apagar_titulo).
    Oferecer o botão pro título que tem lançamento seria prometer o que o servidor
    recusa — e o usuário clicaria achando que não funcionou."""
    bloco = _bloco_pagos()
    # recorta O `if` DO CAIXA, não o primeiro do bloco: a linha do "baixado dd/mm" tem
    # um {% if t.pago_em %}...{% else %} antes, e partir no primeiro else lia o trecho
    # errado — o teste passava por acidente.
    m = re.search(r"\{%\s*if t\.lancamento_id\s*%\}(.*?)\{%\s*endif\s*%\}", bloco, re.S)
    assert m, "a tela não checa o vínculo com o caixa"
    com_caixa, _, sem_caixa = m.group(1).partition("{% else %}")
    assert sem_caixa, "o if do caixa não tem ramo else"
    assert "/apagar" in sem_caixa, "o apagar caiu no ramo errado do if"
    assert "/apagar" not in com_caixa, "o apagar está sendo oferecido pra quem tem caixa"
    assert "/painel/financeiro" in com_caixa, "quem tem lançamento precisa do caminho"


def test_a_rota_carrega_os_pagos_e_o_fio_do_apagar_continua_ligado():
    fonte = inspect.getsource(portal.painel_empresa)
    assert 'status="pago"' in fonte and "titulos_pagos=titulos_pagos" in fonte
    assert "apagar_titulo" in inspect.getsource(portal.empresa_titulo_apagar)
