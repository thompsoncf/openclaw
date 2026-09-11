"""O follow-up automático (finance/follow_up): a escada, os seis estados, os
quatro degraus de cobrança e a trava do reagendamento.

O que estes testes protegem, em uma frase cada:
  * a próxima ação NASCE SOZINHA — se ela dependesse do vendedor, o dia 1 na
    Prime seria 273 campos em branco (é o que a produção dizia em 07/09);
  * a data da festa só aperta o prazo de quem VENDE festa (CLAUDE.md §6);
  * a mão do vendedor manda, mas só até o cliente falar de novo;
  * o mesmo atraso nunca cobra duas vezes, e abrir o card não encerra nada;
  * adiar em silêncio três vezes passa a exigir motivo.

Banco descartável, `agora` sempre fixo — o motor tem janela de atendimento, e um
teste que dependesse da hora real passaria ou falharia conforme o dia.
"""
import os
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from psycopg_pool import ConnectionPool

from finance import follow_up as fu
from finance import raio_x_perfil as rxp

EVENTOS = rxp.perfil("eventos")
RECORRENTE = rxp.perfil("consultoria")

MIG = Path(__file__).resolve().parent.parent / "db" / "migracoes"
CONTA = 7


def _proxima_quarta_12h() -> datetime:
    """Quarta-feira 12h UTC (09h em Brasília, dentro da janela padrão) — a
    PRÓXIMA, nunca hoje, então `AGORA` fica sempre de 1 a 7 dias no futuro.

    POR QUE NÃO É MAIS UMA DATA FIXA. Era `datetime(2026, 9, 9, 12, 0)`, e em
    09/09/2026 essa data virou HOJE: a suíte ficou vermelha na main sem ninguém
    ter tocado em código, e continuaria vermelha pra sempre.

    O motivo é que dois testes daqui comparam duas linhas do tempo diferentes.
    `test_a_mao_manda_ate_o_cliente_falar_de_novo` e
    `test_adiar_em_silencio_tres_vezes_passa_a_exigir_motivo` injetam a mensagem
    do cliente em `AGORA - 6h` / `AGORA - 1h` e exigem que ela seja mais NOVA que
    a marcação — mas o `criado_em` da marcação sai do relógio de verdade do banco
    (`follow_up_marcacoes.criado_em default now()`), não de `AGORA`. Enquanto o
    dia de hoje era anterior a 09/09 essas mensagens estavam no futuro e a
    comparação fechava sozinha; às 06:00 UTC de 09/09/2026 o relógio real passou
    por `AGORA - 6h` e a asserção inverteu.

    Ancorar numa quarta futura preserva o que a data fixa queria dizer (o dia da
    semana e a hora dentro da janela de trabalho) e tira o prazo de validade.

    O `+ 7` não é folga por desencargo: sem ele, a "próxima quarta" vista de uma
    terça à noite fica a 12h de distância, e um teste novo com deslocamento maior
    que isso voltaria a quebrar por relógio. Com ele a distância é sempre de 7 a
    13 dias, e nenhum deslocamento plausível dentro de um teste alcança o passado.
    """
    agora = datetime.now(timezone.utc)
    dias = (2 - agora.weekday()) % 7 + 7           # 2 = quarta; nunca menos de 7 dias
    return (agora + timedelta(days=dias)).replace(hour=12, minute=0, second=0,
                                                  microsecond=0)


AGORA = _proxima_quarta_12h()

_SQL = """
create table prospeccao (id bigserial primary key, conta_id bigint, empresa text, contato text,
  status text default 'novo', estagio text default 'lead', orcamento_id bigint,
  vendedor_id bigint, evento_em date, evento_tipo text, evento_convidados int,
  proximo_contato_em timestamptz, atualizado_em timestamptz default now(),
  criado_em timestamptz default now());
create table membros (id bigserial primary key, conta_id bigint, nome text, email text,
  papel text default 'vendedor', ativo boolean default true);
create table funil_avisos (id bigserial primary key, conta_id bigint, prospeccao_id bigint,
  estado text, nivel text, etapa text default '', ref_em timestamptz, simulado boolean default false,
  membro_id bigint, criado_em timestamptz default now());
create unique index uq_funil_aviso on funil_avisos
  (prospeccao_id, estado, nivel, etapa, ref_em, simulado);
create table funil_motivos_perda (id bigserial primary key, conta_id bigint,
  chave text, rotulo text, ordem int default 0, ativo boolean default true,
  exige_descricao boolean default false, criado_em timestamptz default now(),
  constraint uq_fmp unique (conta_id, chave));
create table funil_etapas (id bigserial primary key, conta_id bigint, chave text,
  rotulo text, ordem int default 0, fixa boolean default false, fase text default 'venda',
  prazo_min integer, gatilho text, gatilho_ativo boolean default false,
  teto_dias integer, renovacoes_max integer not null default 0,
  exige_justificativa boolean not null default true, renova_sozinho_h integer,
  saidas_permitidas text, toques_dias text,
  exige_motivo boolean not null default false, reativa_para text,
  sai_do_quadro boolean not null default false,
  agenda_ao_entrar boolean not null default false);
create table funil_regua (conta_id bigint primary key,
  gatilhos_modo text default 'off', cobranca_modo text default 'off',
  janela_dias text default '1,2,3,4,5,6', janela_abre time default '08:00',
  janela_fecha time default '19:00', sem_resposta_min int default 120,
  bola_nossa_min int default 240, bola_cliente_min int default 4320,
  escala_min int default 240, teto_avisos_dia int default 5,
  -- a coluna existe na 177; o stub não a tinha porque nada aqui escrevia
  -- na tabela até a aba do Follow-up ganhar o interruptor (07/09/2026)
  atualizado_em timestamptz not null default now());
create table conversas (id bigserial primary key, conta_id bigint, prospeccao_id bigint,
  canal text default 'whatsapp', visto_ate_id bigint);
-- o histórico existe desde a 177 e passou a ser lido aqui em 11/09/2026: a escada
-- ancorada na etapa (migração 233) precisa saber desde quando o lead está nela
create table funil_movimentos (id bigserial primary key, conta_id bigint, prospeccao_id bigint,
  de text, para text, motivo text, membro_id bigint, criado_em timestamptz default now());
create table mensagens (id bigserial primary key, conversa_id bigint, direcao text,
  texto text default '', criado_em timestamptz default now());
"""

_ETAPAS = [("novo", 0, "venda"), ("contatado", 10, "venda"), ("qualificado", 20, "venda"),
           ("proposta", 30, "venda"), ("ganho", 900, "fechamento"), ("perdido", 910, "fechamento")]


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_follow_up_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=3, open=True, kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_SQL)
        # a migração de verdade, não uma cópia dela: é o único jeito de o teste
        # perceber que a coluna nova não chegou em produção
        c.execute((MIG / "218_follow_up.sql").read_text(encoding="utf-8"))
        for ch, o, fase in _ETAPAS:
            c.execute("""insert into funil_etapas (conta_id, chave, rotulo, ordem, fase)
                         values (%s,%s,%s,%s,%s)""", (CONTA, ch, ch.capitalize(), o, fase))
        c.commit()
    yield p
    p.close()


@pytest.fixture()
def c(pool):
    with pool.connection() as con:
        con.execute("delete from follow_up_marcacoes")
        con.execute("delete from funil_avisos")
        con.execute("delete from mensagens")
        con.execute("delete from conversas")
        con.execute("delete from prospeccao")
        con.execute("delete from membros")
        con.execute("delete from funil_regua")
        con.commit()
        yield con
        con.rollback()


# ------------------------------------------------------------------ montadores

def _vend(c, nome="Pedro", papel="vendedor"):
    return c.execute("""insert into membros (conta_id, nome, email, papel)
                        values (%s,%s,%s,%s) returning id""",
                     (CONTA, nome, f"{nome.lower()}@x.com", papel)).fetchone()[0]


def _lead(c, vend=None, status="contatado", contato="Ana", evento_em=None, criado=None):
    return c.execute("""insert into prospeccao (conta_id, vendedor_id, contato, status, evento_em, criado_em)
                        values (%s,%s,%s,%s,%s,coalesce(%s, now())) returning id""",
                     (CONTA, vend, contato, status, evento_em, criado)).fetchone()[0]


def _conversa(c, lead):
    return c.execute("insert into conversas (conta_id, prospeccao_id) values (%s,%s) returning id",
                     (CONTA, lead)).fetchone()[0]


def _msg(c, conv, direcao, em, texto=""):
    return c.execute("""insert into mensagens (conversa_id, direcao, texto, criado_em)
                        values (%s,%s,%s,%s) returning id""",
                     (conv, direcao, texto, em)).fetchone()[0]


def _fala(c, lead, *pares):
    """(direcao, dias_atras) — monta a conversa de um lead de uma vez."""
    conv = _conversa(c, lead)
    for direcao, dias in pares:
        _msg(c, conv, direcao, AGORA - timedelta(days=dias))
    return conv


def _modo(c, modo="ligado", **kw):
    fu.config(c, CONTA)   # semeia a linha
    campos = ", ".join(f"{k}=%s" for k in kw)
    c.execute(f"update funil_regua set follow_up_modo=%s{', ' + campos if kw else ''} where conta_id=%s",
              (modo, *kw.values(), CONTA))


# ------------------------------------------------------------------ a âncora do tempo

def test_agora_esta_sempre_no_futuro_e_numa_quarta():
    """A invariante que os testes deste arquivo assumem sem dizer.

    Dois deles injetam mensagem em `AGORA - 6h` / `AGORA - 1h` e a comparam com um
    `criado_em` vindo do relógio do banco. Se `AGORA` deixar de estar no futuro,
    eles quebram sozinhos, num dia qualquer, sem nenhum commit — foi o que
    aconteceu em 09/09/2026. Esta asserção falha primeiro e diz o porquê, em vez
    de mandar quem está de plantão caçar `assert not True` em dois testes de
    follow-up.
    """
    folga = AGORA - datetime.now(timezone.utc)
    assert folga > timedelta(hours=24), (
        f"AGORA precisa estar pelo menos 1 dia à frente do relógio real; está {folga}")
    assert AGORA.weekday() == 2, "a janela padrão dos testes é quarta-feira"
    assert (AGORA.hour, AGORA.minute) == (12, 0), "12h UTC = 09h em Brasília"


# ------------------------------------------------------------------ a escada (pura)

def test_escada_torta_volta_pro_padrao():
    assert fu._escada("2,4,7,15") == (2, 4, 7, 15)
    assert fu._escada(" 3 , 5 ") == (3, 5)
    # config ilegível não pode virar prazo zero, que cobraria tudo de todo mundo
    for torto in ("", None, "abc", "0", "-1,-2"):
        assert fu._escada(torto) == (2, 4, 7, 15), torto


def _cfg(**kw):
    return dict(fu._PADRAO, sem_resposta_min=120, bola_nossa_min=240, **kw)


def test_a_bola_vem_antes_de_tudo():
    """Cliente esperando é mais urgente e mais acionável que card parado."""
    ult_in = AGORA - timedelta(hours=10)
    prazo, acao = fu.prazo_automatico(
        status="proposta", ult_in=ult_in, ult_out=AGORA - timedelta(days=9),
        criado_em=None, tentativas=0, evento_em=None, cfg=_cfg(), tem_data=True, agora=AGORA)
    assert prazo == ult_in + timedelta(minutes=240)
    assert "esperando" in acao


def test_lead_sem_resposta_nossa_conta_do_nascimento():
    nasceu = AGORA - timedelta(hours=5)
    prazo, acao = fu.prazo_automatico(
        status="novo", ult_in=None, ult_out=None, criado_em=nasceu, tentativas=0,
        evento_em=None, cfg=_cfg(), tem_data=True, agora=AGORA)
    assert prazo == nasceu + timedelta(minutes=120) and "ninguém falou" in acao


def test_proposta_tem_prazo_proprio_e_a_escada_sobe():
    out = AGORA - timedelta(days=1)
    p, a = fu.prazo_automatico(status="proposta", ult_in=None, ult_out=out, criado_em=None,
                               tentativas=1, evento_em=None, cfg=_cfg(), tem_data=True, agora=AGORA)
    assert p == out + timedelta(days=3) and "proposta" in a
    esperado = {1: 2, 2: 4, 3: 7, 4: 15, 9: 15}
    for tent, dias in esperado.items():
        p, _ = fu.prazo_automatico(status="contatado", ult_in=None, ult_out=out, criado_em=None,
                                   tentativas=tent, evento_em=None, cfg=_cfg(), tem_data=True, agora=AGORA)
        assert p == out + timedelta(days=dias), tent
    # do 4º toque a tela para de insistir e pergunta
    _, a4 = fu.prazo_automatico(status="contatado", ult_in=None, ult_out=out, criado_em=None,
                                tentativas=4, evento_em=None, cfg=_cfg(), tem_data=True, agora=AGORA)
    assert "encerra" in a4


def test_a_festa_perto_aperta_o_prazo_so_de_quem_vende_festa():
    """CLAUDE.md §6: o segundo relógio é do nicho, e a palavra também."""
    out = AGORA - timedelta(hours=2)          # conversou agorinha: prazo folgado
    festa = AGORA.date() + timedelta(days=12)
    p_ev, a_ev = fu.prazo_automatico(status="contatado", ult_in=None, ult_out=out, criado_em=None,
                                     tentativas=1, evento_em=festa, cfg=_cfg(), tem_data=True, agora=AGORA)
    # apertou: já vencido (a festa entrou na janela há 18 dias), e não o prazo folgado
    assert p_ev < AGORA and p_ev != out + timedelta(days=2) and "data" in a_ev
    p_rc, a_rc = fu.prazo_automatico(status="contatado", ult_in=None, ult_out=out, criado_em=None,
                                     tentativas=1, evento_em=festa, cfg=_cfg(), tem_data=False, agora=AGORA)
    assert p_rc == out + timedelta(days=2)
    for palavra in ("festa", "data", "convidados", "visita"):
        assert palavra not in a_rc


def test_o_prazo_da_festa_nao_muda_de_uma_passada_pra_outra():
    """O DEFEITO DE 11/09/2026, e o teste que faltava pra pegá-lo.

    O ramo da festa devolvia `agora` como prazo. Como o dedup do aviso é por
    `ref_em` (= o prazo), cada ciclo do poller inventava um fato novo e o aviso saía
    outra vez: no ensaio da conta 34 o lead 977 acumulou 442 avisos em quatro dias,
    441 com `ref_em` distinto. Ligado, ele sozinho comeria a cota diária do vendedor
    em meia hora, todo dia, represando o follow-up de verdade.

    O teste antigo (`..._aperta_o_prazo_...`) passava com o defeito de pé: ele
    chamava a função UMA vez, e um relógio errado só aparece quando se pergunta duas.
    """
    out = AGORA - timedelta(hours=2)
    festa = AGORA.date() + timedelta(days=12)
    prazos = {fu.prazo_automatico(status="contatado", ult_in=None, ult_out=out,
                                  criado_em=AGORA - timedelta(days=40), tentativas=1,
                                  evento_em=festa, cfg=_cfg(), tem_data=True,
                                  agora=AGORA + timedelta(minutes=m))[0]
              for m in (0, 2, 4, 120)}
    assert len(prazos) == 1, f"o prazo da festa mudou entre passadas: {sorted(prazos)}"


def test_a_janela_da_festa_nao_comeca_antes_de_o_lead_existir():
    """Lead cadastrado com a festa JÁ dentro da janela: a abertura da janela está no
    passado dele, e ancorar ali faria o lead nascer "atrasado há 20 dias" — número
    que nunca foi verdade. O relógio começa quando o lead chegou."""
    nasceu = AGORA - timedelta(hours=3)
    # a janela abriu há 25 dias (festa em 5, `fu_festa_dias` 30); o lead tem 3 horas
    p, _ = fu.prazo_automatico(status="contatado", ult_in=None, ult_out=AGORA - timedelta(hours=1),
                               criado_em=nasceu, tentativas=1,
                               evento_em=AGORA.date() + timedelta(days=5),
                               cfg=_cfg(), tem_data=True, agora=AGORA)
    assert p == nasceu


def test_festa_longe_nao_aperta_nada():
    out = AGORA - timedelta(hours=2)
    p, _ = fu.prazo_automatico(status="contatado", ult_in=None, ult_out=out, criado_em=None,
                               tentativas=1, evento_em=AGORA.date() + timedelta(days=200),
                               cfg=_cfg(), tem_data=True, agora=AGORA)
    assert p == out + timedelta(days=2)


def test_os_seis_estados():
    e = fu.estado_de
    assert e(AGORA + timedelta(days=2), AGORA - timedelta(hours=1), AGORA) == "andamento"
    assert e(AGORA + timedelta(days=2), AGORA - timedelta(days=5), AGORA) == "agendado"
    assert e(AGORA - timedelta(hours=3), AGORA - timedelta(days=5), AGORA) == "hoje"
    assert e(AGORA - timedelta(hours=30), AGORA - timedelta(days=5), AGORA) == "atrasado"
    assert e(AGORA - timedelta(hours=80), AGORA - timedelta(days=5), AGORA) == "critico"
    assert e(AGORA - timedelta(hours=80), None, AGORA, sem_acao=True) == "sem_acao"
    assert e(None, None, AGORA) == "sem_acao"


# ------------------------------------------------------------------ os leads (banco)

def test_a_conta_inteira_numa_consulta_com_estado_e_acao(c):
    v = _vend(c)
    # 1. cliente falou por último, ninguém respondeu → bola nossa, já venceu
    a = _lead(c, v, contato="Ana"); _fala(c, a, ("out", 6), ("in", 5))
    # 2. proposta enviada e ele sumiu há 9 dias → crítico
    b = _lead(c, v, status="proposta", contato="Bia"); _fala(c, b, ("in", 12), ("out", 9))
    # 3. falamos ontem, 1ª tentativa → ainda no prazo
    d = _lead(c, v, contato="Duda"); _fala(c, d, ("in", 3), ("out", 1))
    # 4. festa já passou → não há o que propor
    e = _lead(c, v, contato="Eva", evento_em=AGORA.date() - timedelta(days=3)); _fala(c, e, ("out", 20))
    linhas = {x["quem"]: x for x in fu.leads(c, CONTA, EVENTOS, AGORA)}
    assert linhas["Ana"]["estado"] == "critico" and linhas["Ana"]["bola"] == "aguardando vendedor"
    assert linhas["Bia"]["estado"] == "critico" and "proposta" in linhas["Bia"]["acao"]
    assert linhas["Duda"]["estado"] in ("agendado", "andamento")
    assert linhas["Eva"]["estado"] == "sem_acao" and linhas["Eva"]["prazo"] is None
    assert linhas["Bia"]["tentativas"] == 1 and linhas["Bia"]["bola"] == "aguardando cliente"


def test_lead_encerrado_nao_entra_na_fila(c):
    v = _vend(c)
    for st in ("ganho", "perdido"):
        lead = _lead(c, v, status=st, contato=st); _fala(c, lead, ("out", 30))
    assert fu.leads(c, CONTA, EVENTOS, AGORA) == []


def test_a_mao_manda_ate_o_cliente_falar_de_novo(c):
    """'Ele disse que retorna terça' vale — mas não vira um jeito de silenciar
    o lead pra sempre: mensagem nova é fato novo, e a escada recomeça."""
    v = _vend(c)
    lead = _lead(c, v, contato="Ana"); conv = _fala(c, lead, ("in", 9), ("out", 8))
    terca = AGORA + timedelta(days=4)
    # ONTEM o vendedor marcou (depois da última fala, que foi há 8 dias) — é o
    # instante da marcação que decide se ela é mais nova que a conversa, então ele
    # entra explícito. Antes de 09/09/2026 quem carimbava era o `now()` do banco, e
    # o teste passava só porque `AGORA` ainda era futuro; no dia em que a data
    # chegou, a comparação inverteu e o teste quebrou sozinho na main.
    fu.marcar(c, CONTA, lead, terca, acao="retorno que ele pediu", membro_id=v,
              agora=AGORA - timedelta(days=1))
    x = fu.leads(c, CONTA, EVENTOS, AGORA)[0]
    assert x["na_mao"] and x["prazo"] == terca and x["estado"] == "agendado"
    # o cliente escreve depois da marcação: a bola volta a ser nossa
    _msg(c, conv, "in", AGORA - timedelta(hours=6))
    y = fu.leads(c, CONTA, EVENTOS, AGORA)[0]
    assert not y["na_mao"] and "esperando" in y["acao"]


def test_ordem_da_fila_poe_a_festa_mais_perto_na_frente(c):
    v = _vend(c)
    longe = _lead(c, v, contato="Longe", evento_em=AGORA.date() + timedelta(days=300))
    perto = _lead(c, v, contato="Perto", evento_em=AGORA.date() + timedelta(days=40))
    sem = _lead(c, v, contato="Sem")
    for lead in (longe, perto, sem):
        _fala(c, lead, ("in", 30), ("out", 29))
    nomes = [x["quem"] for x in fu.ordenar(fu.leads(c, CONTA, EVENTOS, AGORA))]
    assert nomes == ["Perto", "Longe", "Sem"]


def test_resumo_e_painel_por_vendedor(c):
    p, j = _vend(c, "Pedro"), _vend(c, "Jacque")
    for dono, n in ((p, 3), (j, 1)):
        for i in range(n):
            lead = _lead(c, dono, contato=f"L{dono}{i}"); _fala(c, lead, ("in", 20), ("out", 19))
    linhas = fu.leads(c, CONTA, EVENTOS, AGORA)
    assert fu.resumo(linhas)["critico"] == 4 and fu.resumo(linhas)["ativos"] == 4
    gest = fu.por_vendedor(linhas)
    assert [(g["nome"], g["ativos"], g["critico"]) for g in gest] == [("Pedro", 3, 3), ("Jacque", 1, 1)]


def test_sincronizar_escreve_o_prazo_e_nao_encosta_no_marcado_na_mao(c):
    v = _vend(c)
    auto = _lead(c, v, contato="Auto"); _fala(c, auto, ("in", 20), ("out", 19))
    mao = _lead(c, v, contato="Mao"); _fala(c, mao, ("in", 20), ("out", 19))
    escolhido = AGORA + timedelta(days=5)
    fu.marcar(c, CONTA, mao, escolhido, membro_id=v, agora=AGORA)
    linhas = fu.leads(c, CONTA, EVENTOS, AGORA)
    assert fu.sincronizar(c, CONTA, linhas) == 1        # só o automático
    assert fu.sincronizar(c, CONTA, linhas) == 0        # nada mudou: não repete
    prazos = dict(c.execute("select contato, proximo_contato_em from prospeccao").fetchall())
    assert prazos["Mao"] == escolhido and prazos["Auto"] is not None


# ------------------------------------------------------------------ o reagendamento

def test_adiar_em_silencio_tres_vezes_passa_a_exigir_motivo(c):
    v = _vend(c)
    lead = _lead(c, v, contato="Ana"); conv = _fala(c, lead, ("in", 9), ("out", 8))
    # os adiamentos acontecem ao longo dos últimos dias, todos DEPOIS da última
    # fala (há 8 dias) e ANTES da conversa lá embaixo que zera a contagem
    assert fu.marcar(c, CONTA, lead, AGORA + timedelta(days=1), membro_id=v,
                     agora=AGORA - timedelta(days=5))["ok"]
    assert fu.marcar(c, CONTA, lead, AGORA + timedelta(days=2), membro_id=v,
                     agora=AGORA - timedelta(days=4))["ok"]
    r = fu.marcar(c, CONTA, lead, AGORA + timedelta(days=3), membro_id=v,
                  agora=AGORA - timedelta(days=3))
    assert r == {"ok": False, "erro": "motivo_obrigatorio"}
    # com motivo, passa
    assert fu.marcar(c, CONTA, lead, AGORA + timedelta(days=3), membro_id=v,
                     motivo="noiva viajou", agora=AGORA - timedelta(days=2))["ok"]
    # e falar com o cliente zera a contagem: adiar depois de conversar é trabalho
    _msg(c, conv, "out", AGORA - timedelta(hours=1))
    assert not fu.exige_motivo(c, CONTA, lead)
    assert fu.marcar(c, CONTA, lead, AGORA + timedelta(days=6), membro_id=v, agora=AGORA)["ok"]


def test_o_historico_guarda_quem_adiou_e_por_que(c):
    v = _vend(c, "Pedro")
    lead = _lead(c, v); _fala(c, lead, ("in", 9), ("out", 8))
    fu.marcar(c, CONTA, lead, AGORA + timedelta(days=1), acao="ligar", membro_id=v, motivo="pediu terça", agora=AGORA)
    fu.marcar(c, CONTA, lead, AGORA + timedelta(days=2), membro_id=None, automatico=True, agora=AGORA)
    h = fu.historico(c, CONTA, lead)
    assert len(h) == 2 and h[0]["automatico"] and h[0]["quem"] == "o sistema"
    assert h[1]["quem"] == "Pedro" and h[1]["motivo"] == "pediu terça" and h[1]["acao"] == "ligar"
    # o contador do card conta só o que foi feito na mão depois da última mensagem
    assert fu.leads(c, CONTA, EVENTOS, AGORA)[0]["adiados"] == 1


# ------------------------------------------------------------------ a cobrança

def test_desligado_nao_cobra_ninguem(c):
    v = _vend(c)
    lead = _lead(c, v); _fala(c, lead, ("in", 20), ("out", 19))
    assert fu.avaliar(c, CONTA, AGORA, EVENTOS) == {"avisos": 0, "simulados": 0,
                                                    "represados": 0, "pendentes": []}
    assert c.execute("select count(*) from funil_avisos").fetchone()[0] == 0


def test_ensaio_grava_o_que_teria_mandado_e_nao_manda(c):
    v = _vend(c)
    lead = _lead(c, v); _fala(c, lead, ("in", 20), ("out", 19))
    _modo(c, "observando")
    r = fu.avaliar(c, CONTA, AGORA, EVENTOS)
    assert r["simulados"] == 1 and r["avisos"] == 0 and r["pendentes"] == []
    assert c.execute("select count(*) from funil_avisos where simulado").fetchone()[0] == 4


def test_o_mesmo_atraso_nunca_cobra_duas_vezes(c):
    v = _vend(c)
    lead = _lead(c, v, contato="Ana"); conv = _fala(c, lead, ("in", 20), ("out", 19))
    _modo(c, "ligado")
    r1 = fu.avaliar(c, CONTA, AGORA, EVENTOS)
    assert r1["avisos"] == 1 and r1["pendentes"][0]["degrau"] == "a72"
    # segunda passada, mesmo fato: nada
    assert fu.avaliar(c, CONTA, AGORA, EVENTOS)["avisos"] == 0
    # o vendedor MANDA MENSAGEM: fato novo, prazo novo — e o lead sai da fila
    _msg(c, conv, "out", AGORA - timedelta(minutes=5))
    assert fu.avaliar(c, CONTA, AGORA, EVENTOS)["avisos"] == 0
    assert fu.leads(c, CONTA, EVENTOS, AGORA)[0]["estado"] in ("andamento", "agendado")


def test_abrir_o_card_nao_encerra_o_alerta(c):
    """Só ação registrada encerra. Um update em `atualizado_em` — que é o que
    abrir/arrastar o card faz — não muda o fato, logo o aviso continua de pé."""
    v = _vend(c)
    lead = _lead(c, v); _fala(c, lead, ("in", 20), ("out", 19))
    _modo(c, "ligado")
    fu.avaliar(c, CONTA, AGORA, EVENTOS)
    c.execute("update prospeccao set atualizado_em=now(), status='qualificado' where id=%s", (lead,))
    x = fu.leads(c, CONTA, EVENTOS, AGORA)[0]
    assert x["estado"] == "critico" and x["atraso_h"] > 72


def test_os_degraus_sobem_e_o_gestor_entra_a_partir_de_48h(c):
    v = _vend(c)
    # atraso de ~26h: pegou 'venc' e 'a24', ainda não escalou
    lead = _lead(c, v, status="proposta"); _fala(c, lead, ("in", 10), ("out", 4))
    _modo(c, "ligado")
    r = fu.avaliar(c, CONTA, AGORA, EVENTOS)
    assert r["pendentes"][0]["degrau"] == "a24" and r["pendentes"][0]["nivel"] == "vendedor"
    niveis = {x[0] for x in c.execute("select nivel from funil_avisos").fetchall()}
    assert niveis == {"venc", "a24"}


def test_o_teto_represa_e_nao_perde_o_aviso(c):
    v = _vend(c)
    for i in range(4):
        lead = _lead(c, v, contato=f"L{i}"); _fala(c, lead, ("in", 20), ("out", 19))
    _modo(c, "ligado", fu_teto_dia=2)
    r = fu.avaliar(c, CONTA, AGORA, EVENTOS)
    assert r["avisos"] == 2 and r["represados"] == 2
    # amanhã o teto zera e os represados saem: o dedup é pelo FATO, não pelo dia
    c.execute("update funil_avisos set criado_em = %s", (AGORA - timedelta(days=1),))
    r2 = fu.avaliar(c, CONTA, AGORA, EVENTOS)
    assert r2["avisos"] == 2 and r2["represados"] == 0


def test_fora_do_expediente_ninguem_e_acordado(c):
    v = _vend(c)
    lead = _lead(c, v); _fala(c, lead, ("in", 20), ("out", 19))
    _modo(c, "ligado")
    madrugada = AGORA.replace(hour=5)      # 02h em Brasília
    assert fu.avaliar(c, CONTA, madrugada, EVENTOS)["avisos"] == 0
    domingo = datetime(2026, 9, 13, 15, 0, tzinfo=timezone.utc)   # 12h de domingo
    assert fu.avaliar(c, CONTA, domingo, EVENTOS)["avisos"] == 0


def test_quem_esta_no_prazo_ou_sem_acao_nao_e_cobrado(c):
    v = _vend(c)
    ok = _lead(c, v, contato="Ok"); _fala(c, ok, ("in", 2), ("out", 1))
    passou = _lead(c, v, contato="Passou", evento_em=AGORA.date() - timedelta(days=2))
    _fala(c, passou, ("in", 30), ("out", 29))
    _modo(c, "ligado")
    assert fu.avaliar(c, CONTA, AGORA, EVENTOS)["avisos"] == 0


# ------------------------------------------------------------------ o nicho

def test_so_o_perfil_de_eventos_ganhou_a_tela_nesta_rodada():
    """Combinado com o dono em 07/09: eventos primeiro, na Prime; o recorrente
    entra quando a régua provar que funciona com gente usando."""
    assert fu.PERFIS_COM_TELA == ("eventos",)
    assert RECORRENTE["chave"] not in fu.PERFIS_COM_TELA
    assert rxp.perfil("hortifruti")["chave"] not in fu.PERFIS_COM_TELA


def test_a_config_le_a_janela_da_regua_e_nao_inventa_outra(c):
    cfg = fu.config(c, CONTA)
    assert cfg["follow_up_modo"] == "off" and cfg["fu_toques"] == (2, 4, 7, 15)
    assert cfg["fu_teto_dia"] == 15 and cfg["fu_festa_dias"] == 30
    # os prazos da conversa são os MESMOS da régua — uma configuração só
    assert cfg["bola_nossa_min"] == 240 and cfg["janela_abre"].hour == 8


# ------------------------------------------------------------------ a tela

def _tela(**ctx):
    import web.painel_follow_up as pfu  # noqa: F401 — registra o template
    from web.portal import _env
    # `cfg`, `caps` e `tem_follow_up` entraram em 07/09/2026, quando a tela virou
    # aba de Prospecção: a barra de abas lê os dois últimos, e o bloco "como
    # funciona" tira os prazos do `cfg` da conta em vez de escrevê-los no texto.
    base = dict(perfil=EVENTOS, papel="dono", topo=fu.resumo([]), fila=[], sobrando=0,
                estado="critico", vend_f=None, etapa_f="", vendedores=[], etapas=[],
                gestao=[], modo="off", rotulo=fu.ROTULO, emoji=fu.EMOJI,
                cfg=dict(fu._PADRAO), tem_follow_up=True, raio_x_perfil=EVENTOS,
                caps={"vendas": True, "origens": True, "financeiro": True, "gerir": True},
                br=pfu._br, tempo=pfu._tempo, adia_max=fu.ADIAMENTOS_ATE_MOTIVO, erro="",
                resumo_msg=pfu._resumo_msg, quando_curto=pfu._quando_curto)
    t = _env.get_template("follow_up")
    return "".join(t.blocks["conteudo"](t.new_context(dict(base, **ctx))))


def _linha(**kw):
    d = {"id": 1, "status": "contatado", "vendedor_id": 5, "vendedor": "Pedro", "quem": "Roberta",
         "evento_em": date(2026, 9, 19), "evento_tipo": "Casamento", "convidados": 120,
         "ult_in": None, "ult_out": None, "ult": None, "tentativas": 1,
         "prazo": AGORA - timedelta(days=3), "acao": "mandar proposta hoje", "na_mao": False,
         "adiados": 0, "adiado_por": None, "estado": "critico", "atraso_h": 94,
         "parado_h": 94, "bola": "aguardando cliente", "faltam": 12, "msg": None}
    d.update(kw)
    return d


def test_a_tela_mostra_o_que_o_dono_pediu_em_cada_card():
    html = _tela(fila=[_linha()], topo=dict(fu.resumo([_linha()]), com_festa=1))
    for pedaco in ("Roberta", "🚨 Crítico", "aguardando cliente", "mandar proposta hoje",
                   "3d</b> sem interação", "1 tentativa", "Casamento", "120 convidados", "Pedro",
                   "FOLLOW-UPS HOJE", "CRÍTICOS +72H", "SEM PRÓXIMA AÇÃO"):
        assert pedaco in html, pedaco


def test_a_tela_marca_quem_foi_adiado_em_serie():
    html = _tela(fila=[_linha(adiados=3, adiado_por=5)])
    assert "🔁 adiado 3× sem falar" in html
    assert "obrigatório" in html          # o campo motivo já avisa antes de recusar
    # o selo é UM. Antes o número vinha no selo e de novo na linha de detalhes
    # logo abaixo — a mesma informação duas vezes no mesmo campo de visão. A
    # repetição que sobrou está dentro do formulário, explicando a exigência.
    assert html.count('class="fu-pill hoje"') == 1
    assert "adiado <b>3×</b>" not in html


def test_quem_adiou_uma_ou_duas_vezes_aparece_na_linha_de_detalhes():
    """Abaixo da trava não há selo — mas o número precisa aparecer em algum
    lugar, senão o vendedor só descobre o histórico quando é recusado."""
    html = _tela(fila=[_linha(adiados=2, adiado_por=5)])
    assert "adiado <b>2×</b> sem mensagem no meio" in html
    assert "fu-pill hoje" not in html


def test_o_vendedor_nao_ve_o_painel_da_gestao():
    com = _tela(papel="dono", gestao=[{"id": 5, "nome": "Pedro", "ativos": 3, "adiados": 0,
                                       **{e: 0 for e in fu.ESTADOS}}])
    sem = _tela(papel="vendedor", gestao=[])
    assert "Por vendedor" in com and "Por vendedor" not in sem


def test_a_tela_avisa_quando_os_avisos_estao_desligados_ou_em_ensaio():
    """A frase mudou em 07/09/2026 (era "os avisos automáticos estão desligados…
    ligue na Régua"): agora o interruptor está aqui, e o aviso só diz em que pé
    a coisa está. O que se checa é o ESTADO dito na tela, não o texto antigo."""
    assert "Está <b>desligado</b>" in _tela(modo="off")
    assert "Está em <b>ensaio</b>" in _tela(modo="observando")
    ligado = _tela(modo="ligado")
    assert "Está <b>desligado</b>" not in ligado and "Está em <b>ensaio</b>" not in ligado


def test_o_vocabulario_de_festa_so_aparece_pra_quem_vende_festa():
    """CLAUDE.md §6 — o mesmo teste que o Raio-X faz, no card do follow-up."""
    rc = _tela(perfil=RECORRENTE, fila=[_linha()])
    for palavra in ("festa", "Casamento", "convidados", "sem data definida"):
        assert palavra not in rc, palavra
    assert "Roberta" in rc and "3d</b> sem interação" in rc


def test_a_volta_nao_aceita_endereco_de_fora():
    import web.painel_follow_up as pfu
    assert pfu._volta("/painel/follow-up?estado=hoje", "") == "/painel/follow-up?estado=hoje"
    assert pfu._volta("/painel/follow-up", "falhou") == "/painel/follow-up?erro=falhou"
    for hostil in ("https://outro.site", "//evil.com", "/painel/empresa", "", None):
        assert pfu._volta(hostil, "").startswith("/painel/follow-up")


def test_a_chave_de_ligar_saiu_da_regua_e_o_nicho_continua_valendo():
    """Era "a chave de ligar só aparece no nicho que tem a tela", e ficava na
    Régua. Em 07/09/2026 o dono mandou tirá-la de lá — mas a intenção original
    continua sendo protegida, agora em dois lugares: a Régua não oferece mais a
    chave a ninguém, e quem não tem a tela não vê nem a ABA (portão
    `tem_follow_up` na barra), então não há chave sem tela em canto nenhum."""
    import web.painel_prospeccao as pp
    from web.portal import _env
    t = _env.get_template("prospeccao_regua")
    base = dict(conta=None, aviso=None, etapas=[], conv=[], eventos=[], unidades=[],
                dias_on={1, 2, 3, 4, 5, 6}, n_mov=0, gerencia=True, request=None,
                caps={"vendas": True, "origens": True}, raio_x_perfil=EVENTOS,
                tem_follow_up=True,
                rot_ramo="eventos", janela_herda=True,
                esc={"n": "", "u": "h", "ph": "4", "herda": True},
                teto={"v": "", "ph": "5", "herda": True},
                fup={"proposta": {"v": "", "ph": "3", "herda": True},
                     "toques": {"v": "", "ph": "2,4,7,15", "herda": True},
                     "festa": {"v": "", "ph": "30", "herda": True, "tem": True},
                     "teto": {"v": "", "ph": "15", "herda": True}},
                cfg=dict(fu._PADRAO, gatilhos_modo="off", cobranca_modo="off",
                         janela_abre=time(8), janela_fecha=time(19), teto_avisos_dia=5,
                         sem_resposta_min=120, bola_nossa_min=240, bola_cliente_min=4320,
                         escala_min=240, janela_dias="1,2,3,4,5,6"))
    bloco = t.blocks["conteudo"]
    regua = "".join(bloco(t.new_context(base)))
    # A trava é sobre a CHAVE DE LIGAR, não sobre a palavra: desde 11/09/2026 a
    # Régua carrega os PRAZOS do follow-up (parametrização — o dono precisa alcançar
    # o número sem deploy). Procurar o texto "Follow-up automático" confundia as duas
    # coisas e barraria a tela certa; o que não pode voltar é o seletor de modo.
    assert 'name="follow_up_modo"' not in regua, "a chave voltou pra Régua"
    assert "Prazos do follow-up" in regua, "os prazos do follow-up sumiram da Régua"
    # os dois motores que continuam sendo dela
    assert "Gatilhos das etapas" in regua and "Cobrança por prazo" in regua
    # e a chave está na tela do Follow-up, com o portão do nicho na aba
    assert 'action="/painel/follow-up/modo"' in _tela()
    assert "{% if tem_follow_up %}" in pp._navbar("funil")


def test_o_banco_recusa_modo_inventado(c):
    fu.config(c, CONTA)
    with pytest.raises(Exception):
        c.execute("update funil_regua set follow_up_modo='talvez' where conta_id=%s", (CONTA,))


def _conta_row(slug, cidade="TERESINA"):
    """Uma linha de `conta_logada` como ela é de verdade: a cidade no 7, o slug do
    nicho no 16. Foi confundir os dois que fez o menu sumir na Prime (07/09) —
    ver tests/test_nicho_da_conta.py."""
    return (34, "pj", "Empresa", "e@x.com", "pro", "ativa", None, cidade,
            False, None, False, True, True, False, True, False, slug)


def test_o_perfil_de_eventos_e_o_unico_que_liga_a_chave():
    from web.painel_prospeccao import _tem_follow_up
    assert _tem_follow_up(_conta_row("eventos")) is True
    assert _tem_follow_up(_conta_row("consultoria")) is False
    assert _tem_follow_up(_conta_row("hortifruti")) is False
    assert _tem_follow_up(_conta_row(None)) is False
    # a cidade nunca decide nada: era o índice que estava sendo lido por engano
    assert _tem_follow_up(_conta_row("eventos", cidade="consultoria")) is True
    assert _tem_follow_up((1, "curta")) is False        # mock de teste não quebra a tela


# ------------------------------------------------------------------ o balão da conversa

def test_o_card_mostra_onde_a_conversa_parou(c):
    """O vendedor via o lead sem uma palavra do que havia dentro — a mesma coisa
    que o funil corrigiu em 02/09 e que faltava aqui."""
    v = _vend(c)
    lead = _lead(c, v, contato="Ana")
    conv = _conversa(c, lead)
    _msg(c, conv, "out", AGORA - timedelta(days=9), "mandei os valores")
    mid = _msg(c, conv, "in", AGORA - timedelta(days=8), "vou ver com meu marido e te falo")
    x = fu.leads(c, CONTA, EVENTOS, AGORA)[0]
    assert x["msg"]["texto"] == "vou ver com meu marido e te falo"
    assert x["msg"]["nova"] is True and x["msg"]["minha"] is False
    # depois de o vendedor abrir o Inbox, a bolinha verde apaga
    c.execute("update conversas set visto_ate_id=%s where id=%s", (mid, conv))
    assert fu.leads(c, CONTA, EVENTOS, AGORA)[0]["msg"]["nova"] is False


def test_a_ultima_mensagem_vale_a_mais_nova_entre_os_canais(c):
    v = _vend(c)
    lead = _lead(c, v)
    wa = _conversa(c, lead)
    _msg(c, wa, "in", AGORA - timedelta(days=5), "pelo whatsapp")
    email = c.execute("""insert into conversas (conta_id, prospeccao_id, canal)
                         values (%s,%s,'email') returning id""", (CONTA, lead)).fetchone()[0]
    _msg(c, email, "out", AGORA - timedelta(days=2), "pelo e-mail")
    x = fu.leads(c, CONTA, EVENTOS, AGORA)[0]
    assert x["msg"]["texto"] == "pelo e-mail" and x["msg"]["minha"] is True


def test_lead_sem_conversa_nao_ganha_balao(c):
    v = _vend(c)
    _lead(c, v)
    assert fu.leads(c, CONTA, EVENTOS, AGORA)[0]["msg"] is None


def test_o_balao_aparece_na_tela_com_o_texto_cortado():
    longa = "esse é um texto bem comprido que não cabe numa linha só do card e precisa ser cortado"
    html = _tela(fila=[_linha(msg={"texto": longa, "em": AGORA - timedelta(hours=3),
                                   "nova": True, "minha": False})])
    assert 'class="fu-msg nova"' in html and "…" in html
    assert longa not in html          # cortado, não inteiro
    sem = _tela(fila=[_linha(msg=None)])
    assert 'class="fu-msg' not in sem


# ------------------------------------------------------------------ remarcar em um toque

def test_a_tela_oferece_remarcar_em_um_toque():
    html = _tela(fila=[_linha()])
    assert 'name="dias" value="1"' in html and ">Amanhã<" in html
    assert 'name="dias" value="3"' in html and 'name="dias" value="7"' in html
    assert "Outra data" in html


def test_batida_a_trava_o_toque_rapido_some_e_o_motivo_vira_obrigatorio():
    """Um toque não tem onde escrever motivo — então, a um adiamento da trava,
    o único caminho é o formulário."""
    perto = _tela(fila=[_linha(adiados=fu.ADIAMENTOS_ATE_MOTIVO - 1)])
    assert 'name="dias"' not in perto
    assert "obrigatório" in perto and "required" in perto
    assert "Remarcar com motivo" in perto
    longe = _tela(fila=[_linha(adiados=0)])
    assert 'name="dias"' in longe


def test_a_ficha_do_lead_abre_no_endereco_que_existe():
    """O link apontava pra /painel/prospeccao/lead/{id}, que não existe — e o
    FastAPI devolvia {"detail":"Not Found"} sem passar por tela nenhuma."""
    import web.painel_prospeccao as pp  # noqa: F401
    from fastapi.routing import APIRoute
    html = _tela(fila=[_linha(id=931)])
    assert 'href="/painel/prospeccao/931"' in html
    assert "/painel/prospeccao/lead/" not in html
    # e o endereço existe mesmo, na tabela de rotas
    caminhos = {r.path for r in pp.router.routes if isinstance(r, APIRoute)}
    assert "/painel/prospeccao/{alvo_id}" in caminhos


# ------------------------------------------------------------------ abrir a conversa

def test_a_previa_carrega_a_conversa_e_a_aba(c):
    """O balão de web/balao_conversa precisa de (conversa_id, aba) — sem isso o
    card mostra o texto e não tem como abrir nada."""
    v = _vend(c)
    zap = _lead(c, v, contato="Zap")
    conv = _conversa(c, zap)
    _msg(c, conv, "in", AGORA - timedelta(days=8), "oi")
    mail = _lead(c, v, contato="Mail")
    cm = c.execute("""insert into conversas (conta_id, prospeccao_id, canal)
                      values (%s,%s,'email') returning id""", (CONTA, mail)).fetchone()[0]
    _msg(c, cm, "in", AGORA - timedelta(days=8), "bom dia")
    linhas = {x["quem"]: x for x in fu.leads(c, CONTA, EVENTOS, AGORA)}
    assert linhas["Zap"]["msg"]["conversa_id"] == conv and linhas["Zap"]["msg"]["aba"] == "conversas"
    assert linhas["Mail"]["msg"]["conversa_id"] == cm and linhas["Mail"]["msg"]["aba"] == "emails"


def test_a_previa_e_o_botao_que_abre_a_conversa():
    html = _tela(fila=[_linha(msg={"texto": "vou ver com meu marido", "em": AGORA,
                                   "nova": True, "minha": False,
                                   "conversa_id": 4242, "aba": "conversas"})])
    assert "kbAbrirChat(event,4242,'conversas'" in html
    assert 'class="fu-msg abre nova"' in html and "💬" in html


def test_sem_conversa_a_previa_nao_vira_botao():
    """Lead com mensagem mas sem id de conversa (dado torto) não pode virar um
    botão que abre o nada."""
    html = _tela(fila=[_linha(msg={"texto": "oi", "em": AGORA, "nova": False,
                                   "minha": True, "conversa_id": None, "aba": "conversas"})])
    assert "kbAbrirChat(event," not in html and 'class="fu-msg"' in html


def test_a_tela_carrega_o_balao_da_conversa():
    """CSS e JS vêm de web/balao_conversa pelas globais do Jinja — se a tela
    esquecer de injetar, o botão existe e o clique não faz nada."""
    html = _tela(fila=[_linha(msg={"texto": "oi", "em": AGORA, "nova": False, "minha": True,
                                   "conversa_id": 7, "aba": "conversas"})])
    assert ".chatpop{" in html and "function kbAbrirChat" in html


# ------------------------------------------------------------------ a rota de remarcar

@pytest.fixture()
def rota(monkeypatch, pool, c):
    """A rota de verdade, com pool e sessão — a camada que não tinha teste
    nenhum, que foi por onde o link quebrado da ficha passou."""
    import web.painel_follow_up as pfu
    from web import portal as pt
    linha = (CONTA, "pj", "Prime", "e@x.com", "pro", "ativa", None, "TERESINA",
             False, None, False, True, True, False, True, False, "eventos")
    monkeypatch.setattr(pfu, "get_pool", lambda: pool)
    monkeypatch.setattr(pfu, "conta_logada", lambda r: linha)
    req = SimpleNamespace(session={"conta_id": CONTA, "papel": "dono", "membro_id": None},
                          state=SimpleNamespace(), query_params={})
    return pfu, req


def _marcado(c, lead):
    return c.execute("""select prazo_em, acao from follow_up_marcacoes
                         where prospeccao_id=%s order by id desc limit 1""", (lead,)).fetchone()


def test_outra_data_grava_no_fuso_de_brasilia(rota, c):
    """14:30 em Brasília é 17:30 UTC. Um fuso trocado aqui faria o prazo vencer
    três horas antes ou depois do combinado, todo dia, sem ninguém notar."""
    pfu, req = rota
    lead = _lead(c, _vend(c))
    c.commit()
    r = pfu.follow_up_reagendar(req, lead_id=lead, quando="2026-09-25", hora="14:30",
                                acao="ligar pra ela", volta="/painel/follow-up?estado=critico")
    assert r.status_code == 303 and r.headers["location"] == "/painel/follow-up?estado=critico"
    prazo, acao = _marcado(c, lead)
    assert prazo == datetime(2026, 9, 25, 17, 30, tzinfo=timezone.utc) and acao == "ligar pra ela"


def test_o_botao_rapido_marca_as_nove_da_manha(rota, c):
    pfu, req = rota
    lead = _lead(c, _vend(c))
    c.commit()
    r = pfu.follow_up_reagendar(req, lead_id=lead, dias="3", volta="/painel/follow-up")
    assert r.status_code == 303 and "erro" not in r.headers["location"]
    prazo, _ = _marcado(c, lead)
    assert prazo.astimezone(timezone.utc).hour == 12      # 09h de Brasília
    # A CONTA É EM BRASÍLIA, dos dois lados. O botão marca "daqui a 3 dias" a
    # partir do dia LOCAL; comparar com `date.today()` em UTC quebra das 21h à
    # meia-noite, quando o UTC já virou e o Brasil não — foi assim que este teste
    # falhou às 21h13 de 07/09, com o produto certo. Um teste que passa ou falha
    # conforme a hora do dia é pior que teste nenhum: ensina a ignorar vermelho.
    hoje_br = (datetime.now(timezone.utc) + pfu._UTC_BR).date()
    assert ((prazo + pfu._UTC_BR).date() - hoje_br).days == 3


def test_data_vazia_ou_torta_volta_com_recado_e_nao_grava(rota, c):
    pfu, req = rota
    lead = _lead(c, _vend(c))
    c.commit()
    for ruim in ("", "amanhã", "2026-13-45"):
        r = pfu.follow_up_reagendar(req, lead_id=lead, quando=ruim, volta="/painel/follow-up")
        assert r.headers["location"] == "/painel/follow-up?erro=data_invalida", ruim
    assert _marcado(c, lead) is None
    # e um número de dias fora da faixa cai no mesmo lugar, em vez de marcar 2030
    r = pfu.follow_up_reagendar(req, lead_id=lead, dias="9999", volta="/painel/follow-up")
    assert r.headers["location"] == "/painel/follow-up?erro=data_invalida"
    assert _marcado(c, lead) is None


# ------------------------------------------------- a tela virou aba (07/09/2026)

def test_a_tela_desenha_a_barra_de_abas_da_prospeccao():
    """O pedido do dono: "follow up e origens faz mais sentido dentro da aba
    prospecção, por que está tudo relacionado". A barra é a MESMA do funil
    (`_navbar`), não uma cópia — cópia foi como a aba "Quem atacar" sumiu."""
    html = _tela()
    assert '<nav class="pnavbar"' in html
    assert 'href="/painel/prospeccao"' in html and 'href="/painel/prospeccao/regua"' in html
    # e é ELA que está acesa
    assert 'href="/painel/follow-up">📅 Follow-up</a>' in html
    import re
    aba = re.search(r'<a class="pnav([^"]*)" href="/painel/follow-up"', html)
    assert aba and " on" in aba.group(1), "a aba do Follow-up não fica acesa na própria tela"


def test_o_interruptor_esta_na_tela_e_nao_manda_mais_pra_regua():
    """O interruptor saiu da Régua (decisão do dono, 07/09/2026). A tela dizia
    "ligue na Régua do funil" — mandava a pessoa embora pra ligar o que estava
    olhando."""
    html = _tela(modo="off")
    assert 'action="/painel/follow-up/modo"' in html
    for rotulo in ("Desligado", "Observando", "Ligado"):
        assert f'>{rotulo}</button>' in html
    assert "Régua do funil</a>" not in html, "a tela ainda empurra pra Régua pra ligar"


def test_o_vendedor_le_o_estado_mas_nao_liga_nem_desliga():
    """Ligar o follow-up muda o que a CONTA INTEIRA recebe — é de dono e gestor,
    a mesma regra que a Régua já aplicava por `gerencia`."""
    html = _tela(papel="vendedor", modo="observando")
    assert 'action="/painel/follow-up/modo"' not in html
    assert "em ensaio" in html          # ele vê em que pé está


def test_as_regras_saem_da_config_da_conta_nao_do_texto():
    """O bloco "como funciona" mostra os prazos DESTA conta. Escrever "3 dias" no
    texto viraria mentira na primeira conta que mexesse na régua."""
    html = _tela(cfg=dict(fu._PADRAO, fu_proposta_dias=5, fu_toques=(1, 9), fu_festa_dias=20,
                          fu_teto_dia=7))
    assert "cobrar retorno em <b>5 dias</b>" in html
    assert "<b>1d</b>" in html and "<b>9d</b>" in html
    assert "<b>20 dias</b>" in html and "<b>7 avisos por dia</b>" in html
    # os quatro degraus e as duas travas, que ninguém sabia que existiam
    assert "48h" in html and "vendedor + gestor" in html
    assert f"<b>{fu.ADIAMENTOS_ATE_MOTIVO} adiamentos seguidos</b>" in html
    # e o que mais importa saber: o relógio não é o card
    import re
    liso = re.sub(r"\s+", " ", html)
    assert "não encerram alerta nenhum" in liso


def test_o_relogio_da_festa_so_aparece_pra_quem_vende_data(monkeypatch):
    """CLAUDE.md §6: o segundo relógio é do nicho. Hoje só o perfil de eventos
    tem a tela, mas o texto não pode falar de festa por estar escrito na mão —
    ele pergunta ao perfil."""
    assert "sem proposta vence <b>hoje</b>" in _tela(perfil=EVENTOS)
    assert "sem proposta vence <b>hoje</b>" not in _tela(perfil=RECORRENTE)


# ------------------------------------------- ligar/desligar na própria tela

def _modo_no_banco(c):
    return c.execute("select follow_up_modo from funil_regua where conta_id=%s",
                     (CONTA,)).fetchone()[0]


def _post_modo(rota, valor, papel="dono"):
    """Chama a rota como o FastAPI chama: o `modo` vem por `Form(...)`, e o
    handler é síncrono de propósito (ver o docstring dele — async com banco
    síncrono trava o event loop)."""
    from types import SimpleNamespace
    pfu, req = rota
    req = SimpleNamespace(session=dict(req.session, papel=papel),
                          state=req.state, query_params={})
    return pfu.follow_up_modo(req, modo=valor)


def test_o_dono_liga_o_follow_up_na_propria_tela(rota, c):
    """O interruptor era da Régua, e a tela dizia "ligue na Régua do funil" —
    mandava a pessoa embora pra ligar o que ela estava olhando (decisão do dono,
    07/09/2026)."""
    pfu, _ = rota
    fu.config(c, CONTA)
    c.commit()
    assert _modo_no_banco(c) == "off"
    r = _post_modo(rota, "ligado")
    assert r.status_code == 303 and r.headers["location"] == "/painel/follow-up"
    assert _modo_no_banco(c) == "ligado"
    _post_modo(rota, "observando")
    assert _modo_no_banco(c) == "observando"


def test_o_vendedor_nao_liga_nem_desliga(rota, c):
    """Ligar muda o que a CONTA INTEIRA recebe — a mesma regra que a Régua já
    aplicava por `gerencia`."""
    fu.config(c, CONTA)
    c.execute("update funil_regua set follow_up_modo='ligado' where conta_id=%s", (CONTA,))
    c.commit()
    r = _post_modo(rota, "off", papel="vendedor")
    assert r.status_code == 303
    assert _modo_no_banco(c) == "ligado", "o vendedor desligou o follow-up da conta"


def test_valor_estranho_nao_vira_modo(rota, c):
    """Só os três de `funil_regua.MODOS`. Um valor torto virando coluna deixaria
    o motor lendo um estado que ele não sabe interpretar."""
    from finance import funil_regua as fr
    fu.config(c, CONTA)
    c.execute("update funil_regua set follow_up_modo='observando' where conta_id=%s", (CONTA,))
    c.commit()
    assert _post_modo(rota, "ligadão").status_code == 303
    assert _modo_no_banco(c) == "observando"
    assert fr.MODOS == ("off", "observando", "ligado")


def test_a_marcacao_nao_depende_do_relogio_do_banco(c):
    """A trava do defeito de 09/09/2026.

    `criado_em` da marcação NÃO é enfeite: `exige_motivo` e a coluna `na_mao`
    perguntam "esta marcação é mais nova que a última mensagem do cliente?". Se
    quem carimba é o `now()` do Postgres e quem pergunta usa um relógio injetado,
    a resposta passa a depender de qual dos dois está na frente.

    Em produção os dois coincidem e o defeito fica invisível. Foi assim que dois
    testes verdes por semanas quebraram sozinhos na main, sem ninguém tocar em
    follow-up: `AGORA` era uma data fixa que até a véspera era futuro, e no dia em
    que o relógio real passou dela a comparação inverteu.

    Este teste marca no PASSADO e conversa DEPOIS. Com o carimbo do banco isso
    seria impossível de escrever — a marcação nasceria sempre "agora"."""
    v = _vend(c)
    lead = _lead(c, v, contato="Ana"); conv = _fala(c, lead, ("in", 30), ("out", 29))
    # marcado há 10 dias, muito antes de qualquer relógio de parede desta máquina
    fu.marcar(c, CONTA, lead, AGORA + timedelta(days=2), membro_id=v,
              agora=AGORA - timedelta(days=10))
    assert fu.leads(c, CONTA, EVENTOS, AGORA)[0]["na_mao"], (
        "marcação sem conversa depois dela: a bola é do vendedor")
    # o cliente escreve DEPOIS da marcação — mas ainda no passado
    _msg(c, conv, "in", AGORA - timedelta(days=9))
    assert not fu.leads(c, CONTA, EVENTOS, AGORA)[0]["na_mao"], (
        "com o carimbo vindo do banco, a marcação nasceria HOJE e esta mensagem de "
        "9 dias atrás pareceria velha — a bola nunca voltaria pra empresa")


def test_sem_relogio_a_marcacao_segue_carimbando_pelo_banco(c):
    """O parâmetro é opcional de propósito: a tela chama sem ele (o instante da
    marcação É agora, e o banco é a fonte certa). Só quem injeta relógio precisa
    dizer quando."""
    v = _vend(c)
    lead = _lead(c, v, contato="Ana"); _fala(c, lead, ("in", 9), ("out", 8))
    assert fu.marcar(c, CONTA, lead, AGORA + timedelta(days=1), membro_id=v)["ok"]
    r = c.execute("""select criado_em from follow_up_marcacoes
                      where prospeccao_id=%s order by id desc limit 1""", (lead,)).fetchone()
    assert r[0] is not None, "sem `agora`, quem carimba continua sendo o now() do banco"


# ------------------------------------------------- as tentativas como tarefas
# Regra 4 do documento: "as três tentativas devem nascer automaticamente como
# tarefas em D1, D3 e D7, com prazo e alerta de atraso, sem depender da lembrança
# do vendedor". Migração 233: é a ETAPA que declara — sem isso, a escada relativa
# à conversa continua valendo, que é o comportamento de toda conta hoje.

def test_a_etapa_sem_toques_declarados_usa_a_escada_de_sempre():
    p, a = fu.prazo_automatico(status="contatado", ult_in=None, ult_out=AGORA - timedelta(days=1),
                               criado_em=None, tentativas=1, evento_em=None, cfg=_cfg(),
                               tem_data=True, agora=AGORA)
    assert p == AGORA - timedelta(days=1) + timedelta(days=2) and "toque" in a


def test_as_tentativas_da_etapa_contam_da_ENTRADA_e_nao_da_ultima_conversa():
    """A diferença que motivou a migração 233. Com a escada relativa, o vendedor que
    tenta no D2 empurra a próxima pra D2+3, e o "período total de 7 dias" vira sete
    dias depois do último esforço — que pode ser um mês depois da entrada."""
    entrou = AGORA - timedelta(days=2)
    p, a = fu.prazo_automatico(status="follow_up", ult_in=None, ult_out=AGORA - timedelta(hours=3),
                               criado_em=None, tentativas=1, evento_em=None, cfg=_cfg(),
                               tem_data=True, agora=AGORA,
                               toques_fixos=(1, 3, 7), desde=entrou, feitos_na_etapa=1)
    assert p == entrou + timedelta(days=3), "a 2ª tentativa é D3 da ENTRADA"
    assert a == "2ª tentativa de 3"


def test_feitas_as_tres_o_lead_espera_DECISAO_e_nao_vira_perdido():
    """Perder exige motivo (regra 5), e só quem falou com o cliente sabe qual — a
    mesma trava do teto da etapa, que também não move ninguém sozinho."""
    entrou = AGORA - timedelta(days=8)
    p, a = fu.prazo_automatico(status="follow_up", ult_in=None, ult_out=AGORA - timedelta(days=1),
                               criado_em=None, tentativas=3, evento_em=None, cfg=_cfg(),
                               tem_data=True, agora=AGORA,
                               toques_fixos=(1, 3, 7), desde=entrou, feitos_na_etapa=3)
    assert p == entrou + timedelta(days=7) and "encerrar ou reativar" in a


def test_a_bola_do_cliente_continua_vindo_antes_das_tentativas():
    """Cliente esperando resposta é mais urgente que a próxima tentativa agendada,
    e essa ordem não muda por etapa nenhuma."""
    ult_in = AGORA - timedelta(hours=10)
    p, a = fu.prazo_automatico(status="follow_up", ult_in=ult_in, ult_out=AGORA - timedelta(days=3),
                               criado_em=None, tentativas=1, evento_em=None, cfg=_cfg(),
                               tem_data=True, agora=AGORA,
                               toques_fixos=(1, 3, 7), desde=AGORA - timedelta(days=4),
                               feitos_na_etapa=1)
    assert p == ult_in + timedelta(minutes=240) and "esperando" in a


def test_as_tarefas_nascem_com_data_e_marcam_feito_e_atrasado():
    entrou = AGORA - timedelta(days=4)
    t = fu.toques_da_etapa(desde=entrou, toques=(1, 3, 7), feitos=1, agora=AGORA)
    assert [x["dia"] for x in t] == [1, 3, 7]
    assert [x["feito"] for x in t] == [True, False, False]
    # D1 feita; D3 já venceu e não foi feita; D7 ainda não venceu
    assert [x["atrasado"] for x in t] == [False, True, False]
    assert t[2]["prazo"] == entrou + timedelta(days=7)


def test_a_lista_de_tarefas_chega_na_linha_do_lead(c):
    """De ponta a ponta: a etapa declara, o motor calcula, a linha leva pra tela."""
    c.execute("update funil_etapas set toques_dias='1,3,7' where conta_id=%s and chave='contatado'",
              (CONTA,))
    v = _vend(c)
    lid = _lead(c, v, criado=AGORA - timedelta(days=4))
    c.execute("""insert into funil_movimentos (conta_id, prospeccao_id, de, para, motivo, criado_em)
                 values (%s,%s,'novo','contatado','manual',%s)""",
              (CONTA, lid, AGORA - timedelta(days=4)))
    _fala(c, lid, ("out", 3))       # uma tentativa saiu 1 dia depois de entrar na etapa
    linha = [x for x in fu.leads(c, CONTA, EVENTOS, AGORA) if x["id"] == lid][0]
    assert [t["dia"] for t in linha["toques"]] == [1, 3, 7]
    assert linha["toques"][0]["feito"] is True and linha["toques"][1]["feito"] is False
    c.execute("update funil_etapas set toques_dias=null where conta_id=%s and chave='contatado'",
              (CONTA,))
