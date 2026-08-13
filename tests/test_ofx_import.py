"""Import de extrato bancário (OFX): parser/heurística (sem banco) + o fluxo
completo via LivroCaixa (idempotência por chave, duplicata sugerida, aprendizado
por contraparte).

Roda com banco de TESTE separado (ver tests/conftest.py):
    export TEST_DATABASE_URL="postgresql://.../banco_de_teste"
    pytest tests/test_ofx_import.py
"""
import os
from datetime import date
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from db.conexao import init_schema
from finance import plano_contas as pc
from finance.livro_caixa import LivroCaixa
from finance.models import Lancamento
from finance.ofx_import import (
    OfxExtrato, OfxInvalido, OfxTransacao, decodificar, parsear_ofx,
    sugerir_classificacao,
)

# ─────────────────────────────────────────────────────────────────────────
# Parser + heurística — puro, sem banco.
# ─────────────────────────────────────────────────────────────────────────

_OFX_AMOSTRA = """OFXHEADER:100
DATA:OFXSGML
VERSION:102
SECURITY:NONE
ENCODING:USASCII
CHARSET:1252
COMPRESSION:NONE
OLDFILEUID:NONE
NEWFILEUID:NONE
<OFX>
<SIGNONMSGSRSV1>
<SONRS>
<STATUS>
<CODE>0</CODE>
<SEVERITY>INFO</SEVERITY>
</STATUS>
<DTSERVER>20260803120000[-3:BRT]</DTSERVER>
<LANGUAGE>POR</LANGUAGE>
<FI>
<ORG>Banco Cooperativo do Brasil</ORG>
<FID>756</FID>
</FI>
</SONRS>
</SIGNONMSGSRSV1>
<BANKMSGSRSV1>
<STMTTRNRS>
<TRNUID>1</TRNUID>
<STATUS>
<CODE>0</CODE>
<SEVERITY>INFO</SEVERITY>
</STATUS>
<STMTRS>
<CURDEF>BRL</CURDEF>
<BANKACCTFROM>
<BANKID>756</BANKID>
<BRANCHID>4436-9</BRANCHID>
<ACCTID>39449-1</ACCTID>
<ACCTTYPE>CHECKING</ACCTTYPE>
</BANKACCTFROM>
<BANKTRANLIST>
<DTSTART>20260701120000[-3:BRT]</DTSTART>
<DTEND>20260731120000[-3:BRT]</DTEND>
<STMTTRN>
<TRNTYPE>CREDIT</TRNTYPE>
<DTPOSTED>20260707120000[-3:BRT]</DTPOSTED>
<TRNAMT>2680.00</TRNAMT>
<FITID>202607072680001</FITID>
<CHECKNUM>0</CHECKNUM>
<REFNUM>Pix</REFNUM>
<MEMO>PIX RECEBIDO - OUTRA IF</MEMO>
<NAME>Recebimento Pix Espaco Pelle Clinica Derma</NAME>
</STMTTRN>
<STMTTRN>
<TRNTYPE>DEBIT</TRNTYPE>
<DTPOSTED>20260723120000[-3:BRT]</DTPOSTED>
<TRNAMT>-93.00</TRNAMT>
<FITID>2026072393001</FITID>
<CHECKNUM>7119835</CHECKNUM>
<REFNUM>7119835</REFNUM>
<MEMO>COBRANCA DE ALUGUEL - SIPAG 2.0</MEMO>
<NAME>VL pend Aluguel</NAME>
</STMTTRN>
<STMTTRN>
<TRNTYPE>DEBIT</TRNTYPE>
<DTPOSTED>20260709120000[-3:BRT]</DTPOSTED>
<TRNAMT>-3727.17</TRNAMT>
<FITID>202607093727171</FITID>
<CHECKNUM>7606549</CHECKNUM>
<REFNUM>7606549</REFNUM>
<MEMO>DEB. PAGAMENTO DE BOLETO INTERCREDIS</MEMO>
<NAME>Pagamento de cartao de credito</NAME>
</STMTTRN>
<STMTTRN>
<TRNTYPE>DEBIT</TRNTYPE>
<DTPOSTED>20260706120000[-3:BRT]</DTPOSTED>
<TRNAMT>-56.93</TRNAMT>
<FITID>2026070656931</FITID>
<CHECKNUM>018422</CHECKNUM>
<REFNUM>018422</REFNUM>
<MEMO>COMPRA VISA ELECTRON</MEMO>
<NAME>LEO MERCADO TERESINA BR</NAME>
</STMTTRN>
<STMTTRN>
<TRNTYPE>DEBIT</TRNTYPE>
<TRNAMT>-300.00</TRNAMT>
<FITID>SEMDATA</FITID>
</STMTTRN>
</BANKTRANLIST>
<LEDGERBAL>
<BALAMT>6.07</BALAMT>
<DTASOF>20260731120000[-3:BRT]</DTASOF>
</LEDGERBAL>
</STMTRS>
</STMTTRNRS>
</BANKMSGSRSV1>
</OFX>
"""


def test_decodificar_aceita_cp1252_e_utf8():
    texto = "DÉBITO PACOTE SERVIÇOS"
    assert decodificar(texto.encode("cp1252")) == texto
    assert decodificar(texto.encode("utf-8")) == texto


def test_parsear_ofx_dados_da_conta():
    ext = parsear_ofx(_OFX_AMOSTRA)
    assert ext.banco_id == "756"
    assert ext.agencia == "4436-9"
    assert ext.conta == "39449-1"
    assert ext.tipo_conta == "CHECKING"
    assert ext.moeda == "BRL"
    assert ext.periodo_ini == date(2026, 7, 1)
    assert ext.periodo_fim == date(2026, 7, 31)
    assert ext.saldo_final_centavos == 607
    assert ext.saldo_data == date(2026, 7, 31)
    assert ext.chave_conta() == "756:4436-9:39449-1"


def test_parsear_ofx_transacoes_e_sinal_define_tipo():
    ext = parsear_ofx(_OFX_AMOSTRA)
    # a última transação do fixture não tem DTPOSTED -> é ignorada, não trava o parser
    assert len(ext.transacoes) == 4

    pix = ext.transacoes[0]
    assert pix.fitid == "202607072680001"
    assert pix.data == date(2026, 7, 7)
    assert pix.tipo == "receita"
    assert pix.valor_centavos == 268000
    assert pix.descricao == "Recebimento Pix Espaco Pelle Clinica Derma"

    aluguel = ext.transacoes[1]
    assert aluguel.tipo == "despesa"
    assert aluguel.valor_centavos == 9300


def test_parsear_ofx_invalido_sem_stmtrs():
    with pytest.raises(OfxInvalido):
        parsear_ofx("<OFX><SIGNONMSGSRSV1></SIGNONMSGSRSV1></OFX>")


def test_parsear_ofx_sem_tag_ofx_nao_e_ofx():
    with pytest.raises(OfxInvalido):
        parsear_ofx("isso aqui nao e' um arquivo ofx")


def test_sgml_sem_fechamento_de_tag_tambem_parseia():
    """Muitos bancos exportam SGML antigo, sem `</TAG>` nas folhas — o parser
    tem que fechar sozinho, não só aceitar o formato já bem-formado do Sicoob."""
    bruto = """<OFX>
<BANKMSGSRSV1><STMTTRNRS><STMTRS>
<CURDEF>BRL
<BANKACCTFROM>
<BANKID>001
<BRANCHID>1234
<ACCTID>5678-9
<ACCTTYPE>CHECKING
</BANKACCTFROM>
<BANKTRANLIST>
<STMTTRN>
<TRNTYPE>DEBIT
<DTPOSTED>20260101120000
<TRNAMT>-10.50
<FITID>ABC123
<MEMO>TARIFA BANCARIA
</STMTTRN>
</BANKTRANLIST>
</STMTRS></STMTTRNRS></BANKMSGSRSV1>
</OFX>"""
    ext = parsear_ofx(bruto)
    assert ext.banco_id == "001"
    assert len(ext.transacoes) == 1
    assert ext.transacoes[0].valor_centavos == 1050
    assert ext.transacoes[0].tipo == "despesa"


@pytest.mark.parametrize("memo,name,tipo,categoria,confianca,plano", [
    ("COBRANCA DE ALUGUEL - SIPAG 2.0", "VL pend Aluguel", "despesa", "Moradia", "alta", "5.1.01"),
    ("COMPRA VISA ELECTRON", "LEO MERCADO TERESINA BR", "despesa", "Mercado", "media", "3.1.03"),
    ("COMPRA VISA ELECTRON", "PostoTexas TERESINA BR", "despesa", "Transporte", "media", "5.1.08"),
    ("DEBITO PACOTE SERVICOS", "", "despesa", "Servicos", "alta", "6.1.02"),
    # serviços de empresa (contas do grupo 5 criadas na migração 143)
    ("PIX ENVIADO", "DIARISTA MARIA", "despesa", "Servicos", "alta", "5.1.09"),
    ("PAGTO FAXINA DA SEMANA", "", "despesa", "Servicos", "alta", "5.1.09"),
    ("MANUTENCAO AR CONDICIONADO", "", "despesa", "Servicos", "media", "5.1.11"),
    ("PIX CONSERTO DA CAMARA FRIA", "", "despesa", "Servicos", "media", "5.1.11"),
    ("PAGAMENTO ELETRICISTA", "", "despesa", "Servicos", "media", "5.1.11"),
    ("COMPRA MATERIAL DE LIMPEZA", "", "despesa", "Compras", "alta", "5.1.05"),
    ("NF SERVICO TERCEIRIZADO", "", "despesa", "Servicos", "media", "5.1.10"),
    # não-regressão: "manutenção de conta" é TARIFA DE BANCO, não conservação.
    # As duas últimas variantes (sem "de") só passam por causa da guarda nova —
    # sem ela cairiam na regra de manutenção e virariam 5.1.11.
    ("MANUTENCAO DE CONTA", "", "despesa", "Servicos", "alta", "6.1.02"),
    ("TAR MANUTENCAO CONTA CORRENTE", "", "despesa", "Servicos", "alta", "6.1.02"),
    ("DEB MANUTENCAO CTA", "", "despesa", "Servicos", "alta", "6.1.02"),
    # concessionárias e telecom (5.1.02)
    ("DEB AUT EQUATORIAL PIAUI", "", "despesa", "Contas de casa", "alta", "5.1.02"),
    ("AGESPISA SANEAMENTO", "", "despesa", "Contas de casa", "alta", "5.1.02"),
    ("PAGTO CONTA DE LUZ", "", "despesa", "Contas de casa", "alta", "5.1.02"),
    ("BRISANET BANDA LARGA", "", "despesa", "Contas de casa", "alta", "5.1.02"),
    # software e assinaturas (5.1.06)
    ("MICROSOFT 365", "", "despesa", "Assinaturas", "media", "5.1.06"),
    ("COMPRA CANVA PRO", "", "despesa", "Assinaturas", "media", "5.1.06"),
    ("GOOGLE WORKSPACE", "", "despesa", "Assinaturas", "media", "5.1.06"),
    # contabilidade e consultorias (5.1.07)
    ("PIX ESCRITORIO CONTABIL SILVA", "", "despesa", "Servicos", "media", "5.1.07"),
    ("HONORARIOS CONTADOR", "", "despesa", "Servicos", "media", "5.1.07"),
    ("CONSULTORIA TRIBUTARIA", "", "despesa", "Servicos", "media", "5.1.07"),
    # não-regressão: anúncio é MARKETING, não software — tem que ganhar de "google"
    ("GOOGLE ADS", "", "despesa", "Servicos", "alta", "5.1.03"),
    ("FACEBK ADS META", "", "despesa", "Servicos", "alta", "5.1.03"),
])
def test_sugerir_classificacao_regras_de_texto(memo, name, tipo, categoria, confianca, plano):
    txn = OfxTransacao(fitid="x", data=date(2026, 1, 1), valor_centavos=100,
                       tipo=tipo, trntype="DEBIT", memo=memo, name=name,
                       checknum="", refnum="")
    s = sugerir_classificacao(txn)
    assert s.categoria == categoria
    assert s.confianca == confianca
    assert s.plano_conta_codigo == plano


def test_sugerir_classificacao_fatura_de_cartao_nao_arrisca_categoria():
    txn = OfxTransacao(fitid="x", data=date(2026, 1, 1), valor_centavos=372717,
                       tipo="despesa", trntype="DEBIT",
                       memo="DEB. PAGAMENTO DE BOLETO INTERCREDIS",
                       name="Pagamento de cartao de credito", checknum="", refnum="")
    s = sugerir_classificacao(txn)
    assert s.categoria is None
    assert s.plano_conta_codigo is None
    assert "fatura consolidada" in s.motivo


@pytest.mark.parametrize("memo,proibido", [
    # serviços (5.1.09 / 5.1.11)
    ("PREPARO DE BUFFET",        "5.1.11"),  # "preparo" contém "reparo"
    ("DIARIA HOTEL SAO LUIS",    "5.1.09"),  # "diaria" não é diarista
    ("MERCADINHO JARDINS",       "5.1.11"),  # "Jardins" é bairro, não jardinagem
    # concessionárias (5.1.02): as palavras comuns do setor são armadilha
    ("PIX MARIA LUZIA",          "5.1.02"),  # "Luzia" contém "luz"
    ("ACOUGUE BOI GORDO",        "5.1.02"),  # "boi" contém "oi"
    ("PIX JOSE NETO",            "5.1.02"),  # "Neto" contém "net"
    ("SOM AO VIVO EVENTO",       "5.1.02"),  # "ao vivo" contém "vivo"
    ("SUPERMERCADO RIO CLARO",   "5.1.02"),  # "Rio Claro" contém "claro"
    ("COMPRA DESODORANTE",       "5.1.02"),  # "desodorante" contém "deso"
])
def test_sugerir_classificacao_nao_cai_em_falso_positivo_de_substring(memo, proibido):
    """O match é substring cru, então termo curto demais vira armadilha. Cada
    memo aqui contém um pedaço de uma palavra que a gente NÃO usou como termo —
    se alguém encurtar um termo da regra, este teste quebra."""
    txn = OfxTransacao(fitid="x", data=date(2026, 1, 1), valor_centavos=100,
                       tipo="despesa", trntype="DEBIT", memo=memo, name="",
                       checknum="", refnum="")
    s = sugerir_classificacao(txn)
    assert s.plano_conta_codigo != proibido


def test_sugerir_classificacao_pix_recebido_sem_padrao_fica_sem_categoria():
    """Receita por Pix/transferência sem heurística de texto forte: melhor não
    chutar 'Vendas' de qualquer entrada — isso é trabalho do aprendizado por
    contraparte (LivroCaixa), não da heurística de texto."""
    txn = OfxTransacao(fitid="x", data=date(2026, 1, 1), valor_centavos=100000,
                       tipo="receita", trntype="CREDIT", memo="PIX RECEBIDO - OUTRA IF",
                       name="Recebimento Pix Fulano de Tal", checknum="", refnum="")
    s = sugerir_classificacao(txn)
    assert s.categoria is None
    assert s.confianca is None


# ─────────────────────────────────────────────────────────────────────────
# Fluxo completo via LivroCaixa — precisa de banco.
# ─────────────────────────────────────────────────────────────────────────

_MIGRACOES = ("018_chave_nfce_lancamentos.sql", "057_natureza_lancamento.sql",
              "132_plano_contas_centros_custo.sql", "138_chave_ofx_lancamentos.sql",
              # 143 traz as contas que as regras novas citam (5.1.09/5.1.11) —
              # sem ela o código não resolve e o plano_conta_id vira None calado.
              "143_plano_contas_locacao_buffet_servicos.sql")


@pytest.fixture(scope="module")
def pool():
    test_db_url = os.environ["TEST_DATABASE_URL"]
    p = ConnectionPool(test_db_url, min_size=1, max_size=4, open=True,
                       kwargs={"prepare_threshold": None})
    init_schema(p)
    base = Path(__file__).resolve().parent.parent / "db" / "migracoes"
    for m in _MIGRACOES:
        with p.connection() as c:
            c.execute((base / m).read_text(encoding="utf-8"))
            c.commit()
    yield p
    p.close()


@pytest.fixture()
def conta_id(pool):
    with pool.connection() as c:
        cid = c.execute(
            "insert into contas (tipo, nome) values ('pj', 'Espaco Pelle') returning id"
        ).fetchone()[0]
        c.commit()
    return cid


@pytest.fixture()
def livro(pool, conta_id):
    return LivroCaixa(pool, conta_id)


def _plano_id(pool, codigo: str) -> int:
    return next(c["id"] for c in pc.listar_plano(pool) if c["codigo"] == codigo)


def _extrato(transacoes) -> OfxExtrato:
    return OfxExtrato(banco_id="756", agencia="4436-9", conta="39449-1",
                      tipo_conta="CHECKING", moeda="BRL",
                      periodo_ini=date(2026, 7, 1), periodo_fim=date(2026, 7, 31),
                      saldo_final_centavos=607, saldo_data=date(2026, 7, 31),
                      transacoes=transacoes)


def test_pre_visualizar_sugere_por_heuristica_de_texto(livro):
    txn = OfxTransacao(fitid="f1", data=date(2026, 7, 23), valor_centavos=9300,
                       tipo="despesa", trntype="DEBIT",
                       memo="COBRANCA DE ALUGUEL", name="VL pend Aluguel",
                       checknum="", refnum="")
    itens = livro.pre_visualizar_importacao_ofx(_extrato([txn]), natureza_padrao="empresa")
    assert len(itens) == 1
    item = itens[0]
    assert item["categoria"] == "Moradia"
    assert item["confianca"] == "alta"
    assert item["natureza"] == "empresa"
    assert item["ja_importado"] is False
    assert item["duplicatas_provaveis"] == []
    assert item["selecionado_padrao"] is True


def test_pre_visualizar_resolve_conta_das_regras_novas(livro, pool):
    """As regras citam códigos por TEXTO ('5.1.09'); quem resolve pra id é o
    mapa do plano. Se a conta não existir no banco, livro_caixa devolve
    plano_conta_id=None SEM erro nenhum — o cliente veria categoria sugerida e
    conta contábil vazia. Este teste fecha essa porta."""
    txns = [
        OfxTransacao(fitid="d1", data=date(2026, 7, 10), valor_centavos=15000,
                     tipo="despesa", trntype="DEBIT", memo="PIX ENVIADO",
                     name="DIARISTA MARIA", checknum="", refnum=""),
        OfxTransacao(fitid="d2", data=date(2026, 7, 11), valor_centavos=48000,
                     tipo="despesa", trntype="DEBIT",
                     memo="MANUTENCAO AR CONDICIONADO", name="", checknum="", refnum=""),
    ]
    itens = livro.pre_visualizar_importacao_ofx(_extrato(txns), natureza_padrao="empresa")
    por_fitid = {i["fitid"]: i for i in itens}
    assert por_fitid["d1"]["plano_conta_id"] == _plano_id(pool, "5.1.09")
    assert por_fitid["d2"]["plano_conta_id"] == _plano_id(pool, "5.1.11")
    assert por_fitid["d1"]["categoria"] == "Servicos"


def test_confirmar_importacao_grava_com_origem_extrato_e_chave(livro, pool, conta_id):
    plano_moradia = _plano_id(pool, "5.1.01")
    itens = [{"fitid": "f1", "tipo": "despesa", "valor_centavos": 9300,
              "data": date(2026, 7, 23), "descricao": "VL pend Aluguel",
              "categoria": "Moradia", "natureza": "empresa",
              "plano_conta_id": plano_moradia, "centro_custo_id": None}]
    r = livro.confirmar_importacao_ofx("756:4436-9:39449-1", itens)
    assert r == {"importados": 1, "ja_existiam": 0}

    with pool.connection() as c:
        row = c.execute(
            """select origem, chave, categoria, natureza, plano_conta_id, valor_centavos
                 from lancamentos where conta_id=%s""", (conta_id,)).fetchone()
    assert row[0] == "extrato"
    assert row[1] == "ofx:756:4436-9:39449-1:f1"
    assert row[2] == "Moradia"
    assert row[3] == "empresa"
    assert row[4] == plano_moradia
    assert row[5] == 9300


def test_confirmar_importacao_e_idempotente_por_fitid(livro):
    item = [{"fitid": "f-repete", "tipo": "despesa", "valor_centavos": 5000,
             "data": date(2026, 7, 1), "descricao": "Teste", "categoria": "Outros",
             "natureza": None, "plano_conta_id": None, "centro_custo_id": None}]
    r1 = livro.confirmar_importacao_ofx("756:4436-9:39449-1", item)
    r2 = livro.confirmar_importacao_ofx("756:4436-9:39449-1", item)
    assert r1 == {"importados": 1, "ja_existiam": 0}
    assert r2 == {"importados": 0, "ja_existiam": 1}
    assert livro.saldo_centavos() == -5000  # não duplicou o valor


def test_pre_visualizar_marca_ja_importado_apos_reimportar_mesmo_extrato(livro):
    txn = OfxTransacao(fitid="f-reimport", data=date(2026, 7, 5), valor_centavos=2000,
                       tipo="despesa", trntype="DEBIT", memo="X", name="Y",
                       checknum="", refnum="")
    extrato = _extrato([txn])
    livro.confirmar_importacao_ofx(extrato.chave_conta(), [{
        "fitid": "f-reimport", "tipo": "despesa", "valor_centavos": 2000,
        "data": date(2026, 7, 5), "descricao": "Y", "categoria": "Outros",
        "natureza": None, "plano_conta_id": None, "centro_custo_id": None,
    }])

    itens = livro.pre_visualizar_importacao_ofx(extrato)
    assert itens[0]["ja_importado"] is True
    assert itens[0]["selecionado_padrao"] is False


def test_pre_visualizar_sinaliza_duplicata_provavel_como_sugestao(livro):
    """Um lançamento MANUAL (sem FITID em comum) com mesmo valor+data do OFX:
    vira sugestão de ignorar, não decisão automática — ja_importado continua
    False (o cliente PODE confirmar mesmo assim), só o padrão vem desmarcado."""
    livro.adicionar(Lancamento.criar(
        tipo="despesa", valor_reais="300.00", categoria="Servicos",
        descricao="Pagamento fornecedor", data=date(2026, 7, 3), origem="manual"))

    txn = OfxTransacao(fitid="f-dup", data=date(2026, 7, 3), valor_centavos=30000,
                       tipo="despesa", trntype="DEBIT", memo="PIX EMITIDO OUTRA IF",
                       name="Pagamento Pix ***.051.073-**", checknum="", refnum="")
    itens = livro.pre_visualizar_importacao_ofx(_extrato([txn]))
    item = itens[0]
    assert item["ja_importado"] is False
    assert len(item["duplicatas_provaveis"]) == 1
    assert item["duplicatas_provaveis"][0]["descricao"] == "Pagamento fornecedor"
    assert item["selecionado_padrao"] is False


def test_sugestao_por_contraparte_aprende_com_lancamento_ja_classificado(livro, pool):
    plano_servicos = _plano_id(pool, "1.1.02")
    lanc = livro.adicionar(Lancamento.criar(
        tipo="receita", valor_reais="1700.00", categoria="Vendas",
        descricao="Espaço Pelle Clínica Derma", data=date(2026, 7, 13),
        origem="manual", natureza="empresa"), forcar=True)
    # confirma a classificação contábil (é o que o endpoint de plano-conta faz)
    with pool.connection() as c:
        c.execute("update lancamentos set plano_conta_id=%s where id=%s",
                  (plano_servicos, lanc.id))
        c.commit()

    txn = OfxTransacao(fitid="f-contraparte", data=date(2026, 7, 27), valor_centavos=200000,
                       tipo="receita", trntype="CREDIT", memo="TRANSF.RECEBIDA - PIX SICOOB",
                       name="REM.: ESPACO PELLE CLINICA DERMA", checknum="", refnum="")
    itens = livro.pre_visualizar_importacao_ofx(_extrato([txn]))
    item = itens[0]
    assert item["categoria"] == "Vendas"
    assert item["plano_conta_id"] == plano_servicos
    assert item["confianca"] == "alta"
    assert "aprendido de 1" in item["motivo"]
