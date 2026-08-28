"""A régua do funil — as quatro travas, o relógio de atendimento e a bola.

O que estes testes protegem, em uma frase cada:
  * o relógio só anda em horário de atendimento (senão a régua acorda vendedor de
    madrugada e a equipe desliga tudo no primeiro dia);
  * gatilho nunca puxa card pra trás, nunca deixa o lead no meio do caminho e
    nunca desfaz o que o vendedor decidiu na mão;
  * o modo observação mede sem mexer em nada — e não reescreve a mesma linha a
    cada ciclo do poller.
"""
import os
from datetime import datetime, time, timedelta, timezone
from unittest import mock

import pytest
from psycopg_pool import ConnectionPool

from finance import funil_regua as fr

CONTA = 9
AGORA = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)   # 09:00 em Brasília

_SQL = """
create table prospeccao (id bigserial primary key, conta_id bigint, empresa text,
  status text default 'novo', estagio text default 'lead', orcamento_id bigint,
  vendedor_id bigint, atualizado_em timestamptz default now(),
  criado_em timestamptz default now());
create table membros (id bigserial primary key, conta_id bigint, nome text, email text,
  papel text default 'vendedor', ativo boolean default true);
create table funil_avisos (id bigserial primary key, conta_id bigint, prospeccao_id bigint,
  estado text, nivel text, etapa text default '', ref_em timestamptz, simulado boolean default false,
  membro_id bigint, criado_em timestamptz default now());
create unique index uq_funil_aviso on funil_avisos
  (prospeccao_id, estado, nivel, etapa, ref_em, simulado);
create table funil_etapas (id bigserial primary key, conta_id bigint, chave text,
  rotulo text, ordem int default 0, fixa boolean default false, fase text default 'venda',
  prazo_min integer, gatilho text, gatilho_ativo boolean default false);
create table funil_regua (conta_id bigint primary key,
  gatilhos_modo text default 'off', cobranca_modo text default 'off',
  janela_dias text default '1,2,3,4,5,6', janela_abre time default '08:00',
  janela_fecha time default '19:00', sem_resposta_min int default 120,
  bola_nossa_min int default 240, bola_cliente_min int default 4320,
  escala_min int default 240, teto_avisos_dia int default 5);
create table funil_movimentos (id bigserial primary key, conta_id bigint,
  prospeccao_id bigint, de text, para text, motivo text, membro_id bigint,
  criado_em timestamptz default now());
create table conversas (id bigserial primary key, conta_id bigint, prospeccao_id bigint, chip_id bigint);
create table mensagens (id bigserial primary key, conversa_id bigint, direcao text,
  criado_em timestamptz default now(),
  midia_ref jsonb, midia_tipo text, midia_meta jsonb);
create table eventos_agenda (id bigserial primary key, conta_id bigint, prospeccao_id bigint,
  inicio timestamptz, fim timestamptz, status text default 'ativo', desfecho text,
  criado_em timestamptz default now());
create table orcamentos (id bigserial primary key, conta_id bigint, status text,
  atualizado_em timestamptz, aprovada_em timestamptz, sinal_pago_em timestamptz,
  contrato_assinado_em timestamptz);
-- migração 178: é ela que sabe que a proposta SAIU, por qual canal e quando. O
-- gatilho `orcamento_enviado` lê daqui — sem a tabela ele fica cego pro e-mail,
-- pra conversa do app e pro link copiado, que é o estado que este trabalho corrige.
create table orcamento_envios (id bigserial primary key, conta_id bigint,
  orcamento_id bigint, canal text default 'email', destino text default '',
  remetente text default '', ok boolean default true, erro text default '',
  por text default '', criado_em timestamptz default now());
"""

_ETAPAS = [("novo", "Novo", 0, True, "venda", None, None, False),
           ("contatado", "Contatado", 10, False, "venda", None, "resposta_nossa", False),
           ("qualificado", "Agendado Visita", 20, False, "venda", None, "compromisso", False),
           ("proposta", "Proposta", 30, False, "venda", None, "orcamento_enviado", False),
           ("ganho", "Sinal Pago", 900, True, "fechamento", None, "sinal_pago", False),
           ("perdido", "Perdido", 910, True, "fechamento", None, None, False),
           ("eventos", "Eventos Realizados", 920, False, "pos", None, "compromisso_feito", False)]


@pytest.fixture()
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_funil_regua_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=3, open=True, kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_SQL)
        for e in _ETAPAS:
            c.execute("""insert into funil_etapas (conta_id, chave, rotulo, ordem, fixa, fase,
                           prazo_min, gatilho, gatilho_ativo) values (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                      (CONTA,) + e)
        c.commit()
    yield p
    p.close()


def _cfg(**kw):
    base = {"janela_dias": "1,2,3,4,5,6", "janela_abre": time(8, 0), "janela_fecha": time(19, 0)}
    base.update(kw)
    return base


def _lead(c, empresa="ACME", status="novo", vend=None, criado=None):
    return c.execute(
        """insert into prospeccao (conta_id, empresa, status, vendedor_id, criado_em)
           values (%s,%s,%s,%s,coalesce(%s, now())) returning id""",
        (CONTA, empresa, status, vend, criado)).fetchone()[0]


def _conversa(c, lead_id):
    return c.execute("insert into conversas (conta_id, prospeccao_id) values (%s,%s) returning id",
                     (CONTA, lead_id)).fetchone()[0]


def _msg(c, conv, direcao, quando):
    c.execute("insert into mensagens (conversa_id, direcao, criado_em) values (%s,%s,%s)",
              (conv, direcao, quando))


def _manual(c, lead_id, de="proposta", para="novo", quando=None):
    """Move o card À MÃO, num instante do MESMO relógio do resto do arquivo.

    POR QUE ISTO EXISTE. `registrar_movimento` grava `criado_em` com o `now()` do
    banco — o relógio de VERDADE. Todo o resto deste arquivo mede o tempo a partir
    de `AGORA`, que é fixo. A trava 3 compara os dois (`quando > _ultimo_manual`),
    e comparar dois relógios diferentes dá respostas diferentes conforme a hora em
    que o CI roda: `AGORA + 1 dia` é 20/08 12:00, então o teste passava de manhã e
    falhava à tarde — e, a partir de 21/08, falharia para sempre.

    Carimbar aqui põe os dois lados no mesmo relógio. Nada de produção muda: quem
    chama `registrar_movimento` de verdade quer o `now()` do banco mesmo.
    """
    fr.registrar_movimento(c, CONTA, lead_id, de, para, "manual", membro_id=1)
    c.execute("""update funil_movimentos set criado_em=%s
                  where id=(select max(id) from funil_movimentos where prospeccao_id=%s)""",
              (quando or AGORA, lead_id))


def _ligar(c, chave, modo="ligado"):
    c.execute("update funil_etapas set gatilho_ativo=true where conta_id=%s and chave=%s", (CONTA, chave))
    c.execute("insert into funil_regua (conta_id, gatilhos_modo) values (%s,%s) "
              "on conflict (conta_id) do update set gatilhos_modo=excluded.gatilhos_modo", (CONTA, modo))


# ----------------------------------------------------------------- o relógio

def test_fora_do_expediente_o_cronometro_nao_anda():
    # 22h de um sábado até 23h do mesmo sábado: uma hora de relógio, zero de atendimento
    ini = datetime(2026, 8, 22, 1, 0, tzinfo=timezone.utc)    # sáb 22:00 BR (sexta 22h BR? não: 01:00Z = sex 22:00)
    assert fr.minutos_uteis(ini, ini + timedelta(hours=1), _cfg()) == 0


def test_conta_so_o_pedaco_dentro_da_janela():
    # 10:00 BR -> 12:00 BR numa quarta = 120 minutos cheios
    ini = datetime(2026, 8, 19, 13, 0, tzinfo=timezone.utc)
    assert fr.minutos_uteis(ini, ini + timedelta(hours=2), _cfg()) == 120


def test_atravessar_a_noite_nao_soma_a_madrugada():
    # quarta 18:00 BR -> quinta 09:00 BR: 1h de quarta + 1h de quinta = 120 min
    ini = datetime(2026, 8, 19, 21, 0, tzinfo=timezone.utc)   # qua 18:00 BR
    fim = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)   # qui 09:00 BR
    assert fr.minutos_uteis(ini, fim, _cfg()) == 120


def test_domingo_fora_da_janela_nao_conta():
    # sábado 18:00 BR -> segunda 09:00 BR: 1h de sábado + 1h de segunda
    ini = datetime(2026, 8, 22, 21, 0, tzinfo=timezone.utc)
    fim = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
    assert fr.minutos_uteis(ini, fim, _cfg()) == 120


def test_semana_sem_dia_nenhum_nunca_vence():
    """Desligar a semana toda tem que significar 'não cobra', nunca 'cobra sempre'."""
    ini = datetime(2026, 8, 19, 13, 0, tzinfo=timezone.utc)
    assert fr.minutos_uteis(ini, ini + timedelta(days=5), _cfg(janela_dias="")) == 0


def test_dentro_da_janela_so_no_expediente():
    assert fr.dentro_da_janela(datetime(2026, 8, 19, 13, 0, tzinfo=timezone.utc), _cfg())   # qua 10h BR
    assert not fr.dentro_da_janela(datetime(2026, 8, 19, 3, 0, tzinfo=timezone.utc), _cfg())  # qua 00h BR
    assert not fr.dentro_da_janela(datetime(2026, 8, 23, 15, 0, tzinfo=timezone.utc), _cfg())  # domingo


# ----------------------------------------------------------------- travas 1 e 2

def test_gatilho_nunca_puxa_card_pra_tras():
    cands = [{"chave": "contatado", "ordem": 10}]
    assert fr.escolher_etapa(30, cands) is None      # o lead já está em Proposta


def test_empate_leva_o_lead_pra_etapa_mais_avancada():
    cands = [{"chave": "proposta", "ordem": 30}, {"chave": "ganho", "ordem": 900}]
    assert fr.escolher_etapa(0, cands)["chave"] == "ganho"


# ----------------------------------------------------------------- a bola

def test_bola_nossa_quando_o_cliente_falou_por_ultimo(pool):
    with pool.connection() as c:
        lead = _lead(c); conv = _conversa(c, lead)
        _msg(c, conv, "out", AGORA - timedelta(hours=5))
        _msg(c, conv, "in", AGORA - timedelta(hours=2))
        c.commit()
        estado, ref = fr.estado_da_bola(c, lead)
        assert estado == "bola_nossa"
        assert ref == AGORA - timedelta(hours=2)


def test_bola_do_cliente_quando_respondemos_por_ultimo(pool):
    with pool.connection() as c:
        lead = _lead(c); conv = _conversa(c, lead)
        _msg(c, conv, "in", AGORA - timedelta(hours=5))
        _msg(c, conv, "out", AGORA - timedelta(hours=1))
        c.commit()
        assert fr.estado_da_bola(c, lead)[0] == "bola_cliente"


def test_sem_resposta_quando_nunca_falamos_nada(pool):
    """O pior estado que existe — e o que a conta 34 tem 35 vezes em 21 dias."""
    with pool.connection() as c:
        lead = _lead(c); conv = _conversa(c, lead)
        _msg(c, conv, "in", AGORA - timedelta(hours=30))
        c.commit()
        estado, ref = fr.estado_da_bola(c, lead)
        assert estado == "sem_resposta"
        assert ref == AGORA - timedelta(hours=30)


def test_lead_sem_conversa_nenhuma_e_sem_resposta(pool):
    with pool.connection() as c:
        lead = _lead(c)
        c.commit()
        assert fr.estado_da_bola(c, lead)[0] == "sem_resposta"


# ----------------------------------------------------------------- gatilhos

def test_desligado_nao_mexe_em_nada(pool):
    with pool.connection() as c:
        lead = _lead(c); conv = _conversa(c, lead)
        _msg(c, conv, "out", AGORA)
        c.execute("update funil_etapas set gatilho_ativo=true where conta_id=%s", (CONTA,))
        c.commit()
        assert fr.aplicar_gatilhos(c, CONTA) == {"movidos": 0, "simulados": 0}
        assert c.execute("select status from prospeccao where id=%s", (lead,)).fetchone()[0] == "novo"


def test_observando_anota_o_salto_sem_mover_o_card(pool):
    with pool.connection() as c:
        lead = _lead(c); conv = _conversa(c, lead)
        _msg(c, conv, "out", AGORA)
        _ligar(c, "contatado", "observando")
        c.commit()
        assert fr.aplicar_gatilhos(c, CONTA)["simulados"] == 1
        c.commit()
        assert c.execute("select status from prospeccao where id=%s", (lead,)).fetchone()[0] == "novo"
        mov = c.execute("select de, para, motivo from funil_movimentos where prospeccao_id=%s",
                        (lead,)).fetchone()
        assert mov == ("novo", "contatado", "simulado:resposta_nossa")


def test_observando_nao_reescreve_a_cada_ciclo(pool):
    """O poller roda a cada 2 minutos; sem guarda, 'o que teria acontecido' viraria
    um contador de ciclos."""
    with pool.connection() as c:
        lead = _lead(c); conv = _conversa(c, lead)
        _msg(c, conv, "out", AGORA)
        _ligar(c, "contatado", "observando")
        c.commit()
        fr.aplicar_gatilhos(c, CONTA); c.commit()
        assert fr.aplicar_gatilhos(c, CONTA)["simulados"] == 0
        c.commit()
        n = c.execute("select count(*) from funil_movimentos where prospeccao_id=%s", (lead,)).fetchone()[0]
        assert n == 1


def test_ligado_move_o_card_e_deixa_a_linha_no_historico(pool):
    with pool.connection() as c:
        lead = _lead(c); conv = _conversa(c, lead)
        _msg(c, conv, "out", AGORA)
        _ligar(c, "contatado")
        c.commit()
        assert fr.aplicar_gatilhos(c, CONTA)["movidos"] == 1
        c.commit()
        assert c.execute("select status from prospeccao where id=%s", (lead,)).fetchone()[0] == "contatado"
        assert c.execute("select motivo from funil_movimentos where prospeccao_id=%s",
                         (lead,)).fetchone()[0] == "gatilho:resposta_nossa"


def test_sinal_pago_leva_direto_pro_fechamento(pool):
    with pool.connection() as c:
        lead = _lead(c)
        oid = c.execute("""insert into orcamentos (conta_id, status, sinal_pago_em)
                           values (%s,'aprovada',%s) returning id""", (CONTA, AGORA)).fetchone()[0]
        c.execute("update prospeccao set orcamento_id=%s where id=%s", (oid, lead))
        _ligar(c, "ganho")
        c.commit()
        fr.aplicar_gatilhos(c, CONTA); c.commit()
        assert c.execute("select status from prospeccao where id=%s", (lead,)).fetchone()[0] == "ganho"


def test_a_mao_do_vendedor_manda(pool):
    """TRAVA 3: o vendedor puxou o card de volta pra Novo depois do orçamento. O
    gatilho do orçamento (que é ANTERIOR) não pode desfazer isso no ciclo seguinte."""
    with pool.connection() as c:
        lead = _lead(c, status="novo")
        oid = c.execute("""insert into orcamentos (conta_id, status, atualizado_em)
                           values (%s,'enviado',%s) returning id""",
                        (CONTA, AGORA - timedelta(days=2))).fetchone()[0]
        c.execute("update prospeccao set orcamento_id=%s where id=%s", (oid, lead))
        _manual(c, lead)
        _ligar(c, "proposta")
        c.commit()
        assert fr.aplicar_gatilhos(c, CONTA)["movidos"] == 0
        c.commit()
        assert c.execute("select status from prospeccao where id=%s", (lead,)).fetchone()[0] == "novo"


def test_evento_novo_depois_do_movimento_manual_volta_a_valer(pool):
    """A trava 3 respeita a decisão do vendedor, mas não congela o lead pra sempre:
    fato NOVO depois da mão dele volta a mover."""
    with pool.connection() as c:
        lead = _lead(c, status="novo")
        oid = c.execute("""insert into orcamentos (conta_id, status, atualizado_em)
                           values (%s,'enviado',%s) returning id""",
                        (CONTA, AGORA + timedelta(days=1))).fetchone()[0]
        c.execute("update prospeccao set orcamento_id=%s where id=%s", (oid, lead))
        _manual(c, lead)
        _ligar(c, "proposta")
        c.commit()
        assert fr.aplicar_gatilhos(c, CONTA)["movidos"] == 1


# ------------------------------------------- orçamento enviado, por QUALQUER canal
#
# A versão original do gatilho perguntava só `orcamentos.status`, e só um caminho no
# sistema inteiro escreve 'enviado' ao enviar (a proposta criada no app do vendedor).
# Mandar por e-mail, mandar na conversa do app ou copiar o link não mexem em status
# nenhum — então o gatilho ficava cego justamente pros canais mais usados.
#
# Medido na conta 34 em 19/08/2026, com o gatilho ligado desde as 14:02:
# `resposta_nossa` moveu 78 cards sozinho e este aqui moveu ZERO.

def _orc_rascunho(c, quando=None):
    """Uma proposta que NUNCA teve o status mexido — o caso que estava quebrado."""
    return c.execute(
        """insert into orcamentos (conta_id, status, atualizado_em)
           values (%s,'rascunho',%s) returning id""",
        (CONTA, quando or AGORA)).fetchone()[0]


def _envio(c, oid, canal="email", ok=True, quando=None):
    c.execute("""insert into orcamento_envios (conta_id, orcamento_id, canal, ok, criado_em)
                 values (%s,%s,%s,%s,%s)""", (CONTA, oid, canal, ok, quando or AGORA))


@pytest.mark.parametrize("canal", ["email", "whatsapp", "link"])
def test_envio_registrado_move_o_card_seja_qual_for_o_canal(pool, canal):
    """O coração deste trabalho: proposta em RASCUNHO, mandada por um canal
    qualquer, move o card. Antes, nenhum dos três movia."""
    with pool.connection() as c:
        lead = _lead(c, status="novo")
        oid = _orc_rascunho(c)
        c.execute("update prospeccao set orcamento_id=%s where id=%s", (oid, lead))
        _envio(c, oid, canal)
        _ligar(c, "proposta")
        c.commit()
        assert fr.aplicar_gatilhos(c, CONTA)["movidos"] == 1
        c.commit()
        assert c.execute("select status from prospeccao where id=%s",
                         (lead,)).fetchone()[0] == "proposta"


def test_tentativa_que_falhou_nao_move_o_card(pool):
    """`ok=false` é a caixa que recusou. Proposta que não chegou não é proposta
    enviada — e mover o card aqui esconderia justamente o que precisa de conserto."""
    with pool.connection() as c:
        lead = _lead(c, status="novo")
        oid = _orc_rascunho(c)
        c.execute("update prospeccao set orcamento_id=%s where id=%s", (oid, lead))
        _envio(c, oid, "email", ok=False)
        _ligar(c, "proposta")
        c.commit()
        assert fr.aplicar_gatilhos(c, CONTA)["movidos"] == 0


def test_o_instante_do_gatilho_e_o_do_ENVIO(pool):
    """TRAVA 3 compara o instante do fato com o último movimento manual. Se o
    instante fosse `atualizado_em`, qualquer edição posterior da proposta faria o
    fato "acontecer" de novo e ressuscitaria um card que alguém puxou de propósito.

    Aqui: envio ANTES da mão do vendedor, proposta editada DEPOIS. O card fica."""
    with pool.connection() as c:
        lead = _lead(c, status="novo")
        oid = _orc_rascunho(c, quando=AGORA + timedelta(days=1))   # editada hoje
        c.execute("update prospeccao set orcamento_id=%s where id=%s", (oid, lead))
        _envio(c, oid, "email", quando=AGORA - timedelta(days=3))  # enviada antes
        _manual(c, lead)
        _ligar(c, "proposta")
        c.commit()
        assert fr.aplicar_gatilhos(c, CONTA)["movidos"] == 0, \
            "o gatilho usou a data de edição e desfez a decisão do vendedor"


def test_o_caminho_antigo_pelo_status_continua_valendo(pool):
    """A proposta criada no app do vendedor nasce 'enviado' e sem linha de envio —
    ela não pode parar de funcionar por causa da porta nova."""
    with pool.connection() as c:
        lead = _lead(c, status="novo")
        oid = c.execute("""insert into orcamentos (conta_id, status, atualizado_em)
                           values (%s,'enviado',%s) returning id""",
                        (CONTA, AGORA)).fetchone()[0]
        c.execute("update prospeccao set orcamento_id=%s where id=%s", (oid, lead))
        _ligar(c, "proposta")
        c.commit()
        assert fr.aplicar_gatilhos(c, CONTA)["movidos"] == 1


def test_proposta_solta_nao_move_ninguem(pool):
    """Sem `prospeccao.orcamento_id` não há card pra mover — por isso o vínculo é
    metade do trabalho (a outra metade está em tests/test_proposta_lead.py)."""
    with pool.connection() as c:
        _lead(c, status="novo")
        oid = _orc_rascunho(c)
        _envio(c, oid, "email")
        _ligar(c, "proposta")
        c.commit()
        assert fr.aplicar_gatilhos(c, CONTA)["movidos"] == 0


def test_um_gatilho_quebrado_nao_derruba_os_outros(pool):
    """Sem savepoint por evento, uma tabela que falta envenena a transação e a conta
    inteira fica SEM gatilho nenhum — os que funcionavam param junto com o que
    quebrou. Silêncio por gatilho é degradação; por conta é apagão."""
    with pool.connection() as c:
        lead = _lead(c, status="novo")
        _msg(c, _conversa(c, lead), "out", AGORA)   # dispara resposta_nossa
        oid = _orc_rascunho(c)
        c.execute("update prospeccao set orcamento_id=%s where id=%s", (oid, lead))
        _envio(c, oid, "email")
        _ligar(c, "contatado")
        _ligar(c, "proposta")
        c.commit()

    quebrado = dict(fr._SQL_EVENTO)
    quebrado["orcamento_enviado"] = "select id, now() from tabela_que_nao_existe"
    with mock.patch.object(fr, "_SQL_EVENTO", quebrado):
        with pool.connection() as c:
            assert fr.aplicar_gatilhos(c, CONTA)["movidos"] == 1, \
                "o gatilho quebrado levou o resto junto"
            c.commit()
            assert c.execute("select status from prospeccao where id=%s",
                             (lead,)).fetchone()[0] == "contatado"


# ----------------------------------------------------------------- fase

def test_fechado_passa_a_incluir_a_pos_venda(pool):
    """O motivo de existir a fase: o lead que anda pra "Eventos Realizados" não pode
    sumir do "ganhos do mês" — ele foi vendido, o evento é depois."""
    with pool.connection() as c:
        fechadas = fr.chaves_fechadas(fr.etapas(c, CONTA))
        assert set(fechadas) == {"ganho", "eventos"}
        assert "perdido" not in fechadas


# ----------------------------------------------------------------- o motor

def test_motor_e_inerte_com_tudo_desligado(pool):
    """O deploy não pode mexer em nada. Conta sem régua ligada nem aparece."""
    with pool.connection() as c:
        lead = _lead(c); conv = _conversa(c, lead)
        _msg(c, conv, "out", AGORA)
        c.execute("update funil_etapas set gatilho_ativo=true where conta_id=%s", (CONTA,))
        c.commit()
    assert fr.rodar(pool) == {"contas": 0, "movidos": 0, "simulados": 0,
                              "avisos": 0, "escalados": 0}
    with pool.connection() as c:
        assert c.execute("select status from prospeccao where id=%s", (lead,)).fetchone()[0] == "novo"


def test_motor_roda_so_nas_contas_que_ligaram(pool):
    with pool.connection() as c:
        lead = _lead(c); conv = _conversa(c, lead)
        _msg(c, conv, "out", AGORA)
        _ligar(c, "contatado")
        c.commit()
    assert fr.rodar(pool) == {"contas": 1, "movidos": 1, "simulados": 0,
                              "avisos": 0, "escalados": 0}
    with pool.connection() as c:
        assert c.execute("select status from prospeccao where id=%s", (lead,)).fetchone()[0] == "contatado"


# ----------------------------------------------------------------- "fechado?"

def _conta_fechados(c, conta_id=CONTA):
    return c.execute("select count(*) from prospeccao p where p.conta_id=%s and p.status in "
                     + fr.sql_fechadas("p"), (conta_id,)).fetchone()[0]


def _conta_abertos(c, conta_id=CONTA):
    return c.execute("select count(*) from prospeccao p where p.conta_id=%s and "
                     + fr.sql_encerradas_nao("p"), (conta_id,)).fetchone()[0]


def test_lead_na_pos_venda_continua_contando_como_vendido(pool):
    """O motivo desta parte existir. Antes, o placar do vendedor contava
    `status='ganho'` cru: no dia em que alguém arrastasse o card de "Sinal Pago"
    pra "Eventos Realizados", a venda sumia do mês como se tivesse sido desfeita."""
    with pool.connection() as c:
        _lead(c, "PAGOU", status="ganho")
        _lead(c, "EVENTO FEITO", status="eventos")     # etapa de fase 'pos'
        _lead(c, "AINDA NEGOCIANDO", status="proposta")
        _lead(c, "FOI EMBORA", status="perdido")
        c.commit()
        assert _conta_fechados(c) == 2, "a etapa de pós-venda tem que contar como ganha"
        assert _conta_abertos(c) == 1, "só o que ainda está em venda fica 'em aberto'"


def test_perdido_nunca_entra_no_fechado(pool):
    with pool.connection() as c:
        _lead(c, "PERDIDA", status="perdido")
        c.commit()
        assert _conta_fechados(c) == 0


def test_conta_sem_etapas_semeadas_ainda_reconhece_ganho(pool):
    """A subconsulta correlaciona por conta_id: numa conta que nunca abriu o funil,
    funil_etapas está vazia e `status in ()` responderia que ninguém nunca vendeu.
    O `union all select 'ganho'` existe pra isso."""
    outra = CONTA + 500
    with pool.connection() as c:
        c.execute("insert into prospeccao (conta_id, empresa, status) values (%s,'SEM ETAPAS','ganho')",
                  (outra,))
        c.commit()
        assert _conta_fechados(c, outra) == 1
        assert _conta_abertos(c, outra) == 0


# ----------------------------------------------------------------- cobrança
def _cobrar(c, quando=None):
    return fr.avaliar_cobranca(c, CONTA, quando or AGORA)


def _ligar_cobranca(c, modo="ligado", **kw):
    campos = {"cobranca_modo": modo}
    campos.update(kw)
    fr.config(c, CONTA)
    sets = ", ".join(f"{k}=%s" for k in campos)
    c.execute(f"update funil_regua set {sets} where conta_id=%s", (*campos.values(), CONTA))


def _vendedor(c, nome="Jacqueline"):
    return c.execute("insert into membros (conta_id, nome, email) values (%s,%s,%s) returning id",
                     (CONTA, nome, f"{nome.lower()}@x.com")).fetchone()[0]


def _esperando(c, horas, vend=None):
    """Lead com o cliente falando por último há N horas — a 'bola com você'."""
    lead = _lead(c, "ESPERANDO", vend=vend)
    conv = _conversa(c, lead)
    _msg(c, conv, "out", AGORA - timedelta(hours=horas + 1))
    _msg(c, conv, "in", AGORA - timedelta(hours=horas))
    return lead


def test_cobranca_desligada_nao_avisa_ninguem(pool):
    with pool.connection() as c:
        _esperando(c, 30, _vendedor(c))
        c.commit()
        assert _cobrar(c)["avisos"] == 0


def test_bola_com_a_gente_alem_do_prazo_vira_aviso(pool):
    with pool.connection() as c:
        v = _vendedor(c)
        _esperando(c, 30, v)                       # 30h de relógio, bem além de 4h úteis
        _ligar_cobranca(c)
        c.commit()
        r = _cobrar(c)
        assert r["avisos"] == 1
        assert r["pendentes"][0]["membro_id"] == v
        assert r["pendentes"][0]["estado"] == "bola_nossa"


def test_o_mesmo_fato_nunca_cobra_duas_vezes(pool):
    """O poller roda a cada 2 minutos: sem o dedup, o vendedor levaria 30 pushes por
    hora do mesmo lead."""
    with pool.connection() as c:
        _esperando(c, 30, _vendedor(c))
        _ligar_cobranca(c)
        c.commit()
        assert _cobrar(c)["avisos"] == 1
        c.commit()
        assert _cobrar(c)["avisos"] == 0


def test_fato_novo_volta_a_cobrar(pool):
    """O cliente escreve de novo daqui a um mês: é outro fato, e tem que cobrar.
    Por isso o dedup é por `ref_em` e não por lead."""
    with pool.connection() as c:
        v = _vendedor(c)
        lead = _esperando(c, 30, v)
        _ligar_cobranca(c)
        c.commit()
        assert _cobrar(c)["avisos"] == 1
        c.commit()
        _msg(c, _conversa(c, lead), "in", AGORA - timedelta(hours=20))
        c.commit()
        assert _cobrar(c)["avisos"] == 1


def test_fora_do_expediente_guarda_e_nao_acorda_ninguem(pool):
    with pool.connection() as c:
        _esperando(c, 30, _vendedor(c))
        _ligar_cobranca(c)
        c.commit()
        madrugada = datetime(2026, 8, 19, 6, 0, tzinfo=timezone.utc)     # 03:00 BR
        assert _cobrar(c, madrugada)["avisos"] == 0
        c.commit()
        assert _cobrar(c)["avisos"] == 1, "e sai quando o expediente abre"


def test_teto_do_dia_represa_em_vez_de_perder(pool):
    with pool.connection() as c:
        v = _vendedor(c)
        for i in range(4):
            lead = _lead(c, f"LEAD {i}", vend=v)
            conv = _conversa(c, lead)
            _msg(c, conv, "out", AGORA - timedelta(hours=31))
            _msg(c, conv, "in", AGORA - timedelta(hours=30))
        _ligar_cobranca(c, teto_avisos_dia=2)
        c.commit()
        r = _cobrar(c)
        assert (r["avisos"], r["represados"]) == (2, 2)


def test_o_vendedor_e_sempre_cobrado_antes_do_gestor(pool):
    """Mesmo num lead atrasado há dias. Escalar de cara nunca avisaria o dono do
    lead — viraria fofoca sobre ele em vez de aviso pra ele."""
    with pool.connection() as c:
        _esperando(c, 300, _vendedor(c))      # muito além do prazo + escala
        _ligar_cobranca(c)
        c.commit()
        r = _cobrar(c)
        assert (r["avisos"], r["escalados"]) == (1, 0)
        assert r["pendentes"][0]["nivel"] == "vendedor"


def test_gestor_entra_depois_de_escala_min_sem_ninguem_agir(pool):
    with pool.connection() as c:
        _esperando(c, 300, _vendedor(c))
        _ligar_cobranca(c)
        c.commit()
        _cobrar(c)                                   # 1ª passada: cobra o vendedor
        c.commit()
        # e nada muda enquanto a escala não vence
        assert _cobrar(c, AGORA + timedelta(hours=1))["escalados"] == 0
        c.commit()
        r = _cobrar(c, AGORA + timedelta(hours=6))   # escala_min padrão = 4h
        assert r["escalados"] == 1
        assert r["pendentes"][0]["nivel"] == "gestor"


def test_observando_conta_sem_mandar_nada(pool):
    """É o que o dono pediu: medir o barulho que a régua faria, sem fazer barulho."""
    with pool.connection() as c:
        _esperando(c, 30, _vendedor(c))
        _ligar_cobranca(c, modo="observando")
        c.commit()
        r = _cobrar(c)
        assert (r["simulados"], r["avisos"], r["pendentes"]) == (1, 0, [])


def test_lead_fechado_nao_e_cobrado(pool):
    with pool.connection() as c:
        v = _vendedor(c)
        lead = _lead(c, "JA VENDIDO", status="ganho", vend=v)
        conv = _conversa(c, lead)
        _msg(c, conv, "out", AGORA - timedelta(hours=31))
        _msg(c, conv, "in", AGORA - timedelta(hours=30))
        # e o de pós-venda também não: o evento foi entregue, não há o que cobrar
        lead2 = _lead(c, "EVENTO FEITO", status="eventos", vend=v)
        conv2 = _conversa(c, lead2)
        _msg(c, conv2, "out", AGORA - timedelta(hours=31))
        _msg(c, conv2, "in", AGORA - timedelta(hours=30))
        _ligar_cobranca(c)
        c.commit()
        assert _cobrar(c)["avisos"] == 0


def test_etapa_travada_so_cobra_com_a_conversa_em_dia(pool):
    """'O cliente está esperando' é mais urgente e mais acionável que 'este card não
    anda' — então a conversa cobra primeiro, e a etapa só quando ela está em dia."""
    with pool.connection() as c:
        v = _vendedor(c)
        lead = _lead(c, "PROPOSTA PARADA", status="proposta", vend=v,
                     criado=AGORA - timedelta(days=40))
        conv = _conversa(c, lead)
        _msg(c, conv, "in", AGORA - timedelta(days=39))
        _msg(c, conv, "out", AGORA - timedelta(minutes=30))     # respondemos agorinha
        c.execute("update funil_etapas set prazo_min=%s where conta_id=%s and chave='proposta'",
                  (10 * 1440, CONTA))
        _ligar_cobranca(c, bola_cliente_min=999999)             # follow-up longe de vencer
        c.commit()
        r = _cobrar(c)
        assert r["avisos"] == 1
        assert r["pendentes"][0]["estado"] == "etapa"


def test_escalar_troca_de_destinatario(pool):
    """Se o aviso de gestor fosse pro mesmo vendedor, escalar não seria escalar:
    cobraria de novo quem já foi cobrado e ninguém acima ficaria sabendo."""
    enviados = []
    with pool.connection() as c:
        v = _vendedor(c, "Vendedor")
        dono = c.execute("insert into membros (conta_id, nome, email, papel) "
                         "values (%s,'Dono','dono@x.com','dono') returning id", (CONTA,)).fetchone()[0]
        _esperando(c, 300, v)
        _ligar_cobranca(c)
        c.commit()
        _cobrar(c); c.commit()
        r = _cobrar(c, AGORA + timedelta(hours=6)); c.commit()

    import finance.cockpit as ck
    orig = ck.enviar_push
    ck.enviar_push = lambda pool, conta, membro, t, cp, url: enviados.append(membro)
    try:
        fr.notificar(pool, CONTA, r["pendentes"])
    finally:
        ck.enviar_push = orig
    assert enviados == [dono], f"o escalado tem que ir pro dono, foi pra {enviados}"


def test_a_escala_nao_depende_da_hora_REAL_em_que_o_teste_roda(pool):
    """A armadilha que derrubou estes testes em 19/08/2026, às 14h UTC.

    A escala lê `funil_avisos.criado_em` de volta e compara com `agora` pra saber
    se passou tempo suficiente sem ninguém agir. A coluna nascia do `default
    now()` — o relógio do BANCO —, então a comparação juntava dois relógios. Em
    produção eles coincidem e ninguém via; aqui, com o instante injetado, a
    escala só acontecia enquanto a hora real do dia deixasse a conta fechar. Os
    dois testes acima passavam de manhã e falhavam à tarde, no mesmo commit.

    Este caso prende a propriedade: dado o mesmo instante injetado, o resultado é
    o mesmo — em qualquer hora do dia em que alguém rode a suíte."""
    from datetime import timedelta
    with pool.connection() as c:
        v = _vendedor(c, "Vendedor")
        _esperando(c, 300, v)
        _ligar_cobranca(c)
        c.commit()
        _cobrar(c); c.commit()
        # a passada seguinte é 6h DEPOIS da primeira no relógio injetado, e
        # escala_min é 240 — tem que escalar, sem consultar o relógio de ninguém.
        r = _cobrar(c, AGORA + timedelta(hours=6)); c.commit()
        assert r["escalados"] == 1, r
        assert [p["nivel"] for p in r["pendentes"]] == ["gestor"]
        # e o aviso ficou carimbado com o instante da passada, não com o do banco
        carimbos = c.execute("select nivel, criado_em from funil_avisos order by id").fetchall()
        assert dict(carimbos) == {"vendedor": AGORA, "gestor": AGORA + timedelta(hours=6)}

