"""A consulta do funil de Serviços e a cura do token — as duas coisas que a
listagem faz a cada carregamento.

CONTEXTO. `/painel/servicos/lista` é relida em nove pontos da tela (salvar,
fechar, sinal, marcar data, enviar, anexar comprovante, excluir, gerar proposta
e abrir a aba). Tudo que ela faz, faz nove vezes por sessão de trabalho. Duas
coisas saíram dali em 19/09/2026:

1. A CURA DO TOKEN era um `update ... where conta_id=%s and token is null` MAIS
   um commit ANTES do SELECT, sempre. Em produção ela atualizava zero linhas —
   `salvar` já grava o token no INSERT e no UPDATE desde que a coluna existe —
   e mesmo assim abria uma transação de escrita por leitura do funil. Agora a
   pergunta "tem o que curar?" é respondida pelo SELECT que já rodou.

2. AS SUBCONSULTAS CORRELACIONADAS viraram dois `left join lateral`. Eram sete
   buscas por linha lendo as mesmas duas linhas: cinco em `contratos` e duas em
   `eventos_agenda`.

O teste do SQL roda a consulta DE VERDADE contra o banco de teste. O motivo é
específico: esta consulta já derrubou o funil inteiro com 500 por ORDER BY
ambíguo (está comentado no próprio arquivo), e juntar tabelas é exatamente o que
recria essa condição. Compilar não basta — só executando é que a ambiguidade
aparece.
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from db.conexao import init_schema
from web import painel_servicos as ps


def _tok():
    """Token único por execução: `idx_orcamentos_token` é único e o banco de
    teste sobrevive entre rodadas."""
    return uuid.uuid4().hex[:18]


_MIGRACOES = ("064_clientes_lojista.sql", "098_agenda.sql",
              "160_agenda_pre_reserva.sql",
              "164_contratos.sql", "165_contrato_token.sql",
              "189_contrato_enviado_em.sql", "178_orcamento_envios.sql")


@pytest.fixture(scope="module")
def pool():
    p = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=4,
                       open=True, kwargs={"prepare_threshold": None})
    init_schema(p)
    base = Path(__file__).resolve().parent.parent / "db" / "migracoes"
    with p.connection() as c:
        # `orcamentos` nasce do guard de runtime do próprio módulo — é o mesmo DDL
        # que o preDeployCommand aplica, e usar ele aqui garante que o teste vê as
        # colunas que a tela vê.
        ps._criar_orcamentos(c)
        c.commit()
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
            "insert into contas (tipo, nome) values ('pj', 'Prime Teste') "
            "returning id").fetchone()[0]
        c.commit()
    return cid


def _consulta_do_funil(conta_nome, conta_id):
    """Recompõe exatamente o SQL que a rota monta, com os mesmos parâmetros.

    Ler o texto do módulo (e não uma cópia) é o que faz o teste continuar
    valendo quando alguém mexer na consulta — uma cópia envelheceria calada.
    """
    return (ps._COLS_FUNIL + ps._DE_FUNIL
            + " where orcamentos.conta_id=%s"
              " order by orcamentos.criado_em desc limit 50",
            (conta_nome, conta_id))


# ------------------------------------------------------------------ a consulta

def test_consulta_do_funil_roda_sem_ambiguidade(pool, conta_id):
    """O teste que o 500 do ORDER BY ambíguo teria pego."""
    with pool.connection() as c:
        c.execute("insert into orcamentos (conta_id, cliente, empresa, status, "
                  "token, numero, modo) values (%s,'Maria','','rascunho', %s, 1, 'evento')",
                  (conta_id, _tok()))
        c.commit()
        sql, args = _consulta_do_funil("PRIME", conta_id)
        linhas = c.execute(sql, args).fetchall()
    assert len(linhas) == 1
    assert linhas[0][1] == "Maria"


def test_a_consulta_devolve_as_29_colunas_que_a_tela_le_por_posicao(pool, conta_id):
    """`painel_servicos_lista` lê o resultado por índice, de r[0] a r[28].

    Uma coluna a mais ou a menos no meio desloca TODAS as de baixo em silêncio —
    o contrato aqui é a quantidade e a ordem, não o nome.
    """
    with pool.connection() as c:
        c.execute("insert into orcamentos (conta_id, cliente, status, token, modo) "
                  "values (%s,'Ana','rascunho',%s,'evento')", (conta_id, _tok()))
        c.commit()
        sql, args = _consulta_do_funil("PRIME", conta_id)
        cur = c.execute(sql, args)
        assert len(cur.description) == 29
        r = cur.fetchone()
    # as sete que vieram dos laterais continuam nas mesmas posições
    assert r[17] is None and r[18] is None and r[19] is None   # contrato
    assert r[20] is None and r[22] is None                     # agenda
    assert r[26] is None and r[27] is None                     # contrato (fim)


def test_lateral_traz_o_contrato_vivo_e_ignora_o_substituido(pool, conta_id):
    """Um orçamento com contrato e um aditivo que o substitui.

    `ux_contratos_orcamento` deixa os dois conviverem (o aditivo tem o mesmo
    `orcamento_id`), e o funil tem que mostrar o VIVO — o que não substitui
    ninguém.
    """
    with pool.connection() as c:
        oid = c.execute(
            "insert into orcamentos (conta_id, cliente, status, token, modo) "
            "values (%s,'Rone','fechado',%s,'evento') returning id",
            (conta_id, _tok())).fetchone()[0]
        token_vivo = "ctk-" + _tok()
        vivo = c.execute(
            "insert into contratos (conta_id, orcamento_id, numero, token, "
            "assinado_em) values (%s,%s,7,%s, now()) returning id",
            (conta_id, oid, token_vivo)).fetchone()[0]
        c.execute(
            "insert into contratos (conta_id, orcamento_id, numero, token, "
            "substitui_id) values (%s,%s,8,%s,%s)",
            (conta_id, oid, "ctk2-" + _tok(), vivo))
        c.commit()
        sql, args = _consulta_do_funil("PRIME", conta_id)
        r = [x for x in c.execute(sql, args).fetchall() if x[0] == oid][0]
    assert r[17] == token_vivo   # token do contrato vivo
    assert r[18] == 7          # número
    assert r[19] is True       # assinado
    assert r[27] == vivo       # id


# ------------------------------------------------------------- a cura do token

class _ConexaoFalsa:
    """Só o suficiente pra ver SE a cura toca no banco — que é o ponto."""

    def __init__(self, devolve=()):
        self.execucoes = []
        self.commits = 0
        self._devolve = devolve

    def execute(self, sql, args=None):
        self.execucoes.append((sql, args))
        return self

    def fetchall(self):
        return self._devolve

    def commit(self):
        self.commits += 1


def _linha(oid, token):
    """Uma linha do funil no formato que o SELECT devolve — só r[0] e r[9] importam."""
    return (oid,) + (None,) * 8 + (token,) + (None,) * 19


def test_sem_token_faltando_a_cura_nao_abre_transacao(pool):
    """O caso de produção: nenhuma proposta sem token.

    É a razão de a cura ter saído de antes do SELECT. Se este teste passar a
    falhar, o funil voltou a escrever no banco a cada leitura.
    """
    c = _ConexaoFalsa()
    assert ps._curar_tokens(c, 34, [_linha(1, "abc"), _linha(2, "def")]) == {}
    assert c.execucoes == []
    assert c.commits == 0


def test_cura_so_as_linhas_sem_token_e_devolve_o_que_curou(pool):
    """Proposta anterior à coluna `token`: cura, mas só ela, e o token novo volta
    pra linha do funil — sem isso o "Abrir proposta" só apareceria no próximo
    carregamento."""
    c = _ConexaoFalsa(devolve=[(2, "novo22")])
    curados = ps._curar_tokens(c, 34, [_linha(1, "abc"), _linha(2, None)])
    assert curados == {2: "novo22"}
    assert c.commits == 1
    sql, args = c.execucoes[0]
    assert "update orcamentos set token" in sql
    assert "token is null" in sql
    assert args == (34, [2])       # só o id que veio sem token


def test_a_cura_acontece_de_verdade_no_banco(pool, conta_id):
    """Ponta a ponta: a linha sem token sai da cura com token, e a que já tinha
    não é tocada."""
    with pool.connection() as c:
        ja = "ja-" + _tok()
        com = c.execute("insert into orcamentos (conta_id, cliente, token) "
                        "values (%s,'Com',%s) returning id",
                        (conta_id, ja)).fetchone()[0]
        sem = c.execute("insert into orcamentos (conta_id, cliente) "
                        "values (%s,'Sem') returning id", (conta_id,)).fetchone()[0]
        c.commit()
        curados = ps._curar_tokens(c, conta_id, [_linha(com, "jatinha"), _linha(sem, None)])
        assert list(curados) == [sem]
        assert curados[sem]
        guardado = c.execute("select token from orcamentos where id=%s",
                             (sem,)).fetchone()[0]
        assert guardado == curados[sem]
        intacto = c.execute("select token from orcamentos where id=%s",
                            (com,)).fetchone()[0]
        assert intacto == ja
