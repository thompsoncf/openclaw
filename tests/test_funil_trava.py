"""A trava da insistência (finance/funil_trava): a regra e o ensaio.

O que estes testes protegem, em uma frase cada:
  * o cliente que está esperando resposta NUNCA é travado — nem no 30º dia, nem
    com cinco tentativas nossas em cima, nem com as renovações esgotadas;
  * a bola é decidida por ID, não por data, porque duas mensagens quase
    simultâneas chegam fora de ordem e empatar para o lado errado cala a empresa;
  * vencido pede justificativa, esgotado é parede, dentro do prazo não engata;
  * etapa sem teto não engata — a trava é consequência do prazo da etapa;
  * o modo nasce 'off', e 'ligado' NÃO é aceito enquanto a tela não existe;
  * o ensaio grava a TENTATIVA e deixa a mensagem passar;
  * um defeito no ensaio nunca impede uma mensagem de sair.

Banco descartável, `agora` sempre injetado — mesmo padrão do teste do teto.
"""
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from finance import funil_trava as tv

MIG = Path(__file__).resolve().parent.parent / "db" / "migracoes"
CONTA = 11
AGORA = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)

_SQL = """
create table prospeccao (id bigserial primary key, conta_id bigint, empresa text, contato text,
  status text default 'novo', estagio text default 'lead', vendedor_id bigint,
  -- as duas que as AÇÕES da justificativa escrevem: 'pediu_data' marca o retorno
  -- e 'mandando' move o lead. Sem elas aqui, o teste passaria e a produção quebraria.
  proximo_contato_em timestamptz, atualizado_em timestamptz default now(),
  criado_em timestamptz default now());
create table membros (id bigserial primary key, conta_id bigint, nome text, email text,
  papel text default 'vendedor', ativo boolean default true);
create table conversas (id bigserial primary key, conta_id bigint, prospeccao_id bigint,
  canal text default 'whatsapp');
create table mensagens (id bigserial primary key, conversa_id bigint, direcao text,
  autor text default 'humano', membro_id bigint, texto text default '',
  criado_em timestamptz default now());
create table funil_movimentos (id bigserial primary key, conta_id bigint, prospeccao_id bigint,
  de text, para text, motivo text, membro_id bigint, criado_em timestamptz default now());
create table funil_avisos (id bigserial primary key, conta_id bigint, prospeccao_id bigint,
  estado text, nivel text, etapa text default '', ref_em timestamptz, simulado boolean default false,
  membro_id bigint, criado_em timestamptz default now());
create table funil_etapas (id bigserial primary key, semeado_de text, conta_id bigint, chave text,
  rotulo text, ordem int default 0, fixa boolean default false, fase text default 'venda',
  prazo_min integer, gatilho text, gatilho_ativo boolean default false);
create table funil_regua (conta_id bigint primary key,
  gatilhos_modo text not null default 'off', cobranca_modo text not null default 'off',
  janela_dias text, janela_abre time, janela_fecha time,
  sem_resposta_min int, bola_nossa_min int, bola_cliente_min int,
  escala_min int, teto_avisos_dia int,
  follow_up_modo text not null default 'off', fu_proposta_dias int, fu_toques_dias text,
  fu_festa_dias int, fu_teto_dia int,
  atualizado_em timestamptz not null default now());
"""


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_funil_trava_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=3, open=True, kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_SQL)
        # as migrações DE VERDADE, não cópias: é o único jeito de o teste perceber
        # que uma coluna nova não chegou em produção
        # a 254 mexe em `funil_motivos_perda`, que nasce na 235 — a ordem é a mesma
        # em que o Render aplica, e replayar fora de ordem quebraria aqui antes de
        # quebrar em produção, que é exatamente o que este replay existe pra pegar
        for nome in ("218_follow_up.sql",
                     "230_funil_teto_da_etapa.sql", "232_funil_saidas_da_etapa.sql",
                     "233_funil_toques_da_etapa.sql",
                     "235_motivos_de_perda_da_conta.sql", "236_reativar_o_lead_que_volta.sql",
                     "238_etapa_sai_do_quadro.sql",
                     "254_funil_semeado_de.sql", "257_trava_insistencia.sql"):
            c.execute((MIG / nome).read_text(encoding="utf-8"))
        for ch, o in (("novo", 0), ("contatado", 10), ("proposta", 30)):
            c.execute("insert into funil_etapas (conta_id, chave, rotulo, ordem) values (%s,%s,%s,%s)",
                      (CONTA, ch, ch.capitalize(), o))
        # a Prime: 7 dias e 2 renovações no Contatado. 'proposta' fica SEM teto.
        c.execute("""update funil_etapas set teto_dias=7, renovacoes_max=2
                      where conta_id=%s and chave='contatado'""", (CONTA,))
        c.commit()
    yield p
    p.close()


@pytest.fixture()
def c(pool):
    with pool.connection() as con:
        for t in ("funil_trava_tentativa", "funil_renovacoes", "funil_movimentos",
                  "mensagens", "conversas", "prospeccao", "membros", "funil_regua"):
            con.execute(f"delete from {t}")
        con.execute("insert into funil_regua (conta_id, trava_modo) values (%s,'observando')",
                    (CONTA,))
        con.commit()
        yield con
        con.rollback()


# ------------------------------------------------------------------ ajudantes

def _lead(c, *, etapa="contatado", dias=0):
    """Um lead na etapa, que entrou nela há `dias`."""
    lid = c.execute("""insert into prospeccao (conta_id, empresa, status, estagio, criado_em)
                       values (%s,'Lead',%s,'lead',%s) returning id""",
                    (CONTA, etapa, AGORA - timedelta(days=dias))).fetchone()[0]
    c.execute("""insert into funil_movimentos (conta_id, prospeccao_id, de, para, criado_em)
                 values (%s,%s,'novo',%s,%s)""",
              (CONTA, lid, etapa, AGORA - timedelta(days=dias)))
    return lid


def _conversa(c, lead):
    return c.execute("""insert into conversas (conta_id, prospeccao_id, canal)
                        values (%s,%s,'whatsapp') returning id""", (CONTA, lead)).fetchone()[0]


def _msg(c, conv, direcao, quando):
    c.execute("""insert into mensagens (conversa_id, direcao, texto, criado_em)
                 values (%s,%s,'oi',%s)""", (conv, direcao, quando))


def _renovar(c, lead, etapa="contatado", quantas=1):
    for i in range(quantas):
        c.execute("""insert into funil_renovacoes (conta_id, prospeccao_id, etapa, ordem,
                                                   justificativa, vence_em, criado_em)
                     values (%s,%s,%s,%s,'porque sim',%s,%s)""",
                  (CONTA, lead, etapa, i + 1, AGORA, AGORA - timedelta(hours=1)))


# ------------------------------------------------------------------ a regra pura

def test_cliente_esperando_nunca_trava_venha_o_que_vier():
    """É a condição que o dono pôs ao aprovar: "sim, poupa quem está esperando".

    A bola é consultada ANTES do prazo. Escrito na ordem contrária, o primeiro
    `return` já teria travado os 26 leads que esta regra existe para poupar.
    """
    for estado in ("ok", "avisar", "vencido", "esgotado"):
        assert tv.decidir(bola="cliente", estado=estado) == "poupou_cliente_esperando", estado


def test_bola_nossa_segue_o_estado_do_prazo():
    assert tv.decidir(bola="nossa", estado="ok") == "no_prazo"
    assert tv.decidir(bola="nossa", estado="avisar") == "no_prazo"
    assert tv.decidir(bola="nossa", estado="vencido") == "pediria_justificativa"
    assert tv.decidir(bola="nossa", estado="esgotado") == "parede"


# ------------------------------------------------------------------ de quem é a bola

def test_a_bola_e_decidida_por_id_e_nao_por_data(c):
    """Duas mensagens quase simultâneas podem chegar fora de ordem no `criado_em`.

    Aqui a nossa saída tem data MAIS NOVA que a entrada do cliente, mas id menor —
    o que acontece quando o webhook do cliente entra primeiro e a nossa gravação
    carimba um relógio adiantado. Por data, a empresa calaria quem está esperando.
    """
    lead = _lead(c, dias=30)
    conv = _conversa(c, lead)
    _msg(c, conv, "in", AGORA - timedelta(minutes=1))     # id menor, data mais velha
    _msg(c, conv, "out", AGORA - timedelta(minutes=5))    # id maior, data mais nova
    # o cliente falou por último POR ID → a bola é dele
    assert tv.bola_de(c, CONTA, lead) == "nossa", "por id, a saída é a última"
    # e o contrário também vale: invertendo a ordem de inserção, a bola vira dele
    lead2 = _lead(c, dias=30)
    conv2 = _conversa(c, lead2)
    _msg(c, conv2, "out", AGORA - timedelta(minutes=1))
    _msg(c, conv2, "in", AGORA - timedelta(minutes=5))
    assert tv.bola_de(c, CONTA, lead2) == "cliente"


def test_lead_sem_conversa_nenhuma_tem_a_bola_conosco(c):
    lead = _lead(c, dias=30)
    assert tv.bola_de(c, CONTA, lead) == "nossa"


def test_tentativas_conta_so_o_que_veio_depois_da_ultima_do_cliente(c):
    lead = _lead(c, dias=10)
    conv = _conversa(c, lead)
    _msg(c, conv, "out", AGORA - timedelta(days=9))
    _msg(c, conv, "in", AGORA - timedelta(days=8))        # ele respondeu: zera
    _msg(c, conv, "out", AGORA - timedelta(days=5))
    _msg(c, conv, "out", AGORA - timedelta(days=3))
    assert tv.tentativas_de(c, CONTA, lead) == 2


# ------------------------------------------------------------------ avaliar

def test_lead_estourado_com_a_bola_conosco_pediria_justificativa(c):
    lead = _lead(c, dias=9)                                # teto é 7
    conv = _conversa(c, lead)
    _msg(c, conv, "out", AGORA - timedelta(days=8))
    v = tv.avaliar(c, CONTA, lead, agora=AGORA)
    assert v["decisao"] == "pediria_justificativa"
    assert v["bola"] == "nossa" and v["estado"] == "vencido"
    assert v["tentativas"] == 1 and 8.9 < v["dias"] < 9.1


def test_o_mesmo_lead_estourado_NAO_trava_se_o_cliente_voltou(c):
    """O caso dos 26. Mesmo lead, mesmo prazo estourado — muda só quem falou."""
    lead = _lead(c, dias=9)
    conv = _conversa(c, lead)
    _msg(c, conv, "out", AGORA - timedelta(days=8))
    _msg(c, conv, "in", AGORA - timedelta(hours=2))        # ele voltou
    v = tv.avaliar(c, CONTA, lead, agora=AGORA)
    assert v["decisao"] == "poupou_cliente_esperando"
    assert v["estado"] == "vencido", "o prazo continua estourado — o que muda é a bola"


def test_renovacoes_esgotadas_viram_parede(c):
    lead = _lead(c, dias=25)
    _renovar(c, lead, quantas=2)                           # renovacoes_max = 2
    v = tv.avaliar(c, CONTA, lead, agora=AGORA)
    assert v["decisao"] == "parede" and v["renovacoes"] == 2


def test_dentro_do_prazo_nao_engata(c):
    lead = _lead(c, dias=3)
    assert tv.avaliar(c, CONTA, lead, agora=AGORA)["decisao"] == "no_prazo"


def test_etapa_sem_teto_nunca_engata(c):
    """'proposta' não tem teto. A trava é consequência do prazo da etapa: sem prazo,
    não há do que o vendedor se justificar."""
    lead = _lead(c, etapa="proposta", dias=300)
    v = tv.avaliar(c, CONTA, lead, agora=AGORA)
    assert v["decisao"] == "no_prazo" and v["etapa"] == "proposta"


# ------------------------------------------------------------------ o modo

def test_o_modo_nasce_off_e_so_aceita_o_que_a_tela_cumpre(c):
    """Nasce 'off' em toda conta. 'ligado' passou a ser aceito quando a tela de
    justificativa nasceu — até o PR anterior ele fechava para 'off', porque um
    modo que a tela não sabe cumprir faria o motor recusar envios que o vendedor
    não teria como destravar. Valor estranho no banco continua fechando para 'off'."""
    c.execute("delete from funil_regua")
    assert tv.modo(c, CONTA) == "off", "conta sem linha na régua"
    c.execute("insert into funil_regua (conta_id) values (%s)", (CONTA,))
    assert tv.modo(c, CONTA) == "off", "o padrão da coluna"
    for v in ("observando", "ligado"):
        c.execute("update funil_regua set trava_modo=%s where conta_id=%s", (v, CONTA))
        assert tv.modo(c, CONTA) == v
    c.execute("update funil_regua set trava_modo='meia-boca' where conta_id=%s", (CONTA,))
    assert tv.modo(c, CONTA) == "off", "o que o código não cumpre, não vale"


# ------------------------------------------------------------------ o ensaio

def _tentativas(c):
    return c.execute("""select decisao, bola, tentativas, simulado
                          from funil_trava_tentativa order by id""").fetchall()


def test_o_ensaio_grava_a_tentativa_e_deixa_passar(c):
    lead = _lead(c, dias=9)
    conv = _conversa(c, lead)
    _msg(c, conv, "out", AGORA - timedelta(days=8))
    v = tv.registrar(c, CONTA, lead, membro_id=None, agora=AGORA)
    assert v and v["decisao"] == "pediria_justificativa"
    linhas = _tentativas(c)
    assert len(linhas) == 1
    assert linhas[0][0] == "pediria_justificativa" and linhas[0][1] == "nossa"
    assert linhas[0][3] is True, "em ensaio tudo é simulado"


def test_o_ensaio_registra_tambem_quando_POUPA(c):
    """É a prova de que a decisão de poupar estava certa: sem registrar, ninguém
    consegue dizer quantas vezes a trava teria calado um cliente que esperava."""
    lead = _lead(c, dias=20)
    conv = _conversa(c, lead)
    _msg(c, conv, "out", AGORA - timedelta(days=19))
    _msg(c, conv, "in", AGORA - timedelta(days=1))
    tv.registrar(c, CONTA, lead, agora=AGORA)
    assert _tentativas(c)[0][0] == "poupou_cliente_esperando"


def test_tentativa_dentro_do_prazo_nao_vira_linha(c):
    """Senão a tabela vira uma cópia de `mensagens` — 188 linhas num dia movimentado
    da Prime, e o relatório do ensaio perde o sentido."""
    lead = _lead(c, dias=2)
    assert tv.registrar(c, CONTA, lead, agora=AGORA) is None
    assert _tentativas(c) == []


def test_modo_off_nao_grava_nada(c):
    c.execute("update funil_regua set trava_modo='off' where conta_id=%s", (CONTA,))
    lead = _lead(c, dias=30)
    assert tv.registrar(c, CONTA, lead, agora=AGORA) is None
    assert _tentativas(c) == []


def test_defeito_no_ensaio_nunca_impede_a_mensagem(c):
    """A mensagem sair vale mais que o ensaio contar. Aqui a tabela do ensaio não
    existe — o que é o deploy pela metade, com o código novo e a migração atrasada —
    e `registrar` devolve None em vez de estourar na cara do vendedor."""
    c.execute("drop table funil_trava_tentativa")
    lead = _lead(c, dias=9)
    assert tv.registrar(c, CONTA, lead, agora=AGORA) is None
    # e a transação continua utilizável: é o savepoint fazendo o trabalho dele
    assert c.execute("select 1").fetchone()[0] == 1


def test_sem_a_tabela_da_regua_a_transacao_do_chamador_sobrevive(c):
    """O caso que quebrou de verdade, e que a versão anterior deste teste não pegava.

    Sem `funil_regua`, a consulta do modo falha. Se esse `except` não desfizer a
    consulta num savepoint, a transação fica envenenada e quem morre a seguir é o
    INSERT da mensagem do vendedor — "current transaction is aborted". Foi assim
    que `test_enviar_mensagem_grava_e_pausa` ficou vermelho na primeira tentativa.
    """
    c.execute("drop table funil_regua")
    lead = _lead(c, dias=9)
    assert tv.modo(c, CONTA) == "off"
    assert tv.registrar(c, CONTA, lead, agora=AGORA) is None
    # a prova: o chamador consegue ESCREVER depois, que é o que o envio faz
    conv = _conversa(c, lead)
    _msg(c, conv, "out", AGORA)
    assert c.execute("select count(*) from mensagens").fetchone()[0] == 1


def test_o_resumo_traz_o_denominador_de_mensagens(c):
    """O número que responde ao dono é "quantas por dia", então a conta de
    justificativas só significa alguma coisa ao lado das mensagens enviadas."""
    mid = c.execute("""insert into membros (conta_id, nome, email) values (%s,'Pedro','p@x.com')
                       returning id""", (CONTA,)).fetchone()[0]
    lead = _lead(c, dias=9)
    conv = _conversa(c, lead)
    _msg(c, conv, "out", AGORA - timedelta(days=8))
    c.execute("""insert into mensagens (conversa_id, direcao, autor, criado_em)
                 values (%s,'out','humano',now())""", (conv,))
    tv.registrar(c, CONTA, lead, membro_id=mid, agora=AGORA)
    r = tv.resumo(c, CONTA, dias=7)
    assert r["engataram"] == 1
    assert r["enviadas"] >= 1
    assert r["por_decisao_e_vendedor"][0]["quem"] == "Pedro"


# ------------------------------------------------------------------ a tela (modo ligado)

def _ligado(c):
    c.execute("update funil_regua set trava_modo='ligado' where conta_id=%s", (CONTA,))


def test_no_modo_ligado_o_registro_nao_e_simulado(c):
    """`simulado` guarda se a mensagem SAIU. Sem essa distinção, o relatório de
    depois de ligar não separaria "teria travado" de "travou"."""
    _ligado(c)
    lead = _lead(c, dias=9)
    conv = _conversa(c, lead)
    _msg(c, conv, "out", AGORA - timedelta(days=8))
    v = tv.registrar(c, CONTA, lead, agora=AGORA)
    assert v["modo"] == "ligado"
    assert _tentativas(c)[0][3] is False


def test_justificar_com_motivo_fora_da_lista_recusa(c):
    lead = _lead(c, dias=9)
    r = tv.justificar(c, CONTA, lead, None, motivo="porque sim", agora=AGORA)
    assert r == {"ok": False, "erro": "motivo_invalido"}


def test_outro_exige_o_texto_e_pediu_data_exige_a_data(c):
    lead = _lead(c, dias=9)
    assert tv.justificar(c, CONTA, lead, None, motivo="outro",
                         agora=AGORA)["erro"] == "descricao_obrigatoria"
    assert tv.justificar(c, CONTA, lead, None, motivo="pediu_data",
                         agora=AGORA)["erro"] == "data_obrigatoria"


def test_justificar_renova_o_prazo_e_a_regra_para_de_engatar(c):
    """É a troca que o desenho promete: justificar uma vez compra uma SEMANA com
    aquele lead. Sem isso o vendedor justificaria a cada mensagem, dez vezes por
    dia, e a trava viraria pedágio."""
    lead = _lead(c, dias=9)
    conv = _conversa(c, lead)
    _msg(c, conv, "out", AGORA - timedelta(days=8))
    assert tv.avaliar(c, CONTA, lead, agora=AGORA)["decisao"] == "pediria_justificativa"
    r = tv.justificar(c, CONTA, lead, None, motivo="outro_caminho", agora=AGORA)
    assert r["ok"] and r["renovacoes"] == 1
    assert tv.avaliar(c, CONTA, lead, agora=AGORA)["decisao"] == "no_prazo"
    # e uma semana depois ela engata de novo
    assert tv.avaliar(c, CONTA, lead, agora=AGORA + timedelta(days=8))["decisao"] \
        == "pediria_justificativa"


def test_a_terceira_vez_e_a_parede_e_justificar_nao_abre(c):
    """As duas renovações da Prime. Na terceira não há justificativa que passe —
    a saída é mover de etapa ou perder, e é isso que a tela oferece."""
    lead = _lead(c, dias=30)
    _renovar(c, lead, quantas=2)
    assert tv.avaliar(c, CONTA, lead, agora=AGORA)["decisao"] == "parede"
    r = tv.justificar(c, CONTA, lead, None, motivo="outro_caminho", agora=AGORA)
    assert r["ok"] is False and r["erro"] == "sem_renovacao"


def test_pediu_data_marca_o_retorno_pelo_caminho_do_follow_up(c):
    """Duas coisas de uma vez: o lead volta no dia que o CLIENTE pediu, e a marcação
    passa por `follow_up.marcar` — com linha em follow_up_marcacoes, autor e motivo.
    Um update solto em `proximo_contato_em` daria dois donos à mesma coluna.

    E a hora é MEIO-DIA UTC, não meia-noite: 00:00 UTC é 21h do dia anterior no
    Brasil, e o lead voltaria pra fila na véspera, à noite."""
    lead = _lead(c, dias=9)
    r = tv.justificar(c, CONTA, lead, None, motivo="pediu_data", data="2026-10-20",
                      agora=AGORA)
    assert r["ok"] and r["voltar_em"] == "2026-10-20"
    quando = c.execute("select proximo_contato_em from prospeccao where id=%s",
                       (lead,)).fetchone()[0]
    assert quando == datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
    marc = c.execute("""select prazo_em, acao from follow_up_marcacoes
                         where prospeccao_id=%s""", (lead,)).fetchone()
    assert marc and marc[0] == quando and "chamar" in marc[1]


def test_data_torta_nao_desfaz_a_renovacao_ja_gravada(c):
    """A renovação é gravada antes da ação. Se a data vier impossível, o vendedor
    não pode perder a renovação que já consumiu — sairia pior do que entrou."""
    lead = _lead(c, dias=9)
    r = tv.justificar(c, CONTA, lead, None, motivo="pediu_data", data="30/02/2026",
                      agora=AGORA)
    assert r["ok"] and r.get("data_ignorada") is True
    assert tv._teto.renovacoes_de(c, lead, "contatado") == 1


def test_mandando_o_que_ele_pediu_move_o_lead_pra_frente_com_historico(c):
    """'proposta' é a próxima etapa de venda deste funil de teste. O nome não está
    no código: sai da ordem das etapas da conta (CLAUDE.md §6)."""
    lead = _lead(c, dias=9)
    r = tv.justificar(c, CONTA, lead, membro_id=None, motivo="mandando", agora=AGORA)
    assert r["ok"] and r["moveu_para"] == "proposta"
    assert c.execute("select status from prospeccao where id=%s",
                     (lead,)).fetchone()[0] == "proposta"
    mov = c.execute("""select de, para, motivo from funil_movimentos
                        where prospeccao_id=%s and motivo=%s""",
                    (lead, tv.MOTIVO_MOV)).fetchone()
    assert mov == ("contatado", "proposta", tv.MOTIVO_MOV), "o movimento fica no histórico"


def test_a_proxima_de_venda_nunca_e_de_fechamento_nem_sai_do_quadro(c):
    """Justificar um envio não pode GANHAR nem PERDER um lead. Aqui 'proposta' sai
    do quadro e a única etapa à frente vira uma de fechamento — e o motivo
    'mandando' então não move nada, em vez de empurrar o lead pra Ganho."""
    c.execute("""update funil_etapas set sai_do_quadro=true
                  where conta_id=%s and chave='proposta'""", (CONTA,))
    c.execute("""insert into funil_etapas (conta_id, chave, rotulo, ordem, fase)
                 values (%s,'ganho','Ganho',900,'fechamento')""", (CONTA,))
    try:
        lead = _lead(c, dias=9)
        assert tv.proxima_de_venda(c, CONTA, "contatado") is None
        r = tv.justificar(c, CONTA, lead, None, motivo="mandando", agora=AGORA)
        assert r["ok"] and "moveu_para" not in r
        assert c.execute("select status from prospeccao where id=%s",
                         (lead,)).fetchone()[0] == "contatado"
    finally:
        c.execute("""update funil_etapas set sai_do_quadro=false
                      where conta_id=%s and chave='proposta'""", (CONTA,))
        c.execute("delete from funil_etapas where conta_id=%s and chave='ganho'", (CONTA,))
