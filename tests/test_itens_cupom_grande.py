"""Cupom GRANDE / em VARIAS FOTOS: salvar os itens em LOTES sem perder nada.

Contexto (o bug que motivou): cupom comprido (dezenas de itens) estourava o
limite de tokens no meio da chamada de registrar_itens_cupom -> a resposta vinha
CORTADA e NADA era salvo. E, mesmo quando o modelo tentava dividir, o segundo
lote batia na trava anti-duplicata (registrar_itens devolvia -1) porque o
lancamento "ja' tinha itens". Resultado: lancamento criado, ZERO itens salvos.

A correcao: modo ANEXAR em registrar_itens (anexar=True), que ACRESCENTA so' os
itens ainda nao salvos daquele mesmo cupom (dedupe por codigo, ou por
descricao+valor_total). Assim da' pra salvar em lotes e mandar fotos que se
sobrepoem sem duplicar.

Roda com banco de TESTE separado (ver tests/conftest.py):
    export TEST_DATABASE_URL="postgresql://.../banco_de_teste"
    pytest
"""
import os
from datetime import date

import pytest
from psycopg_pool import ConnectionPool

from db.conexao import init_schema
from finance.livro_caixa import LivroCaixa, _chave_dedupe_item
from finance.models import Lancamento, Tipo


# --- dedupe key: PURO, nao toca banco -----------------------------------------

def test_chave_dedupe_prefere_codigo():
    # mesmo codigo de barras + mesmo total => mesma chave (usa o codigo, ignora
    # variacao de descricao). Isso e' reenvio da MESMA linha.
    a = _chave_dedupe_item("REFRI COLA 2L", 799, "7894900011517")
    b = _chave_dedupe_item("Refrigerante Coca 2L", 799, "7894900011517")
    assert a == b == ("cod", "7894900011517", 799)


def test_chave_dedupe_mesmo_codigo_totais_diferentes_sao_distintos():
    # MESMO produto (EAN igual) em DUAS linhas do cupom com quantidades/valores
    # diferentes (ex: Sprite 1un a 3,09 e Sprite 5un a 15,45) => itens DISTINTOS,
    # os dois tem que ser salvos. O total no key evita perder a 2a linha.
    a = _chave_dedupe_item("Sprite", 309, "7894900060010")
    b = _chave_dedupe_item("Sprite", 1545, "7894900060010")
    assert a != b


def test_chave_dedupe_sem_codigo_usa_descricao_normalizada_e_total():
    # sem codigo: casa por descricao normalizada (sem acento/caixa) + total pago
    a = _chave_dedupe_item("Feijão Verde KG", 1592, None)
    b = _chave_dedupe_item("feijao verde kg", 1592, "")
    assert a == b == ("desc", "feijao verde kg", 1592)
    # total diferente => item diferente (nao dedupa)
    c = _chave_dedupe_item("Feijão Verde KG", 1516, None)
    assert c != a


def test_chave_dedupe_codigo_curto_cai_pra_descricao():
    # codigo interno curto (< 8 digitos, ex: hortifruti "834") nao serve de chave
    k = _chave_dedupe_item("Feijao", 1592, "834")
    assert k[0] == "desc"


# --- append de verdade no banco -----------------------------------------------

_MIGR = (
    # colunas de lancamentos que LivroCaixa.adicionar preenche (migracoes 018/057)
    "alter table lancamentos add column if not exists chave varchar(44)",
    "alter table lancamentos add column if not exists natureza text",
    # 016/019: colunas usadas por registrar_itens
    "alter table itens_lancamento add column if not exists unidade text not null default 'UN'",
    "alter table itens_lancamento add column if not exists codigo text",
)


@pytest.fixture(scope="module")
def pool():
    test_db_url = os.environ["TEST_DATABASE_URL"]  # garantido pela trava do conftest
    p = ConnectionPool(test_db_url, min_size=1, max_size=4, open=True,
                       kwargs={"prepare_threshold": None})
    init_schema(p)  # idempotente
    for ddl in _MIGR:  # colunas que as migracoes 016/018/019 adicionam
        with p.connection() as c:
            c.execute(ddl)
            c.commit()
    yield p
    p.close()


@pytest.fixture()
def livro(pool):
    with pool.connection() as c:
        cid = c.execute(
            "insert into contas (tipo, nome) values ('pf', 'Teste Cupom') returning id"
        ).fetchone()[0]
        c.commit()
    return LivroCaixa(pool, cid)


def _lanc(livro) -> int:
    lanc = livro.adicionar(Lancamento(tipo=Tipo.DESPESA, valor_centavos=131530,
                                      categoria="Mercado", descricao="Compra mercado",
                                      data=date.today(), origem="foto"), forcar=True)
    return lanc.id


def _item(desc, total, cod=None, un="UN", qtd=1):
    return {"descricao": desc, "quantidade": qtd, "valor_unitario_centavos": total,
            "valor_total_centavos": total, "unidade": un, "codigo": cod}


def _qtd_itens(livro, lanc_id) -> int:
    with livro.pool.connection() as c:
        return c.execute("select count(*) from itens_lancamento where lancamento_id=%s",
                         (lanc_id,)).fetchone()[0]


def test_lotes_acumulam_no_mesmo_cupom(livro):
    lanc_id = _lanc(livro)
    # 1o lote (sem anexar): cupom estava vazio -> salva os 2
    n1 = livro.registrar_itens(lanc_id, [_item("Arroz", 2599, "1"),
                                         _item("Feijao", 1592, "2")])
    assert n1 == 2
    # 2o lote com anexar=True: ACRESCENTA (nao recusa como antes)
    n2 = livro.registrar_itens(lanc_id, [_item("Cafe", 1499, "3"),
                                         _item("Leite", 3189, "4")], anexar=True)
    assert n2 == 2
    assert _qtd_itens(livro, lanc_id) == 4


def test_sem_anexar_ainda_recusa_para_nao_duplicar(livro):
    lanc_id = _lanc(livro)
    assert livro.registrar_itens(lanc_id, [_item("Arroz", 2599)]) == 1
    # sem anexar, um 2o save no mesmo cupom continua sendo recusado (-1)
    assert livro.registrar_itens(lanc_id, [_item("Feijao", 1592)]) == -1
    assert _qtd_itens(livro, lanc_id) == 1


def test_anexar_dedupe_fotos_sobrepostas(livro):
    lanc_id = _lanc(livro)
    # foto 1: itens 1-3
    livro.registrar_itens(lanc_id, [_item("Arroz", 2599, "111"),
                                    _item("Feijao", 1592, "222"),
                                    _item("Cafe", 1499, "333")], anexar=True)
    # foto 2 SE SOBREPOE (repete Cafe) e traz um novo (Leite)
    n = livro.registrar_itens(lanc_id, [_item("Cafe", 1499, "333"),
                                        _item("Leite", 3189, "444")], anexar=True)
    assert n == 1                       # so' o Leite entrou; Cafe foi deduplicado
    assert _qtd_itens(livro, lanc_id) == 4


def test_anexar_lote_inteiro_repetido_retorna_menos_dois(livro):
    lanc_id = _lanc(livro)
    livro.registrar_itens(lanc_id, [_item("Arroz", 2599, "1"),
                                    _item("Feijao", 1592, "2")], anexar=True)
    # reenvio do MESMO lote: nada novo -> -2 (sinaliza "ja' estava salvo")
    n = livro.registrar_itens(lanc_id, [_item("Arroz", 2599, "1"),
                                        _item("Feijao", 1592, "2")], anexar=True)
    assert n == -2
    assert _qtd_itens(livro, lanc_id) == 2


def test_anexar_preserva_itens_repetidos_no_mesmo_lote(livro):
    # duas "COXA TEMPERADA 25,49" na MESMA compra sao itens legitimos distintos:
    # o dedupe so' compara com o que JA' esta' salvo, nao entre si dentro do lote.
    lanc_id = _lanc(livro)
    n = livro.registrar_itens(lanc_id, [_item("Coxa Temperada", 2549, "999"),
                                        _item("Coxa Temperada", 2549, "999")],
                              anexar=True)
    assert n == 2
    assert _qtd_itens(livro, lanc_id) == 2
