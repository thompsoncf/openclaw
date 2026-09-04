""""Já foi paga" saiu do relatório e foi pra aba Empresa.

Escolha do dono em 04/09/2026, olhando o relatório de Contas a pagar: *"a caixa
que fica no final pra marcar a conta como paga pode remover, pois tudo vai ficar
lá dentro da Empresa como combinado"*. Ele escolheu a opção A do mockup em
`docs/mockups/relatorio_botao_pago.html`: tirar o botão E levar a função junto.

**O botão não podia simplesmente sumir**, e é isso que este teste guarda. Ele e o
"dar baixa" da Empresa não fazem a mesma coisa:

    dar baixa   →  CRIA um lançamento (o dinheiro sai agora)
    já foi paga →  AMARRA um lançamento que já existe

Na Prime isso é concreto: o título ZARB de R$ 2.200,00 está aberto e o Pix de
R$ 2.200,00 de 10/08 já está no caixa, vindo do extrato. Fechá-lo pelo "dar
baixa" lançaria a despesa uma SEGUNDA vez, no livro-caixa e no DRE. Tirar o botão
sem levar a função deixaria essa conta — e toda conta paga por Pix ou comprovante
— sem jeito certo de ser fechada.

O que o teste protege:

  * o relatório não tem mais coluna de ação, e o AVISO "Talvez paga" continua lá
    (é informação, não ação);
  * a régua que acha o candidato é UMA só, em `finance/empresa.py`. Duas telas
    perguntam a mesma coisa agora: se cada uma tivesse a sua, uma avisaria o que
    a outra não deixa fechar;
  * conciliar continua sem lançar dinheiro novo;
  * o botão só aparece com candidato ÚNICO;
  * o texto do confirm não vai por `tojson` num atributo — foi assim que o
    atributo saiu quebrado no #598, e a frase carrega a descrição do título, que
    é texto de usuário.
"""
import os
import re
from datetime import date, timedelta

import pytest
from psycopg_pool import ConnectionPool

from finance import empresa as emp
import web.painel_relatorios as rel
from web import portal as pt

HOJE = date.today()

# BANCO PRÓPRIO e esquema escrito à mão, como o test_conferir_texto faz — e não o
# `openclaw_test` compartilhado com `init_schema`. A primeira versão deste arquivo
# escolhia quatro migrações a dedo: passava aqui, onde o banco descartável já
# estava populado por rodadas anteriores, e quebrava no CI, que começa limpo toda
# vez. Foi o vermelho do #616.
#
# `listar_titulos` faz LEFT JOIN em `clientes`, `pessoas` e `membros`, e
# `dar_baixa_titulo` lança no livro-caixa — então `lancamentos` precisa das
# colunas que o INSERT de verdade usa (membro_id, pagamento, forma_pagamento,
# comprovante, chave, plano_conta_id, centro_custo_id). Estão todas aqui: um
# esquema resumido demais daria um teste verde contra uma tabela que não existe
# em lugar nenhum.
_BASE_SQL = """
create table contas (id bigserial primary key, tipo text, nome text);
create table pessoas (id bigserial primary key, nome text);
create table membros (id bigserial primary key, conta_id bigint, nome text,
  email text);
create table clientes (id bigserial primary key, dono_id bigint, pessoa_id bigint,
  nome text);
create table lancamentos (
  id bigserial primary key, conta_id bigint not null, membro_id bigint,
  tipo text not null, valor_centavos bigint not null, categoria text not null,
  descricao text not null default '', data date not null,
  pagamento text, forma_pagamento text, origem text not null default 'manual',
  comprovante text, chave text, natureza text,
  plano_conta_id bigint, centro_custo_id bigint, vendedor_id bigint);
create table titulos (
  id bigserial primary key, conta_id bigint, tipo text, descricao text,
  contraparte text, valor_centavos bigint, vencimento date, status text,
  recorrente boolean default false, periodicidade text, valor_variavel boolean not null default false, acrescimo_centavos int not null default 0, lancamento_acrescimo_id bigint, categoria text, cobranca_link_url text,
  pago_em date, lancamento_id bigint, cliente_id bigint, criado_por bigint,
  aprovacao text not null default 'autorizado', aprovado_por bigint,
  aprovado_em timestamptz, aprovacao_motivo text,
  pago_sem_autorizacao boolean not null default false);
"""


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1,
                           open=True)
    dbname = "zaq_conciliar_empresa_test"
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
        cid = c.execute("insert into contas (tipo, nome) values ('pj','Prime Concilia')"
                        " returning id").fetchone()[0]
        c.commit()
    return cid


def _titulo(pool, conta_id, *, descricao="ZARB CONSULTORIA", valor=220000,
            tipo="pagar", venc_em=-20):
    with pool.connection() as c:
        tid = c.execute(
            """insert into titulos (conta_id, tipo, descricao, contraparte,
                 valor_centavos, vencimento, status, categoria)
               values (%s,%s,%s,'ZARB ASSESSORIA',%s,%s,'aberto','Fornecedores')
               returning id""",
            (conta_id, tipo, descricao, valor, HOJE + timedelta(days=venc_em)),
        ).fetchone()[0]
        c.commit()
    return tid


def _pagamento(pool, conta_id, *, valor=220000, dias=-25, origem="extrato",
               descricao="Pagamento Pix 58.608.090 0001-88", tipo="despesa"):
    with pool.connection() as c:
        lid = c.execute(
            """insert into lancamentos (conta_id, tipo, valor_centavos, categoria,
                 descricao, data, origem, natureza)
               values (%s,%s,%s,'Outros',%s,%s,%s,'empresa') returning id""",
            (conta_id, tipo, valor, descricao, HOJE + timedelta(days=dias), origem),
        ).fetchone()[0]
        c.commit()
    return lid


def _lancamentos(pool, conta_id):
    with pool.connection() as c:
        return c.execute("select count(*) from lancamentos where conta_id=%s",
                         (conta_id,)).fetchone()[0]


# ═══════════════ o relatório ficou só com o aviso ═══════════════
def test_o_relatorio_nao_tem_mais_coluna_de_acao(pool, conta):
    _titulo(pool, conta)
    _pagamento(pool, conta)
    d = rel._dados_titulos_abertos(pool, conta, "pagar")
    assert "acao" not in d, "a coluna do ✓ voltou pro relatório"
    assert "acao_post" not in d["linhas"][0]


def test_mas_o_aviso_continua_no_relatorio(pool, conta):
    """Ele é informação, não ação — e é o que manda a pessoa ir olhar a conta."""
    _titulo(pool, conta)
    _pagamento(pool, conta)
    linha = rel._dados_titulos_abertos(pool, conta, "pagar")["linhas"][0]
    assert linha["talvez"].startswith("Talvez paga · ")


def test_a_rota_de_conciliar_saiu_do_relatorio():
    fonte = open(rel.__file__, encoding="utf-8").read()
    assert '@router.post("/painel/relatorios/conciliar")' not in fonte
    assert '@router.post("/painel/relatorios/desfazer-conciliacao")' in fonte, \
        "o desfazer mora em Contas pagas, que é onde a conciliação aparece depois"


def test_a_rota_de_conciliar_chegou_na_empresa():
    fonte = open(pt.__file__, encoding="utf-8").read()
    assert '@router.post("/painel/empresa/titulo/{titulo_id}/conciliar")' in fonte
    corpo = fonte.split("def empresa_titulo_conciliar")[1].split("\n@router")[0]
    assert "emp.conciliar_titulo" in corpo
    assert "_guard_pj(request)" in corpo, "sem o portão do módulo PJ"


# ═══════════════ uma régua só pras duas telas ═══════════════
def test_a_regua_do_candidato_mora_no_finance():
    """Duas telas perguntam a mesma coisa agora. Se cada uma tivesse a sua, uma
    avisaria "talvez paga" no que a outra não deixasse fechar."""
    assert callable(emp.pagamentos_candidatos)
    da_tela = open(rel.__file__, encoding="utf-8").read()
    assert "def _pagamentos_candidatos" not in da_tela
    assert "emp.pagamentos_candidatos" in da_tela, "a tela tem que CHAMAR a régua"
    do_portal = open(pt.__file__, encoding="utf-8").read()
    assert "emp.pagamentos_candidatos" in do_portal


def test_as_duas_telas_veem_o_mesmo_candidato(pool, conta):
    tid = _titulo(pool, conta)
    lid = _pagamento(pool, conta)
    do_relatorio = rel._dados_titulos_abertos(pool, conta, "pagar")["linhas"][0]
    achado = emp.pagamentos_candidatos(
        pool, conta, emp.listar_titulos(pool, conta, status="aberto", tipo="pagar"),
        "pagar")
    assert do_relatorio["talvez"], "o relatório avisa"
    assert achado[tid]["lancamento_id"] == lid, "e a Empresa oferece o MESMO"


# ═══════════════ conciliar não é dar baixa ═══════════════
def test_conciliar_fecha_a_conta_sem_lancar_dinheiro_novo(pool, conta):
    """A razão de a função ter vindo junto do botão. O ZARB de R$ 2.200 já saiu
    do caixa por Pix; fechá-lo pelo "dar baixa" lançaria R$ 2.200 outra vez."""
    tid = _titulo(pool, conta)
    lid = _pagamento(pool, conta)
    antes = _lancamentos(pool, conta)
    r = emp.conciliar_titulo(pool, conta, tid, lid)
    assert r["ok"] is True, r
    with pool.connection() as c:
        assert c.execute("select status from titulos where id=%s",
                         (tid,)).fetchone()[0] == "pago"
    assert _lancamentos(pool, conta) == antes, \
        "conciliar lançou dinheiro novo — é a despesa contada duas vezes"


def test_dar_baixa_no_mesmo_caso_lancaria_de_novo(pool, conta):
    """O contraste que justifica os dois botões existirem lado a lado. Não é o
    "dar baixa" que está errado — é usá-lo numa conta que já foi paga."""
    tid = _titulo(pool, conta)
    _pagamento(pool, conta)
    antes = _lancamentos(pool, conta)
    emp.dar_baixa_titulo(pool, conta, tid)
    assert _lancamentos(pool, conta) == antes + 1


# ═══════════════ o botão na tela ═══════════════
def test_o_botao_so_aparece_com_candidato_unico():
    """Com dois pagamentos iguais por perto a régua conta e não escolhe. Botão ali
    seria pedir pro dono confirmar um chute que a própria tela não soube dar."""
    fonte = open(pt.__file__, encoding="utf-8").read()
    corpo = fonte.split('titulos = emp.listar_titulos(pool, conta[0], status="aberto")')[1]
    corpo = corpo[:corpo.index("titulos_pagos")]
    assert '_c["n"] == 1' in corpo
    assert '_t["conciliar"] = None' in corpo, "sem candidato, sem botão"


def test_o_confirm_nao_vai_por_tojson_no_atributo():
    """A frase carrega a DESCRIÇÃO do título, que é texto de usuário. O tojson do
    Jinja não escapa aspas — foi assim que o atributo saiu quebrado no #598."""
    bloco = pt._EMPRESA[pt._EMPRESA.index("/conciliar\""):]
    bloco = bloco[:bloco.index("</form>")]
    assert "tojson" not in bloco
    assert 'data-confirmar="{{ t.conciliar.confirmar|e }}"' in bloco


def test_a_empresa_tem_o_ouvinte_que_le_o_data_confirmar():
    """Sem ele o `data-confirmar` é um atributo decorativo e o clique grava
    direto, sem perguntar."""
    assert "form[data-confirmar]" in pt._EMPRESA
    assert "confirm(f.getAttribute('data-confirmar'))" in pt._EMPRESA


def test_o_confirm_explica_a_diferenca_pro_dar_baixa():
    """As duas ações ficam lado a lado e parecem a mesma. Se o texto não separar,
    o clique errado dobra a despesa."""
    fonte = open(pt.__file__, encoding="utf-8").read()
    corpo = fonte.split('_t["conciliar"] = {')[1].split("}\n")[0]
    assert "Nenhum dinheiro novo é lançado" in corpo
    assert "dar baixa" in corpo


def test_o_javascript_da_empresa_e_valido():
    """Mesma régua do relatório: o bloco vive dentro de uma string Python, e uma
    barra invertida solta o mata inteiro e calado."""
    import shutil
    import subprocess
    if not shutil.which("node"):
        pytest.skip("sem node no ambiente")
    for js in re.findall(r"<script>(.*?)</script>", pt._EMPRESA, re.S):
        if "{{" in js or "{%" in js:
            continue
        r = subprocess.run(["node", "--check", "-"], input=js,
                           capture_output=True, text=True)
        assert r.returncode == 0, f"bloco inválido:\n{r.stderr}"
