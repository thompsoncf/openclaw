"""Extrato em PDF virando transações (finance/extrato_pdf.py).

O PDF de teste é GERADO aqui, com dados inventados. Extrato de verdade não entra
no repositório: é nome, agência, conta e a vida financeira de alguém — nada disso
tem por que morar num teste. O que o gerador copia do original é só o que importa
pro parser: as posições das colunas e o jeito do Santander de marcar débito com o
'-' DEPOIS do número.

O teste que mais vale aqui é o da trava: quando a soma não bate com o saldo
impresso, o importador tem que RECUSAR. Um parser de PDF que erra em silêncio
enche o livro caixa de lançamento errado e ninguém descobre até a conciliação.
"""
import pytest

from finance.extrato_pdf import ExtratoPdfInvalido, parsear_pdf

pymupdf = pytest.importorskip("pymupdf", reason="PyMuPDF não instalado")

# Colunas do extrato real (página de 595pt de largura).
X_DATA, X_HIST, X_VALOR, X_SALDO = 34, 65, 433, 513


def _pdf(linhas, marca="BANCO SANTANDER (BRASIL)", competencia="julho/2026"):
    """Monta um PDF com as linhas nas colunas certas.

    Cada linha é (dia, historico, valor, saldo) — todos opcionais menos o
    histórico. `valor`/`saldo` já vêm como o banco imprime ('99,95-')."""
    doc = pymupdf.open()
    pagina = doc.new_page(width=595, height=842)
    pagina.insert_text((X_DATA, 40), marca, fontsize=8)
    pagina.insert_text((X_DATA, 52), competencia, fontsize=8)
    pagina.insert_text((X_DATA, 64), "0100 01.080622-8", fontsize=8)
    y = 90
    for dia, hist, valor, saldo in linhas:
        if dia:
            pagina.insert_text((X_DATA, y), dia, fontsize=8)
        if hist:
            pagina.insert_text((X_HIST, y), hist, fontsize=8)
        if valor:
            pagina.insert_text((X_VALOR, y), valor, fontsize=8)
        if saldo:
            pagina.insert_text((X_SALDO, y), saldo, fontsize=8)
        y += 10
    bruto = doc.tobytes()
    doc.close()
    return bruto


# Extrato mínimo que FECHA: começa em 1.000,00, cai 100,00, sobe 250,00.
COERENTE = [
    ("01/07", "PIX ENVIADO", "100,00-", "900,00"),
    (None,    "Fulano de Tal", None, None),
    ("02/07", "PIX RECEBIDO", "250,00", "1.150,00"),
    (None,    "Beltrana Ltda", None, None),
]


def test_le_os_lancamentos_com_sinal_certo():
    e = parsear_pdf(_pdf(COERENTE))
    assert len(e.transacoes) == 2
    saida, entrada = e.transacoes
    assert (saida.tipo, saida.valor_centavos) == ("despesa", 10000)
    assert (entrada.tipo, entrada.valor_centavos) == ("receita", 25000)


def test_junta_o_favorecido_da_linha_de_baixo():
    """O Santander imprime quem recebeu numa segunda linha, sem valor. Sem
    juntar, a conversa vira 'PIX ENVIADO' e ninguém sabe pra quem foi."""
    e = parsear_pdf(_pdf(COERENTE))
    assert "Fulano de Tal" in e.transacoes[0].descricao
    assert "PIX ENVIADO" in e.transacoes[0].descricao


def test_data_vem_da_competencia_do_extrato():
    """A linha só traz 'dd/mm'; o ano está no título do documento."""
    e = parsear_pdf(_pdf(COERENTE))
    assert [t.data.isoformat() for t in e.transacoes] == ["2026-07-01", "2026-07-02"]
    assert e.periodo_ini.isoformat() == "2026-07-01"
    assert e.chave_conta() == "033:0100:01.080622-8"


def test_a_data_se_repete_nas_linhas_seguintes():
    """A data só é impressa quando MUDA — o resto do dia herda a anterior."""
    e = parsear_pdf(_pdf([
        ("01/07", "PIX ENVIADO", "100,00-", "900,00"),
        (None,    "COMPRA CARTAO DEB", "50,00-", None),
        (None,    "PIX RECEBIDO", "150,00", "1.000,00"),
    ]))
    assert {t.data.isoformat() for t in e.transacoes} == {"2026-07-01"}


def test_recusa_quando_a_soma_nao_bate_com_o_saldo():
    """A trava. Aqui o saldo final está errado de propósito (deveria ser
    1.150,00): o importador não pode aceitar e sujar o caixa."""
    quebrado = [
        ("01/07", "PIX ENVIADO", "100,00-", "900,00"),
        ("02/07", "PIX RECEBIDO", "250,00", "9.999,00"),
    ]
    with pytest.raises(ExtratoPdfInvalido) as err:
        parsear_pdf(_pdf(quebrado))
    assert "não vou importar" in str(err.value)


def test_recusa_banco_desconhecido_dizendo_quais_conhece():
    with pytest.raises(ExtratoPdfInvalido) as err:
        parsear_pdf(_pdf(COERENTE, marca="BANCO QUALQUER S.A."))
    assert "Santander" in str(err.value)


def test_recusa_pdf_sem_texto_explicando_que_e_digitalizado():
    doc = pymupdf.open()
    doc.new_page(width=595, height=842)          # página em branco = sem camada de texto
    bruto = doc.tobytes()
    doc.close()
    with pytest.raises(ExtratoPdfInvalido) as err:
        parsear_pdf(bruto)
    assert "digitalizado" in str(err.value)


def test_recusa_arquivo_que_nem_pdf_e():
    with pytest.raises(ExtratoPdfInvalido):
        parsear_pdf(b"isto aqui nao e um pdf")


def test_fitid_e_estavel_entre_leituras_e_unico_por_lancamento():
    """Estável: reimportar o MESMO extrato não duplica (a chave bate e o
    LivroCaixa pula). Único: dois PIX iguais no mesmo dia são lançamentos
    diferentes e não podem colidir — por isso a ordem no dia entra na chave."""
    iguais = [
        ("01/07", "PIX ENVIADO", "50,00-", None),
        (None,    "Mesmo Fulano", None, None),
        ("01/07", "PIX ENVIADO", "50,00-", "900,00"),
        (None,    "Mesmo Fulano", None, None),
    ]
    a = parsear_pdf(_pdf(iguais))
    b = parsear_pdf(_pdf(iguais))
    assert [t.fitid for t in a.transacoes] == [t.fitid for t in b.transacoes]
    assert len({t.fitid for t in a.transacoes}) == len(a.transacoes) == 2


def test_para_no_anexo_que_repete_os_lancamentos():
    """O 'extrato consolidado' traz, depois dos lançamentos, um anexo de PIX que
    REPETE tudo noutro layout. Lê-lo dobrava os valores — foi o primeiro bug
    real deste parser, e quem o denunciou foi a conferência de saldo."""
    com_anexo = COERENTE + [
        (None, "Data Canal Tipo Favorecido ISPB Agência Conta Valor (R$)", None, None),
        ("01/07", "PIX ENVIADO", "100,00-", None),
        ("02/07", "PIX RECEBIDO", "250,00", None),
    ]
    e = parsear_pdf(_pdf(com_anexo))
    assert len(e.transacoes) == 2
