"""Liberar o pagamento de várias contas, no relatório de Contas a pagar.

Escolha do dono em 04/09/2026, depois de olhar o print da aba Empresa e
perguntar que caixa de marcar era aquela: *"deixa essa caixa aí no relatório
somente para a liberação do pagamento em contas a pagar, faz mais sentido?"*.

Faz, e por um motivo que eu não tinha visto: **é o relatório que mostra o que
decide**. Descrição, fornecedor, prazo e valor lado a lado — é olhando isso que
se responde "essa pode ser paga?". A aba Empresa é a tela de manutenção
(adicionar, editar, apagar), e a caixa que morava lá aparecia em 1 de 33 linhas
— a 32ª —, o que gerou a pergunta.

A fronteira que este teste guarda acima de tudo: **liberar não paga.** Autoriza.
O dinheiro só sai quando alguém dá baixa, e a baixa continua na aba Empresa. Se
essa fronteira borrar, um clique que a pessoa entende como "aprovei" vira
dinheiro saindo do caixa.

E guarda também as duas coisas que o desenho novo pode quebrar sem avisar:

  * a caixa só existe em quem AINDA espera liberação — oferecer caixa que não
    muda nada é a queixa original, de volta;
  * a coluna da caixa DESLOCA as colunas, e as larguras das elásticas são
    escritas por `nth-child`. Errar o deslocamento devolve o fornecedor de 107px
    do print de 04/09.
"""
import os
import re
from datetime import date, timedelta

import pytest
from psycopg_pool import ConnectionPool

import web.painel_relatorios as rel
from finance import empresa as emp
from web import portal as pt

HOJE = date.today()

_BASE_SQL = """
create table contas (id bigserial primary key, nome text);
create table pessoas (id bigserial primary key, nome text);
create table clientes (id bigserial primary key, dono_id bigint, pessoa_id bigint,
  nome text);
create table lancamentos (
  id bigserial primary key, conta_id bigint not null, tipo text not null,
  valor_centavos bigint not null, categoria text not null,
  descricao text not null default '', data date not null,
  origem text not null default 'manual', natureza text);
create table titulos (
  id bigserial primary key, conta_id bigint, tipo text, descricao text,
  contraparte text, valor_centavos bigint, vencimento date, status text,
  recorrente boolean default false, periodicidade text, valor_variavel boolean not null default false, acrescimo_centavos int not null default 0, lancamento_acrescimo_id bigint, categoria text, cobranca_link_url text,
  pago_em date, lancamento_id bigint, cliente_id bigint, criado_por bigint,
  aprovacao text not null default 'autorizado', aprovado_por bigint,
  aprovado_em timestamptz, aprovacao_motivo text,
  pago_sem_autorizacao boolean not null default false);
create table membros (id bigserial primary key, conta_id bigint,
  nome text, email text);
"""


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1,
                           open=True)
    dbname = "zaq_liberacao_relatorio_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=3, open=True,
                       kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_BASE_SQL)
        c.commit()
    yield p
    p.close()


@pytest.fixture
def conta(pool):
    with pool.connection() as c:
        c.execute("truncate contas, clientes, pessoas, lancamentos, titulos "
                  "restart identity")
        cid = c.execute("insert into contas (nome) values ('Prime Eventos') "
                        "returning id").fetchone()[0]
        c.commit()
    return cid


def _titulo(pool, conta_id, *, descricao="ZARB CONSULTORIA", valor=220000,
            tipo="pagar", aprovacao="autorizado", venc_em=-20):
    with pool.connection() as c:
        tid = c.execute(
            """insert into titulos (conta_id, tipo, descricao, contraparte,
                 valor_centavos, vencimento, status, categoria, aprovacao)
               values (%s,%s,%s,'FORNECEDOR',%s,%s,'aberto','Fornecedores',%s)
               returning id""",
            (conta_id, tipo, descricao, valor, HOJE + timedelta(days=venc_em),
             aprovacao)).fetchone()[0]
        c.commit()
    return tid


def _abertos(pool, conta_id, tipo="pagar"):
    return rel._dados_titulos_abertos(pool, conta_id, tipo)


# ═══════════════ a caixa aparece onde tem o que liberar ═══════════════
def test_sem_nada_esperando_nao_ha_caixa_nenhuma(pool, conta):
    """Coluna de caixa numa lista em que nada pode ser liberado é a queixa
    original de volta: caixa que não faz nada."""
    _titulo(pool, conta)
    assert "selecao" not in _abertos(pool, conta)


def test_com_conta_esperando_a_selecao_aparece_com_conta_e_soma(pool, conta):
    _titulo(pool, conta, descricao="LIBERADA")
    _titulo(pool, conta, descricao="ESPERA A", valor=150000, aprovacao="aguardando")
    _titulo(pool, conta, descricao="ESPERA B", valor=100000, aprovacao="aguardando")
    sel = _abertos(pool, conta)["selecao"]
    assert sel["n"] == 2
    assert sel["centavos"] == 250000, "o aviso soma só o que está esperando"
    assert sel["url"] == "/painel/relatorios/liberar"


def test_a_caixa_so_existe_na_linha_que_espera(pool, conta):
    _titulo(pool, conta, descricao="LIBERADA")
    _titulo(pool, conta, descricao="ESPERA", aprovacao="aguardando")
    por_desc = {r["descricao"]: r for r in _abertos(pool, conta)["linhas"]}
    assert por_desc["ESPERA"]["sel_id"]
    assert por_desc["LIBERADA"]["sel_id"] is None


def test_a_recusada_tambem_pode_ser_liberada(pool, conta):
    """Voltar atrás numa recusa é decisão do dono, e a caixa é onde ela cabe."""
    _titulo(pool, conta, descricao="RECUSADA", aprovacao="recusado")
    assert _abertos(pool, conta)["linhas"][0]["sel_id"]


def test_contas_a_receber_nunca_tem_caixa(pool, conta):
    """Liberação existe pra conta a PAGAR — ninguém autoriza dinheiro entrando."""
    _titulo(pool, conta, tipo="receber", aprovacao="aguardando")
    d = _abertos(pool, conta, "receber")
    assert "selecao" not in d
    assert d["linhas"][0]["sel_id"] is None


# ═══════════════ liberar não paga ═══════════════
def test_liberar_nao_muda_status_nem_lanca(pool, conta):
    """A fronteira que não pode borrar. Se um clique de "aprovei" virasse dinheiro
    saindo, seria o pior erro que esta tela poderia cometer."""
    tid = _titulo(pool, conta, aprovacao="aguardando")
    emp.decidir_aprovacao(pool, conta, [tid], "autorizado")
    with pool.connection() as c:
        assert c.execute("select status from titulos where id=%s",
                         (tid,)).fetchone()[0] == "aberto"
        assert c.execute("select count(*) from lancamentos").fetchone()[0] == 0


def test_a_tela_diz_que_liberar_nao_paga(pool, conta):
    """Escrito, não subentendido: quem lê "liberar" numa lista de contas entende
    "pagar" — foi assim que o dono leu a caixa antiga."""
    assert "liberar não paga" in pt._RELATORIOS
    assert "só sai quando" in pt._RELATORIOS
    js = pt._RELATORIOS[pt._RELATORIOS.index("id !== 'rel-lote'"):]
    assert "AUTORIZA o pagamento" in js and "não paga" in js, \
        "o confirm tem que repetir a fronteira na hora de gravar"


def test_liberar_o_que_ja_estava_liberado_nao_recarimba(pool, conta):
    """A marcação vem do navegador e pode incluir linha já decidida. Sem o filtro,
    `aprovado_em` de uma conta de semana passada viraria hoje."""
    velha = _titulo(pool, conta, descricao="JÁ LIBERADA")
    nova = _titulo(pool, conta, descricao="ESPERANDO", aprovacao="aguardando")
    with pool.connection() as c:
        c.execute("update titulos set aprovado_em = now() - interval '7 days' "
                  "where id=%s", (velha,))
        c.commit()
        antes = c.execute("select aprovado_em from titulos where id=%s",
                          (velha,)).fetchone()[0]
    assert emp.decidir_aprovacao(pool, conta, [velha, nova], "autorizado") == 1
    with pool.connection() as c:
        assert c.execute("select aprovado_em from titulos where id=%s",
                         (velha,)).fetchone()[0] == antes


# ═══════════════ quem pode ═══════════════
def test_so_quem_tem_gerir_libera():
    """`financeiro` entra na tela e concilia; liberar é `gerir`, que no modelo de
    papéis já é exclusivo do dono. A régua é a mesma função de capacidades que o
    portal usa — a pergunta "quem libera" não pode ter duas respostas."""
    class _Req:
        def __init__(self, papel):
            self.session = {"papel": papel}
    assert rel._pode_liberar(_Req("dono")) is True
    from contas import equipe as eq
    for papel in eq.CAPS:
        assert rel._pode_liberar(_Req(papel)) is bool(eq.caps_do_papel(papel).get("gerir"))


def test_a_rota_de_liberar_revalida_o_papel():
    """A tela esconde a caixa de quem não pode — mas esconder não é proteger: o
    POST vem do navegador."""
    fonte = open(rel.__file__, encoding="utf-8").read()
    corpo = fonte.split("def relatorios_liberar")[1].split("\n@router")[0]
    assert "_pode_ver(request)" in corpo, "faltou o gate de entrar na tela"
    assert "_pode_liberar(request)" in corpo, "faltou o gate de DECIDIR"
    assert "decidir_aprovacao" in corpo


def test_a_tela_esconde_a_caixa_de_quem_nao_libera():
    fonte = open(rel.__file__, encoding="utf-8").read()
    corpo = fonte.split("def painel_relatorios(")[1].split("\n@router")[0]
    assert 'dados.pop("selecao")' in corpo


# ═══════════════ a coluna nova desloca a tabela ═══════════════
def test_a_coluna_da_caixa_desloca_o_nth_child_das_elasticas():
    """A armadilha deste desenho. As larguras das duas elásticas são escritas por
    `nth-child`, contando as colunas. A caixa entra como PRIMEIRA célula da linha,
    então todas andam uma casa. Errar isso devolve o fornecedor de 107px do print
    de 04/09 — e sem quebrar teste nenhum, porque a tabela continua desenhando."""
    assert "loop.index + (1 if dados.selecao else 0)" in pt._RELATORIOS


def test_o_total_e_o_vazio_contam_a_coluna_a_mais():
    """Rodapé e linha de "nenhum registro" desalinham calados se não contarem."""
    assert '{% if dados.selecao %}<td class="rel-sel"></td>{% endif %}' in pt._RELATORIOS
    assert "(1 if dados.selecao else 0) }}\" class=\"mut\"" in pt._RELATORIOS


def test_a_caixa_se_liga_ao_form_por_atributo():
    """Formulário dentro de <table> o navegador reescreve. O `form=` é o que
    permite as caixas espalhadas pelas linhas pertencerem a um form de fora."""
    assert 'form="rel-lote"' in pt._RELATORIOS
    assert '<form method="post" action="{{ dados.selecao.url }}" id="rel-lote"></form>' \
        in pt._RELATORIOS


# ═══════════════ a barra ═══════════════
def test_a_barra_diz_o_total_em_dinheiro():
    """Contagem sozinha não denuncia uma linha marcada a mais; o valor sim."""
    assert 'id="rel-lote-soma"' in pt._RELATORIOS
    assert "brl(soma)" in pt._RELATORIOS


def test_marcar_todas_respeita_o_filtro_da_tela():
    """Com o filtro ligado, marcar o que está escondido é gravar no escuro."""
    js = pt._RELATORIOS[pt._RELATORIOS.index("rel-todas'"):]
    assert "tr.style.display !== 'none'" in js


@pytest.mark.parametrize("centavos,texto", [
    (0, "R$ 0,00"), (1, "R$ 0,01"), (220000, "R$ 2.200,00"),
    (2939042, "R$ 29.390,42"), (100000000, "R$ 1.000.000,00"),
])
def test_o_dinheiro_da_barra_bate_com_o_do_python(centavos, texto):
    """A barra formata em JS e o resto da tela em Python. Divergir aqui é a tela
    dizendo dois valores pro mesmo dinheiro."""
    import json
    import shutil
    import subprocess
    if not shutil.which("node"):
        pytest.skip("sem node no ambiente")
    i = pt._RELATORIOS.index("function brl(c)")
    js = pt._RELATORIOS[i:pt._RELATORIOS.index("function caixas()")]
    r = subprocess.run(["node", "-e", js + f"console.log(brl({centavos}))"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == texto == rel._brl(centavos)


# ═══════════════ o bloco tem que RODAR, não só existir ═══════════════
def _scripts_do_template() -> dict:
    """Os <script> da tela que dá pra analisar crus (sem Jinja no meio)."""
    fora = {}
    for bloco in re.findall(r"<script>(.*?)</script>", pt._RELATORIOS, re.S):
        if "{{" in bloco or "{%" in bloco:
            continue          # tem Jinja: só faz sentido depois de renderizar
        chave = "barra" if "function caixas()" in bloco else f"outro{len(fora)}"
        fora[chave] = bloco
    return fora


def test_todo_javascript_da_tela_e_valido():
    """**Este teste nasceu de um bug que os outros deixaram passar pro ar — duas
    vezes.**

    O bloco vive dentro de uma string Python. Um `\\n` cru na fonte vira quebra de
    linha DE VERDADE no HTML, e string JavaScript não atravessa linha: em
    04/09/2026 o `confirm` tinha `'?\\n\\n'`, o navegador levantou
    `Uncaught SyntaxError` e o bloco INTEIRO morreu antes de definir nada. O dono
    marcou duas contas e a barra continuou dizendo "0 marcadas · R$ 0,00".

    A primeira versão deste teste recortava o bloco por `"(function () {"` — e
    casava com OUTRO IIFE do template, que sempre esteve válido. Ele passava
    verde enquanto o bug seguia no ar. Por isso agora checa TODOS os blocos, e
    exige que o da barra esteja entre eles: teste que recorta tem que provar que
    recortou a coisa certa.
    """
    import shutil
    import subprocess
    if not shutil.which("node"):
        pytest.skip("sem node no ambiente")
    scripts = _scripts_do_template()
    assert "barra" in scripts, \
        "não achei o bloco da barra — o recorte está olhando pra outro lugar"
    for nome, js in scripts.items():
        r = subprocess.run(["node", "--check", "-"], input=js,
                           capture_output=True, text=True)
        assert r.returncode == 0, (
            f"o bloco <script> '{nome}' não é JavaScript válido — no navegador ele "
            f"morre inteiro e calado:\n{r.stderr}")


def test_nao_ha_quebra_de_linha_crua_dentro_de_string_no_bloco():
    """A causa exata, fixada por si — pra quem editar o bloco ver o porquê antes
    de repetir. Roda sem node, então vale também onde o de cima é pulado."""
    js = _scripts_do_template()["barra"]
    for n, linha in enumerate(js.split("\n"), 1):
        assert linha.count("'") % 2 == 0, (
            f"linha {n} do bloco abre aspas e não fecha — provável `\\n` cru "
            f"que virou quebra de linha:\n  {linha.strip()}")


def test_a_barra_some_quando_nada_esta_marcado():
    """O atributo `hidden` sozinho PERDE pro `display:flex` da classe — a folha do
    navegador tem menos peso que uma regra de classe. Sem a regra explícita, a
    barra fica visível com "0 marcadas" pra sempre, que foi o que o dono viu."""
    assert ".rel-lote-barra[hidden]{display:none}" in pt._RELATORIOS
    css = pt._RELATORIOS[pt._RELATORIOS.index(".rel-lote-barra[hidden]"):]
    assert css.index("[hidden]") < css.index("position:sticky"), \
        "a regra do hidden tem que vir antes da que dá display à barra"
