"""A esteira da cobrança (finance/esteira): 10 por dia, 3 cobranças, dia 7 fecha.

O que estes testes protegem, em uma frase cada:
  * o relógio é a ENTRADA na esteira, não o prazo vencido da etapa — foi o defeito
    que fez a Layane receber os quatro degraus no mesmo minuto;
  * entram no máximo `por_dia` por vendedor, e rodar de novo no mesmo dia não
    duplica ninguém;
  * cliente esperando resposta NUNCA entra na esteira;
  * a mensagem que saiu do WhatsApp Web (sem membro_id) conta como ação — é como a
    Prime trabalha, 1.191 de 1.505 mensagens em setembro;
  * o cliente voltar a falar resolve, e não vira cobrança do vendedor;
  * cobra no dia 1, 3 e 7 — e só neles;
  * o dia 7 marca `ultimo_dia`, e o fechamento só acontece DEPOIS da janela;
  * 'observando' entra e cobra, mas não fecha ninguém;
  * quem mexeu no lead ganha da esteira.
"""
import os
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from finance import esteira as es

MIG = Path(__file__).resolve().parent.parent / "db" / "migracoes"
CONTA = 13
VEND, OUTRO = 71, 72
#: 10h em Brasília — dentro da janela (08:00–19:00)
AGORA = datetime(2026, 9, 19, 13, 0, tzinfo=timezone.utc)

_SQL = """
create table prospeccao (id bigserial primary key, conta_id bigint, empresa text, contato text,
  status text default 'novo', estagio text default 'lead', vendedor_id bigint,
  evento_em date, proximo_contato_em timestamptz, atualizado_em timestamptz default now(),
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
create table funil_etapas (id bigserial primary key, semeado_de text, conta_id bigint, chave text,
  rotulo text, ordem int default 0, fixa boolean default false, fase text default 'venda',
  prazo_min integer, gatilho text, gatilho_ativo boolean default false);
-- o registro de envio (276 e 284): é a memória do "já mandei o fecho hoje", e é
-- onde cada canal do aviso deixa rastro
create table aviso_envios (id bigserial primary key, conta_id bigint, membro_id bigint,
  origem text, canal text, destino text, assunto text, n_leads int, ok boolean,
  motivo text, criado_em timestamptz not null default now(),
  sid text, token text, entregue_em timestamptz, lido_em timestamptz, clicado_em timestamptz);
-- o parâmetro dos e-mails de gestão (migrações 274 e 276): é de onde o fecho do
-- dia tira o endereço do dono que não tem e-mail no cadastro de membro
create table contas (id bigint primary key, nome text,
  resumo_semanal boolean not null default false,
  resumo_semanal_emails text not null default '',
  resumo_semanal_vendedor boolean not null default true,
  resumo_semanal_dia text not null default 'segunda',
  resumo_semanal_dono_emails text not null default '');
create table funil_regua (conta_id bigint primary key,
  gatilhos_modo text not null default 'off', cobranca_modo text not null default 'off',
  janela_dias text, janela_abre time, janela_fecha time,
  sem_resposta_min int, bola_nossa_min int, bola_cliente_min int,
  escala_min int, teto_avisos_dia int,
  follow_up_modo text not null default 'off', fu_proposta_dias int, fu_toques_dias text,
  fu_zap boolean not null default false, fu_festa_dias int, fu_teto_dia int,
  atualizado_em timestamptz not null default now());
"""


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_esteira_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=3, open=True, kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_SQL)
        for nome in ("209_raio_x_dono.sql", "213_perda_motivo_por_perfil.sql",
                     "230_funil_teto_da_etapa.sql", "235_motivos_de_perda_da_conta.sql",
                     "238_etapa_sai_do_quadro.sql", "254_funil_semeado_de.sql",
                     "292_esteira_da_cobranca.sql"):
            c.execute((MIG / nome).read_text(encoding="utf-8"))
        for ch, o in (("novo", 0), ("contatado", 10), ("proposta", 30), ("perdido", 910)):
            c.execute("insert into funil_etapas (conta_id, chave, rotulo, ordem) values (%s,%s,%s,%s)",
                      (CONTA, ch, ch.capitalize(), o))
        c.execute("update funil_etapas set teto_dias=7 where conta_id=%s and chave in ('contatado','proposta')",
                  (CONTA,))
        for mid, nome in ((VEND, "Vendedor"), (OUTRO, "Outro")):
            c.execute("insert into membros (id, conta_id, nome) values (%s,%s,%s)", (mid, CONTA, nome))
        # o DONO, que é quem recebe o fecho do dia (19/09/2026)
        c.execute("insert into membros (id, conta_id, nome, email, papel) "
                  "values (99,%s,'MANOEL','dono@x.com','dono')", (CONTA,))
        c.commit()
    yield p
    p.close()


@pytest.fixture()
def c(pool):
    with pool.connection() as con:
        for t in ("follow_up_esteira", "funil_movimentos", "mensagens", "conversas",
                  "prospeccao", "funil_regua", "aviso_envios"):
            con.execute(f"delete from {t}")
        con.execute("update membros set email=null where conta_id=%s and papel='vendedor'", (CONTA,))
        con.execute("update membros set email='dono@x.com' where conta_id=%s and papel='dono'", (CONTA,))
        con.execute("""insert into contas (id, nome) values (%s,'Prime')
                       on conflict (id) do update set resumo_semanal_emails='',
                                                      resumo_semanal_dono_emails=''""", (CONTA,))
        con.execute("""insert into funil_regua (conta_id, esteira_modo, fu_teto_dia, fu_toques_dias,
                                                janela_dias, janela_abre, janela_fecha)
                       values (%s,'ligado',10,'1,3,7','1,2,3,4,5,6','08:00','19:00')""", (CONTA,))
        con.commit()
        yield con
        con.rollback()


# ------------------------------------------------------------------ ajudantes

def _lead(c, *, vend=VEND, etapa="contatado", dias=30, nome="Lead"):
    lid = c.execute("""insert into prospeccao (conta_id, contato, status, estagio, vendedor_id, criado_em)
                       values (%s,%s,%s,'lead',%s,%s) returning id""",
                    (CONTA, nome, etapa, vend, AGORA - timedelta(days=dias))).fetchone()[0]
    c.execute("""insert into funil_movimentos (conta_id, prospeccao_id, de, para, criado_em)
                 values (%s,%s,'novo',%s,%s)""", (CONTA, lid, etapa, AGORA - timedelta(days=dias)))
    return lid


def _msg(c, lead, direcao, *, membro=None, quando=None):
    conv = c.execute("select id from conversas where prospeccao_id=%s", (lead,)).fetchone()
    if not conv:
        conv = c.execute("""insert into conversas (conta_id, prospeccao_id) values (%s,%s) returning id""",
                         (CONTA, lead)).fetchone()
    c.execute("insert into mensagens (conversa_id, direcao, membro_id, criado_em) values (%s,%s,%s,%s)",
              (conv[0], direcao, membro, quando or (AGORA - timedelta(days=20))))


def _com_bola_nossa(c, **kw):
    """Um lead onde NÓS falamos por último — o estado normal de quem está parado."""
    lid = _lead(c, **kw)
    _msg(c, lid, "in", quando=AGORA - timedelta(days=21))
    _msg(c, lid, "out", quando=AGORA - timedelta(days=20))
    return lid


def _entrou_ha(c, lead, dias):
    c.execute("update follow_up_esteira set entrou_em=%s where prospeccao_id=%s",
              (AGORA - timedelta(days=dias), lead))


# ------------------------------------------------------------------ quem entra

def test_entram_no_maximo_dez_por_vendedor(c):
    for i in range(14):
        _com_bola_nossa(c, nome=f"L{i}")
    assert len(es.entrar(c, CONTA, AGORA)) == 10


def test_rodar_de_novo_no_mesmo_dia_nao_duplica(c):
    for i in range(12):
        _com_bola_nossa(c, nome=f"L{i}")
    assert len(es.entrar(c, CONTA, AGORA)) == 10
    assert es.entrar(c, CONTA, AGORA) == []
    assert c.execute("select count(*) from follow_up_esteira").fetchone()[0] == 10


def test_cada_vendedor_tem_a_propria_cota(c):
    for i in range(12):
        _com_bola_nossa(c, nome=f"A{i}", vend=VEND)
    for i in range(12):
        _com_bola_nossa(c, nome=f"B{i}", vend=OUTRO)
    novos = es.entrar(c, CONTA, AGORA)
    assert len(novos) == 20
    assert sum(1 for n in novos if n["membro_id"] == VEND) == 10


def test_cliente_esperando_nunca_entra(c):
    lid = _lead(c, nome="Esperando")
    _msg(c, lid, "out", quando=AGORA - timedelta(days=21))
    _msg(c, lid, "in", quando=AGORA - timedelta(days=20))   # ele falou por último
    assert es.entrar(c, CONTA, AGORA) == []


def test_so_entra_quem_esta_atrasado(c):
    """Regra do dono: "os 10 leads é só os que estão atrasados". Dentro do prazo
    da etapa (7 dias), o lead não deve nada a ninguém."""
    _com_bola_nossa(c, nome="Dentro do prazo", dias=3)
    assert es.entrar(c, CONTA, AGORA) == []
    atrasado = _com_bola_nossa(c, nome="Atrasado", dias=9)
    assert [n["id"] for n in es.entrar(c, CONTA, AGORA)] == [atrasado]


def test_etapa_sem_prazo_nao_entra(c):
    _com_bola_nossa(c, etapa="novo")
    assert es.entrar(c, CONTA, AGORA) == []


def test_o_mais_antigo_entra_primeiro(c):
    velho = _com_bola_nossa(c, nome="Velho", dias=60)
    for i in range(12):
        _com_bola_nossa(c, nome=f"N{i}", dias=10)
    assert velho in [n["id"] for n in es.entrar(c, CONTA, AGORA)]


# ------------------------------------------------------------------ quem já agiu

def test_mensagem_do_whatsapp_web_conta_como_acao(c):
    """membro_id nulo é o eco do WhatsApp Web — é assim que a Prime trabalha."""
    lid = _com_bola_nossa(c)
    es.entrar(c, CONTA, AGORA)
    _msg(c, lid, "out", membro=None, quando=AGORA + timedelta(hours=1))
    assert es.resolver(c, CONTA, AGORA + timedelta(hours=2)) == 1
    assert c.execute("select resolucao from follow_up_esteira where prospeccao_id=%s",
                     (lid,)).fetchone()[0] == "falou"


def test_cliente_voltar_a_falar_resolve(c):
    lid = _com_bola_nossa(c)
    es.entrar(c, CONTA, AGORA)
    _msg(c, lid, "in", quando=AGORA + timedelta(hours=1))
    es.resolver(c, CONTA, AGORA + timedelta(hours=2))
    assert c.execute("select resolucao from follow_up_esteira where prospeccao_id=%s",
                     (lid,)).fetchone()[0] == "cliente_voltou"


def test_mover_o_card_resolve(c):
    lid = _com_bola_nossa(c)
    es.entrar(c, CONTA, AGORA)
    c.execute("""insert into funil_movimentos (conta_id, prospeccao_id, de, para, motivo, criado_em)
                 values (%s,%s,'contatado','proposta','manual',%s)""",
              (CONTA, lid, AGORA + timedelta(hours=1)))
    es.resolver(c, CONTA, AGORA + timedelta(hours=2))
    assert c.execute("select resolucao from follow_up_esteira where prospeccao_id=%s",
                     (lid,)).fetchone()[0] == "moveu"


def test_a_temperatura_nao_conta_como_acao(c):
    """O motor da temperatura mexe no card sozinho; isso não é o vendedor agindo."""
    lid = _com_bola_nossa(c)
    es.entrar(c, CONTA, AGORA)
    c.execute("""insert into funil_movimentos (conta_id, prospeccao_id, de, para, motivo, criado_em)
                 values (%s,%s,'quente','morno','temperatura',%s)""",
              (CONTA, lid, AGORA + timedelta(hours=1)))
    assert es.resolver(c, CONTA, AGORA + timedelta(hours=2)) == 0


# ------------------------------------------------------------------ as cobranças

def test_cobra_no_dia_1_3_e_7_e_so_neles(c):
    """O dia da ENTRADA é o dia 1 — "cobrar os 10 leads do dia" quer dizer hoje."""
    lid = _com_bola_nossa(c)
    es.entrar(c, CONTA, AGORA)
    vistos = []
    for corridos in range(0, 9):
        _entrou_ha(c, lid, corridos)
        vistos.append(bool(es.cobrancas(c, CONTA, AGORA)))
    # dias corridos desde a entrada → dia da esteira = corridos + 1
    assert [i + 1 for i, v in enumerate(vistos) if v] == [1, 3, 7]


def test_quem_entrou_hoje_e_cobrado_hoje(c):
    lid = _com_bola_nossa(c)
    es.entrar(c, CONTA, AGORA)
    hoje = es.cobrancas(c, CONTA, AGORA)
    assert [i["id"] for i in hoje] == [lid]
    assert hoje[0]["dia"] == 1 and hoje[0]["ultimo_dia"] is False


def test_o_dia_7_e_o_ultimo(c):
    lid = _com_bola_nossa(c)
    es.entrar(c, CONTA, AGORA)
    _entrou_ha(c, lid, 2)
    assert es.cobrancas(c, CONTA, AGORA)[0]["ultimo_dia"] is False
    _entrou_ha(c, lid, 6)
    assert es.cobrancas(c, CONTA, AGORA)[0]["ultimo_dia"] is True


# ------------------------------------------------------------------ o dia 7

def _fim_do_dia(dias_depois=0):
    """19:30 em Brasília — depois da janela."""
    return AGORA.replace(hour=22, minute=30) + timedelta(days=dias_depois)


def test_fecha_no_dia_7_depois_da_janela(c):
    lid = _com_bola_nossa(c)
    es.entrar(c, CONTA, AGORA)
    _entrou_ha(c, lid, 6)
    fechados = es.fechar_vencidos(c, CONTA, _fim_do_dia())
    assert len(fechados) == 1
    assert c.execute("select status from prospeccao where id=%s", (lid,)).fetchone()[0] == "perdido"
    de, para, motivo = c.execute(
        "select de, para, motivo from funil_movimentos where prospeccao_id=%s and para='perdido'",
        (lid,)).fetchone()
    assert (de, para, motivo) == ("contatado", "perdido", es.MOTIVO_MOV)
    # o motivo na ficha NÃO culpa o cliente
    mot, desc = c.execute("select perda_motivo, perda_descricao from prospeccao where id=%s",
                          (lid,)).fetchone()
    assert mot == es.MOTIVO_PERDA and "sem tratativa" in (desc or "")


def test_nao_fecha_antes_da_janela_terminar(c):
    """O aviso diz 'resolva ou fecha hoje às 19h'. Fechar às 13h seria mentira."""
    lid = _com_bola_nossa(c)
    es.entrar(c, CONTA, AGORA)
    _entrou_ha(c, lid, 6)
    assert es.fechar_vencidos(c, CONTA, AGORA) == []
    assert c.execute("select status from prospeccao where id=%s", (lid,)).fetchone()[0] == "contatado"


def test_nao_fecha_antes_do_dia_7(c):
    lid = _com_bola_nossa(c)
    es.entrar(c, CONTA, AGORA)
    _entrou_ha(c, lid, 5)
    assert es.fechar_vencidos(c, CONTA, _fim_do_dia()) == []


def test_observando_cobra_mas_nao_fecha(c):
    c.execute("update funil_regua set esteira_modo='observando' where conta_id=%s", (CONTA,))
    lid = _com_bola_nossa(c)
    es.entrar(c, CONTA, AGORA)
    _entrou_ha(c, lid, 6)
    assert es.cobrancas(c, CONTA, AGORA)[0]["ultimo_dia"] is True
    assert es.fechar_vencidos(c, CONTA, _fim_do_dia()) == []
    assert c.execute("select status from prospeccao where id=%s", (lid,)).fetchone()[0] == "contatado"


def test_quem_agiu_no_ultimo_dia_nao_fecha(c):
    lid = _com_bola_nossa(c)
    es.entrar(c, CONTA, AGORA)
    _entrou_ha(c, lid, 6)
    _msg(c, lid, "out", membro=None, quando=_fim_do_dia() - timedelta(hours=2))
    es.resolver(c, CONTA, _fim_do_dia())
    assert es.fechar_vencidos(c, CONTA, _fim_do_dia()) == []
    assert c.execute("select status from prospeccao where id=%s", (lid,)).fetchone()[0] == "contatado"


def test_pessoa_ganha_da_esteira(c):
    """Alguém moveu o card entre a leitura e o fechamento: a esteira se cala."""
    lid = _com_bola_nossa(c)
    es.entrar(c, CONTA, AGORA)
    _entrou_ha(c, lid, 6)
    c.execute("update prospeccao set status='proposta' where id=%s", (lid,))
    assert es.fechar_vencidos(c, CONTA, _fim_do_dia()) == []


# ------------------------------------------------------------------ o resumo

def test_resumo_conta_o_que_fez_e_o_que_ficou(c):
    feito = _com_bola_nossa(c, nome="Feito")
    _com_bola_nossa(c, nome="Parado")
    es.entrar(c, CONTA, AGORA)
    _msg(c, feito, "out", membro=None, quando=AGORA + timedelta(hours=1))
    es.resolver(c, CONTA, AGORA + timedelta(hours=2))
    r = es.resumo(c, CONTA, VEND, AGORA + timedelta(hours=2))
    assert r["tratou"] == 1 and r["falou"] == 1 and r["na_esteira"] == 1


def test_modo_off_nao_faz_nada(c):
    c.execute("update funil_regua set esteira_modo='off' where conta_id=%s", (CONTA,))
    _com_bola_nossa(c)
    r = es.avaliar(c, CONTA, AGORA)
    assert r["entraram"] == 0 and r["cobrancas"] == []
    assert c.execute("select count(*) from follow_up_esteira").fetchone()[0] == 0


# ───────────────── o fecho do dia pra quem decide (19/09/2026)
#
# A esteira cobra o VENDEDOR de manhã e conta pra ele o que ficou de ontem. Quem
# decide não recebia nada — e a medição daquele dia mostrou por que isso importa:
# dos 30 leads cobrados no dia anterior, ZERO tiveram mensagem, resposta ou
# movimento em 24 horas. Sem alguém lendo o placar, a cobrança vira um ritual que
# ninguém confere.

def _fim_do_expediente():
    """Um instante depois das 19h de Brasília (a janela fecha 19:00)."""
    return AGORA.replace(hour=23, minute=30, second=0, microsecond=0)


def _cobrou_hoje(c, quantos=2):
    """Deixa a conta com cobrança da esteira registrada hoje."""
    for n in range(quantos):
        c.execute("""insert into aviso_envios (conta_id, membro_id, origem, canal, ok, criado_em)
                     values (%s,%s,'esteira','whatsapp',true,%s)""", (CONTA, VEND, AGORA))
    c.connection.commit() if hasattr(c, "connection") else None


def test_o_fecho_do_dia_sai_pro_dono_nos_dois_canais(pool, c, monkeypatch):
    from finance import email_sender as es
    from finance import follow_up as fu
    from finance import esteira as est
    enviados, zaps = [], []
    monkeypatch.setattr(es, "enviar_aviso", lambda *a, **k: enviados.append((a, k)) or True)
    monkeypatch.setattr(fu, "_mandar_zap",
                        lambda *a, **k: zaps.append(a) or {"ok": True, "erro": "", "sid": "S1"})
    monkeypatch.setattr(fu, "_zap_do_membro", lambda *a, **k: "86999990000")
    lead = _lead(c, nome="Ana")
    est.avaliar(c, CONTA, AGORA)
    _cobrou_hoje(c)
    c.commit()

    r = est.fecho_do_dia(pool, CONTA, _fim_do_expediente())
    assert r and r[0]["email"] is True and r[0]["whatsapp"] is True
    assert "O dia fechou" in enviados[0][0][1]
    assert zaps, "o dono não recebeu o fecho no WhatsApp"
    with pool.connection() as con:
        canais = {x[0] for x in con.execute(
            "select canal from aviso_envios where origem='esteira_fecho'").fetchall()}
    # os TRÊS canais desde 19/09/2026 — o push entrou por decisão de quem recebe
    assert canais == {"email", "whatsapp", "push"}, canais
    assert lead


def test_o_fecho_NAO_sai_antes_da_janela_fechar(pool, c, monkeypatch):
    from finance import email_sender as es
    from finance import esteira as est
    monkeypatch.setattr(es, "enviar_aviso", lambda *a, **k: True)
    _cobrou_hoje(c)
    c.commit()
    meio_dia = AGORA.replace(hour=15, minute=0)
    assert est.fecho_do_dia(pool, CONTA, meio_dia) == []


def test_o_fecho_sai_UMA_vez_por_dia(pool, c, monkeypatch):
    """O poller roda de minuto em minuto: sem a trava seriam dezenas."""
    from finance import email_sender as es
    from finance import follow_up as fu
    from finance import esteira as est
    monkeypatch.setattr(es, "enviar_aviso", lambda *a, **k: True)
    monkeypatch.setattr(fu, "_zap_do_membro", lambda *a, **k: "")
    _cobrou_hoje(c)
    c.commit()
    est.fecho_do_dia(pool, CONTA, _fim_do_expediente())
    with pool.connection() as con:
        n1 = con.execute("select count(*) from aviso_envios where origem='esteira_fecho'").fetchone()[0]
    est.fecho_do_dia(pool, CONTA, _fim_do_expediente())
    with pool.connection() as con:
        n2 = con.execute("select count(*) from aviso_envios where origem='esteira_fecho'").fetchone()[0]
    assert n1 == n2 and n1 > 0


def test_dia_SEM_cobranca_nao_gera_fecho(pool, c, monkeypatch):
    """Aviso que chega dizendo nada é o que ensina a ignorar o próximo."""
    from finance import email_sender as es
    from finance import esteira as est
    monkeypatch.setattr(es, "enviar_aviso", lambda *a, **k: True)
    c.commit()
    assert est.fecho_do_dia(pool, CONTA, _fim_do_expediente()) == []


def test_o_fecho_nao_sai_com_a_esteira_em_ensaio(pool, c, monkeypatch):
    from finance import email_sender as es
    from finance import esteira as est
    monkeypatch.setattr(es, "enviar_aviso", lambda *a, **k: True)
    c.execute("update funil_regua set esteira_modo='observando' where conta_id=%s", (CONTA,))
    _cobrou_hoje(c)
    c.commit()
    assert est.fecho_do_dia(pool, CONTA, _fim_do_expediente()) == []


def test_o_texto_do_fecho_diz_o_que_NAO_enxerga():
    """Ligação e conversa pessoal não aparecem. Sem essa linha, quem resolveu no
    telefone vira relapso na leitura de quem cobra."""
    from finance import esteira as est
    titulo, corpo = est.texto_fecho(
        {"tratou": 3, "na_esteira": 7, "fechados_sem_tratativa": 0},
        [("THIAGO", {"tratou": 1, "na_esteira": 4}),
         ("PEDRO", {"tratou": 2, "na_esteira": 3})])
    assert titulo == "📋 O dia fechou: 3 tratados, 7 na esteira"
    assert corpo.split("\n")[0] == "· THIAGO — 1 tratados, 4 na esteira", corpo
    assert "Ligação e conversa pessoal não aparecem" in corpo
    for palavra in ("atenção", "urgente", "relapso", "!"):
        assert palavra not in corpo.lower(), palavra


# ------------------------------------------------------------------ a hora do aviso
# O DIA DA ESTEIRA VIRA À MEIA-NOITE, e o poller roda de minuto em minuto. A trava
# do aviso era só "já saiu hoje?" — então o primeiro ciclo depois da meia-noite
# mandava a cobrança no WhatsApp do vendedor às 00:0x, inclusive no domingo. Estes
# testes fixam a segunda trava: dentro da janela de atendimento, como a régua.

def _madrugada():
    """00:30 em Brasília do dia seguinte — o dia da esteira já virou."""
    return AGORA.replace(hour=3, minute=30) + timedelta(days=1)


def _domingo(hora_utc=13):
    """Domingo 20/09/2026, fora de `janela_dias` ('1,2,3,4,5,6')."""
    return AGORA.replace(hour=hora_utc, minute=0) + timedelta(days=1)


def test_o_aviso_NAO_sai_de_madrugada(pool, c):
    c.commit()
    assert es._e_hora_do_aviso(AGORA, CONTA, pool) is True, "às 10h da manhã tem que sair"
    assert es._e_hora_do_aviso(_madrugada(), CONTA, pool) is False


def test_o_aviso_NAO_sai_no_domingo(pool, c):
    """Domingo não está em `janela_dias` — e a esteira não acorda ninguém no
    descanso só porque o calendário virou."""
    c.commit()
    assert es._e_hora_do_aviso(_domingo(), CONTA, pool) is False


def test_o_aviso_sai_uma_vez_e_so_uma(pool, c):
    _cobrou_hoje(c, 1)
    c.commit()
    assert es._e_hora_do_aviso(AGORA, CONTA, pool) is False


def test_ninguem_entra_fora_do_expediente(c):
    """Quem entrasse de madrugada teria o dia 1 num dia sem cobrança, e na manhã
    seguinte já seria dia 2 — a primeira das três chances, gasta calada."""
    _com_bola_nossa(c)
    assert es.avaliar(c, CONTA, _madrugada())["entraram"] == 0
    assert es.avaliar(c, CONTA, _domingo())["entraram"] == 0
    assert es.avaliar(c, CONTA, AGORA)["entraram"] == 1


def test_nao_fecha_no_domingo(c):
    """19h de domingo não é fim de expediente: ninguém foi cobrado naquele dia."""
    lid = _com_bola_nossa(c)
    es.entrar(c, CONTA, AGORA)
    _entrou_ha(c, lid, 6)
    assert es.fechar_vencidos(c, CONTA, _domingo(hora_utc=22)) == []
    assert len(es.fechar_vencidos(c, CONTA, _fim_do_dia(dias_depois=2))) == 1


def test_o_fecho_do_dia_NAO_sai_no_domingo(pool, c, monkeypatch):
    from finance import email_sender as _es
    from finance import esteira as est
    monkeypatch.setattr(_es, "enviar_aviso", lambda *a, **k: True)
    _cobrou_hoje(c)
    c.commit()
    assert est.fecho_do_dia(pool, CONTA, _domingo(hora_utc=22)) == []


# ------------------------------------------------------------------ os três canais
# "sempre nos 3 canais de comunicação ok?? whatts + email + push" — o dono, em
# 19/09/2026. A esteira nasceu mandando dois: o push ficou de fora porque o
# follow-up antigo é que o usava, e quando a esteira tomou o lugar dele o canal
# ficou órfão — montado, medido no card, e sem nada passando.

def _tres_canais(monkeypatch):
    """Intercepta os três canais. Devolve (zaps, pushes, emails)."""
    from finance import cockpit as ck
    from finance import email_sender as mail
    from finance import follow_up as fu
    zaps, pushes, emails = [], [], []
    monkeypatch.setattr(fu, "_zap_do_membro", lambda *a, **k: "86999990000")
    monkeypatch.setattr(fu, "_mandar_zap",
                        lambda *a, **k: zaps.append(a) or {"ok": True, "erro": "", "sid": "S1"})
    monkeypatch.setattr(ck, "enviar_push", lambda *a, **k: pushes.append((a, k)) or 1)
    monkeypatch.setattr(mail, "enviar_aviso", lambda *a, **k: emails.append((a, k)) or True)
    return zaps, pushes, emails


def _canais_registrados(c, origem):
    return dict(c.execute(
        "select canal, count(*) from aviso_envios where origem=%s group by canal",
        (origem,)).fetchall())


def test_a_cobranca_do_vendedor_sai_nos_TRES_canais(pool, c, monkeypatch):
    zaps, pushes, emails = _tres_canais(monkeypatch)
    c.execute("update membros set email='vendedor@x.com' where id=%s", (VEND,))
    _com_bola_nossa(c)
    cob = es.avaliar(c, CONTA, AGORA)["cobrancas"]
    c.commit()

    es.notificar(pool, CONTA, cob)
    assert zaps and pushes and emails, (zaps, pushes, emails)
    assert _canais_registrados(c, "esteira") == {"whatsapp": 1, "push": 1, "email": 1}
    # o push leva o token do clique — sem ele, o toque na notificação não volta
    linha = c.execute("select token from aviso_envios where canal='push'").fetchone()
    assert linha[0], "o push foi registrado sem token; o clique não teria como voltar"
    assert pushes[0][1]["token"] == linha[0]


def test_o_push_do_vendedor_leva_pra_fila_dele(pool, c, monkeypatch):
    _, pushes, _ = _tres_canais(monkeypatch)
    _com_bola_nossa(c)
    cob = es.avaliar(c, CONTA, AGORA)["cobrancas"]
    c.commit()
    es.notificar(pool, CONTA, cob)
    assert "/painel/follow-up?estado=atrasado" in pushes[0][0][5]


def test_zero_aparelho_com_push_nao_e_erro_e_fica_registrado(pool, c, monkeypatch):
    """O vendedor sem push instalado tem que APARECER no card — é como o dono
    descobre que o aviso daquela pessoa só chega por e-mail e WhatsApp."""
    from finance import cockpit as ck
    _tres_canais(monkeypatch)
    monkeypatch.setattr(ck, "enviar_push", lambda *a, **k: 0)
    _com_bola_nossa(c)
    cob = es.avaliar(c, CONTA, AGORA)["cobrancas"]
    c.commit()
    es.notificar(pool, CONTA, cob)
    ok, motivo = c.execute(
        "select ok, motivo from aviso_envios where origem='esteira' and canal='push'").fetchone()
    assert ok is False and motivo == "nenhum aparelho com push"


# --------------------------------------------------- o fecho e o e-mail da conta

def test_o_fecho_tambem_sai_no_push(pool, c, monkeypatch):
    _, pushes, _ = _tres_canais(monkeypatch)
    _cobrou_hoje(c)
    c.commit()
    r = es.fecho_do_dia(pool, CONTA, _fim_do_expediente())
    assert r and r[0]["push"] is True
    assert _canais_registrados(c, es.ORIGEM_FECHO).get("push") == 1


def test_o_fecho_vai_pro_EMAIL_CADASTRADO_NA_CONTA(pool, c, monkeypatch):
    """O MANOEL é dono sem e-mail no cadastro de membro. O endereço da empresa já
    está no parâmetro do resumo semanal — é de lá que o fecho o tira."""
    _, _, emails = _tres_canais(monkeypatch)
    c.execute("update membros set email=null where papel='dono'")
    c.execute("""update contas set resumo_semanal_emails='gestor@prime.com',
                        resumo_semanal_dono_emails='dono@prime.com' where id=%s""", (CONTA,))
    _cobrou_hoje(c)
    c.commit()

    es.fecho_do_dia(pool, CONTA, _fim_do_expediente())
    destinos = [a[0] for a, _ in emails]
    assert destinos == ["dono@prime.com", "gestor@prime.com"], destinos
    # e ficam registrados sem membro por trás — são endereços da conta, não pessoas
    linhas = c.execute("""select destino, membro_id from aviso_envios
                           where origem=%s and canal='email' and ok
                           order by destino""", (es.ORIGEM_FECHO,)).fetchall()
    assert linhas == [("dono@prime.com", None), ("gestor@prime.com", None)]


def test_o_mesmo_endereco_nao_recebe_o_fecho_duas_vezes(pool, c, monkeypatch):
    """O dono é membro com e-mail E está no parâmetro. Dois e-mails iguais no mesmo
    minuto é o que faz a pessoa criar regra de lixeira pro nosso remetente."""
    _, _, emails = _tres_canais(monkeypatch)
    c.execute("update contas set resumo_semanal_dono_emails='DONO@x.com' where id=%s", (CONTA,))
    _cobrou_hoje(c)
    c.commit()
    es.fecho_do_dia(pool, CONTA, _fim_do_expediente())
    assert [a[0] for a, _ in emails] == ["dono@x.com"]


def test_sem_o_parametro_o_fecho_segue_pelos_membros(pool, c, monkeypatch):
    """Parâmetro vazio não pode impedir o fecho de sair pra quem é membro."""
    _, _, emails = _tres_canais(monkeypatch)
    _cobrou_hoje(c)
    c.commit()
    es.fecho_do_dia(pool, CONTA, _fim_do_expediente())
    assert [a[0] for a, _ in emails] == ["dono@x.com"]


# --------------------------------------------------- quantos fecham amanhã
# "1 - pode fazer os quantos fecham amanha" — o dono, em 19/09/2026. Ele lia "7 na
# esteira" e não sabia quais somem antes da próxima cobrança; é a única parte do
# placar sobre a qual ainda dá pra fazer alguma coisa hoje.

def test_o_resumo_conta_quem_fecha_amanha(c):
    lid = _com_bola_nossa(c, nome="Vespera")
    outro = _com_bola_nossa(c, nome="Novo")
    es.entrar(c, CONTA, AGORA)
    _entrou_ha(c, lid, 5)        # amanhã ele está no dia 7 — o prazo final
    _entrou_ha(c, outro, 0)      # entrou hoje: amanhã é dia 2
    r = es.resumo(c, CONTA, None, AGORA)
    assert (r["na_esteira"], r["fecham_amanha"]) == (2, 1)


def test_quem_ja_fechou_nao_conta_como_fecha_amanha(c):
    lid = _com_bola_nossa(c)
    es.entrar(c, CONTA, AGORA)
    _entrou_ha(c, lid, 6)
    es.fechar_vencidos(c, CONTA, _fim_do_dia())
    assert es.resumo(c, CONTA, None, _fim_do_dia())["fecham_amanha"] == 0


def test_o_texto_do_fecho_diz_quantos_fecham_amanha():
    titulo, corpo = es.texto_fecho(
        {"tratou": 3, "na_esteira": 7, "fechados_sem_tratativa": 0, "fecham_amanha": 2},
        [("THIAGO", {"tratou": 1, "na_esteira": 4, "fecham_amanha": 2}),
         ("PEDRO", {"tratou": 2, "na_esteira": 3, "fecham_amanha": 0})])
    assert titulo == "📋 O dia fechou: 3 tratados, 7 na esteira, 2 fecham amanhã"
    assert corpo.split("\n")[0] == "· THIAGO — 1 tratados, 4 na esteira (2 fecham amanhã)"
    # quem não tem nada fechando amanhã não ganha um "(0 fecham amanhã)" pendurado
    assert corpo.split("\n")[1] == "· PEDRO — 2 tratados, 3 na esteira"
    assert "Fecham amanhã sem tratativa: 2." in corpo


def test_dia_sem_ninguem_vencendo_nao_fala_de_amanha():
    titulo, corpo = es.texto_fecho(
        {"tratou": 3, "na_esteira": 7, "fechados_sem_tratativa": 0, "fecham_amanha": 0},
        [("THIAGO", {"tratou": 1, "na_esteira": 4, "fecham_amanha": 0})])
    assert "amanhã" not in titulo and "amanhã" not in corpo


# --------------------------------------------------- o histórico é do vendedor

def test_o_aviso_manda_o_vendedor_escrever_o_historico():
    """"vendedor tem que criar o historico e bom notificar ele disso tambem" — o
    dono, 19/09/2026. É o mesmo buraco que o placar dele carrega: ligação e
    conversa pessoal não existem no sistema, e quem resolveu no telefone sem
    escrever aparece como quem não fez nada."""
    _, corpo = es.texto("THIAGO", [{"quem": "Isa", "dia": 1, "ultimo_dia": False}],
                        {"tratou": 2, "na_esteira": 8})
    assert "Escreva no histórico do lead" in corpo
    assert "o que não está escrito não conta" in corpo
