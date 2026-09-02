"""A trava anti-duplicidade engolia folha de prestador — e o agente dizia "✅".

O que o cliente viu em 01/09/2026, na Prime Eventos (conta 34):

    17:07:30  comprovante do Pedro Yan, R$ 1.500  ->  gravado (id 824)
    17:08:xx  comprovante do Thiago,    R$ 1.500  ->  ENGOLIDO
              e o agente respondeu "Registrado! ✅ ... Thiago ... R$ 1.500,00"

Trinta e oito segundos de diferença. Mesmo tipo, mesmo valor, mesma data — e a
trava `_lancamento_recente_igual` (janela de 10 min) tratou o segundo como
re-registro acidental do primeiro. O dinheiro sumiu com um visto de confirmação
em cima, e o dono só descobriu quando o prestador cobrou.

**Isso não é caso raro nessa empresa, é a folha dela.** Os títulos dizem:

    1 QUINZENA SETEMBRO/26 THIAGO     R$ 1.500   venc 15/09
    1 QUINZENA SETEMBRO/26 PEDRO YAN  R$ 1.500   venc 15/09
    2 QUINZENA SETEMBRO/26 PEDRO YAN  R$ 1.500   venc 30/09
    2 QUINZENA SETEMBRO/26 THIAGO     R$ 1.500   venc 30/09

Dois prestadores, mesmo valor, mesma quinzena. Em agosto os dois entraram porque
vieram do EXTRATO (ids 672 e 673, criados com 0,5s de diferença), e o extrato usa
`forcar=True`. Em setembro o vendedor fotografou, e o caminho da foto é o único
que passa pela trava.

São dois consertos, e eles se completam:

  1. a régua da trava passa a olhar a DESCRIÇÃO, o que tira o falso positivo da
     folha sem perder o caso que ela existe pra pegar;
  2. quando a trava ainda assim disparar, o agente PERGUNTA em vez de confirmar —
     porque nenhuma régua acerta sempre, e o modo de falha que não pode voltar é
     o silencioso.

O que este teste protege:

  * **nomes diferentes não são duplicata** — o caso Pedro Yan × Thiago;
  * **o sufixo reescrito continua sendo** — "Almoço no Assaí" e "Almoço no Assaí
    Banco do Brasil" são o mesmo comprovante, e era o que a régua antiga
    protegia. A comparação é por continência, não por igualdade;
  * **acento e caixa não separam** o que é a mesma coisa;
  * **descrição vazia mantém o comportamento antigo** (bloqueia): sem texto não
    dá pra distinguir, e barrar de menos duplicaria dinheiro;
  * **o agente não diz "registrada" quando não registrou**;
  * **o `forcar` existe de verdade, e está DECLARADO.** A mensagem manda o agente
    chamar de novo com `forcar: true` — e isso exige duas coisas que faltavam: o
    parâmetro ser lido no `lancar` e estar no schema da ferramenta. Sem qualquer
    uma das duas, o segundo pagamento bateria na trava pra sempre e a saída que a
    mensagem oferece seria uma mentira.
"""
import os
from datetime import date

import pytest
from psycopg_pool import ConnectionPool

from finance.livro_caixa import LivroCaixa, descricoes_sao_a_mesma
from finance.models import Lancamento, Tipo

_BASE_SQL = """
create table contas (id bigserial primary key, nome text);
create table membros (id bigserial primary key, conta_id bigint, nome text);
create table lancamentos (
  id bigserial primary key, conta_id bigint not null, membro_id bigint,
  cliente_id bigint, plano_conta_id bigint, centro_custo_id bigint,
  tipo text not null, valor_centavos bigint not null, categoria text not null,
  descricao text not null default '', data date not null,
  pagamento text not null default '', forma_pagamento text not null default '',
  origem text not null default 'manual', comprovante text not null default '',
  chave text, natureza text, criado_em timestamptz not null default now());
create table itens_lancamento (
  id bigserial primary key, lancamento_id bigint, descricao text,
  quantidade numeric, valor_unitario_centavos bigint,
  valor_total_centavos bigint, unidade text, codigo text);
"""

HOJE = date.today()


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_dedup_folha_test"
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
        c.execute("truncate contas, membros, lancamentos, itens_lancamento restart identity")
        cid = c.execute("insert into contas (nome) values ('Prime Eventos') "
                        "returning id").fetchone()[0]
        c.commit()
    return cid


def _desp(descricao, valor=150000, data=None):
    return Lancamento(tipo=Tipo.DESPESA, valor_centavos=valor, categoria="Servicos",
                      descricao=descricao, data=data or HOJE, origem="foto",
                      natureza="empresa")


def _quantos(pool, conta_id):
    with pool.connection() as c:
        return c.execute("select count(*) from lancamentos where conta_id=%s",
                         (conta_id,)).fetchone()[0]


# ── o caso da Prime, ponta a ponta ───────────────────────────────────────────
def test_dois_prestadores_mesmo_valor_no_mesmo_dia_entram_os_dois(pool, conta):
    """O caso literal de 01/09: Pedro Yan e Thiago, R$ 1.500 cada, 38 segundos de
    diferença. Antes o segundo era engolido."""
    livro = LivroCaixa(pool, conta)
    a = livro.adicionar(_desp("Serviço prestado - Pedro Yan Mendes Valenca Ximenes "
                              "- 2ª quinzena ago/26"))
    b = livro.adicionar(_desp("Serviço prestado - Thiago Cesar Borges Pinheiro "
                              "- 2ª quinzena ago/26"))
    assert a.duplicado is False
    assert b.duplicado is False, "folha de prestador não é duplicata"
    assert a.id != b.id
    assert _quantos(pool, conta) == 2


def test_o_mesmo_comprovante_duas_vezes_continua_barrado(pool, conta):
    """O que a trava existe pra pegar: a mesma foto chegando de novo na rajada."""
    livro = LivroCaixa(pool, conta)
    a = livro.adicionar(_desp("Serviço prestado - Thiago Cesar Borges Pinheiro"))
    b = livro.adicionar(_desp("Serviço prestado - Thiago Cesar Borges Pinheiro"))
    assert b.duplicado is True
    assert b.id == a.id, "devolve o que já existia, não cria outro"
    assert _quantos(pool, conta) == 1


def test_o_sufixo_reescrito_continua_sendo_duplicata(pool, conta):
    """O caso que a régua antiga protegia e que não podia se perder: o agente
    reescreve o fim da descrição do MESMO comprovante."""
    livro = LivroCaixa(pool, conta)
    livro.adicionar(_desp("Almoço no Assaí"))
    b = livro.adicionar(_desp("Almoço no Assaí Banco do Brasil"))
    assert b.duplicado is True
    assert _quantos(pool, conta) == 1


# ── a régua da descrição, isolada ────────────────────────────────────────────
@pytest.mark.parametrize("a,b,mesma", [
    # o caso da folha
    ("Serviço prestado - Pedro Yan Mendes - 2ª quinzena ago/26",
     "Serviço prestado - Thiago Cesar Borges - 2ª quinzena ago/26", False),
    # o caso do sufixo reescrito
    ("Almoço no Assaí", "Almoço no Assaí Banco do Brasil", True),
    ("Almoço no Assaí Banco do Brasil", "Almoço no Assaí", True),
    # acento e caixa não separam o que é igual
    ("Servico no Assai", "Serviço no Assaí", True),
    ("PAGAMENTO PIX", "pagamento pix", True),
    # pontuação e espaço a mais também não
    ("Serviço  prestado -  Thiago", "Servico prestado - Thiago", True),
    # sem texto não dá pra distinguir: mantém o comportamento antigo
    ("", "qualquer coisa", True),
    ("qualquer coisa", "", True),
    ("", "", True),
    ("   ", "Thiago", True),
    (None, "Thiago", True),
    # nomes diferentes de verdade
    ("Diária Herlane", "Diária Beto", False),
    ("INSS julho/2026", "FGTS julho/2026", False),
])
def test_a_regua_da_descricao(a, b, mesma):
    assert descricoes_sao_a_mesma(a, b) is mesma


def test_a_regua_e_simetrica():
    """Qual dos dois chegou primeiro não pode mudar a resposta."""
    pares = [("Almoço no Assaí", "Almoço no Assaí Banco do Brasil"),
             ("Pedro Yan", "Thiago"), ("", "x"), ("abc", "abc")]
    for a, b in pares:
        assert descricoes_sao_a_mesma(a, b) is descricoes_sao_a_mesma(b, a)


# ── o resto da trava não pode ter afrouxado ──────────────────────────────────
def test_valor_diferente_nunca_foi_duplicata(pool, conta):
    livro = LivroCaixa(pool, conta)
    livro.adicionar(_desp("Serviço prestado - Thiago", valor=150000))
    b = livro.adicionar(_desp("Serviço prestado - Thiago", valor=150001))
    assert b.duplicado is False
    assert _quantos(pool, conta) == 2


def test_data_diferente_nunca_foi_duplicata(pool, conta):
    livro = LivroCaixa(pool, conta)
    livro.adicionar(_desp("Serviço prestado - Thiago", data=date(2026, 9, 1)))
    b = livro.adicionar(_desp("Serviço prestado - Thiago", data=date(2026, 9, 2)))
    assert b.duplicado is False
    assert _quantos(pool, conta) == 2


def test_forcar_passa_por_cima(pool, conta):
    """A porta de saída: dois pagamentos com a MESMA descrição existem, e quem
    sabe disso é quem mandou os dois comprovantes."""
    livro = LivroCaixa(pool, conta)
    livro.adicionar(_desp("Serviço prestado - Thiago"))
    b = livro.adicionar(_desp("Serviço prestado - Thiago"), forcar=True)
    assert b.duplicado is False
    assert _quantos(pool, conta) == 2


def test_a_conta_do_vizinho_nao_entra_na_conta(pool, conta):
    with pool.connection() as c:
        outra = c.execute("insert into contas (nome) values ('Outra') "
                          "returning id").fetchone()[0]
        c.commit()
    LivroCaixa(pool, outra).adicionar(_desp("Serviço prestado - Thiago"))
    b = LivroCaixa(pool, conta).adicionar(_desp("Serviço prestado - Thiago"))
    assert b.duplicado is False


# ── o agente para de confirmar o que não gravou ──────────────────────────────
def _tools(pool, conta_id):
    from finance import tools as ft
    return {f.nome: f.executar
            for f in ft.construir_ferramentas(LivroCaixa(pool, conta_id))}


def _lancar(pool, conta_id, descricao, **extra):
    fs = _tools(pool, conta_id)
    entrada = {"valor": 1500.00, "categoria": "Servicos", "descricao": descricao,
               "data": HOJE.strftime("%d/%m/%Y"), "natureza": "empresa",
               "forma_pagamento": "pix", **extra}
    return fs["lancar_despesa"](entrada)


def test_o_agente_avisa_em_vez_de_confirmar(pool, conta):
    """O pedaço mais grave do incidente: o ✅ em cima do dinheiro que sumiu."""
    primeira = _lancar(pool, conta, "Serviço prestado - Thiago Cesar Borges")
    assert "registrada" in primeira.lower()

    segunda = _lancar(pool, conta, "Serviço prestado - Thiago Cesar Borges")
    assert "NAO registrei" in segunda, "não pode dizer que registrou"
    assert "forcar: true" in segunda, "tem que dizer como seguir"
    assert "Thiago" in segunda, "tem que dizer QUAL lançamento já existe"
    assert _quantos(pool, conta) == 1


def test_o_agente_registra_os_dois_prestadores_sem_perguntar(pool, conta):
    """Com a régua nova, o caso da Prime nem chega a virar pergunta."""
    a = _lancar(pool, conta, "Serviço prestado - Pedro Yan Mendes - 2ª quinzena")
    b = _lancar(pool, conta, "Serviço prestado - Thiago Cesar Borges - 2ª quinzena")
    assert "registrada" in a.lower() and "registrada" in b.lower()
    assert _quantos(pool, conta) == 2


def test_o_forcar_do_agente_funciona_de_verdade(pool, conta):
    """Se `forcar` não fosse lido, a mensagem acima mandaria o agente num caminho
    que não existe — e o segundo pagamento bateria na trava pra sempre."""
    _lancar(pool, conta, "Serviço prestado - Thiago")
    forcado = _lancar(pool, conta, "Serviço prestado - Thiago", forcar=True)
    assert "registrada" in forcado.lower()
    assert "NAO registrei" not in forcado
    assert _quantos(pool, conta) == 2
