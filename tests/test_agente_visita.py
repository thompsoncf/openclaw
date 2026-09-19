"""A IA combinando a visita ao espaço (finance/agente_visita, migração 259).

O que estes testes protegem, em uma linha cada:

* **A chave nasce desligada.** Já existe um `pode_agendar` `true` em produção que
  nunca fez nada; se o modo herdasse dele, a função ligaria sozinha em quem nunca
  pediu — exatamente o que a §0 do CLAUDE.md proíbe.
* **A janela é do dono.** Ele decidiu em 14/09/2026, contra a minha recomendação:
  "tem que ser horário comercial e fora esse horário falar com vendedor dono do
  lead ou gestor". Fora da janela a IA não marca — e AVISA, que é a metade que
  impede a regra de virar silêncio no domingo.
* **Não se oferece horário ocupado.** No nicho eventos, marcar em cima de outro
  compromisso é vender a mesma data duas vezes.
* **Uma proposta viva por lead.** Senão o vendedor recebe três cartões do mesmo
  cliente e para de olhar os cartões.
"""
import os
from datetime import datetime, timedelta

import pytest
from psycopg_pool import ConnectionPool

from finance import agenda as ag
from finance import agente_visita as av

CONTA = 77
BRT = ag.BRT

# segunda-feira, 14/09/2026, 10h — dentro da janela
SEG_10 = datetime(2026, 9, 14, 10, 0, tzinfo=BRT)

_SQL = """
create table contas (id bigserial primary key, nome text);
create table prospeccao (id bigserial primary key, conta_id bigint, vendedor_id bigint,
  empresa text, contato text, status text default 'novo',
  evento_tipo text, evento_convidados int,
  atualizado_em timestamptz default now(), criado_em timestamptz default now());
create table membros (id bigserial primary key, conta_id bigint, nome text, email text,
  papel text, ativo boolean default true);
create table prospeccao_atividades (id bigserial primary key, prospeccao_id bigint,
  membro_id bigint, tipo text not null, resultado text, descricao text not null,
  agendado_para timestamptz, criado_em timestamptz not null default now());
create table eventos_agenda (id bigserial primary key, conta_id bigint, membro_id bigint,
  titulo text, inicio timestamptz, fim timestamptz, local text, descricao text,
  status text default 'ativo', tipo text default 'pessoal', lembrete_min int,
  link_online text, prospeccao_id bigint, cliente_id bigint, ics_token text,
  pre_reserva_ate timestamptz, sinal_centavos bigint, tipo_evento text, convidados int,
  hora_sugerida boolean default false, ocupa_espaco boolean, desfecho text, marcado_por text,
  criado_em timestamptz default now());
create table agente_visitas (
  id bigserial primary key, conta_id bigint not null, prospeccao_id bigint not null,
  conversa_id bigint, inicio timestamptz not null, dur_min int not null default 60,
  estado text not null default 'proposta', membro_id bigint, evento_id bigint,
  criado_em timestamptz not null default now(), decidido_em timestamptz, decidido_por bigint);
create unique index agente_visitas_uma_viva on agente_visitas (conta_id, prospeccao_id)
  where estado = 'proposta';
"""


@pytest.fixture()
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_agente_visita"
    with admin.connection() as c:
        c.autocommit = True
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=3, open=True, kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_SQL)
        c.execute("insert into contas (id, nome) values (%s,'Prime')", (CONTA,))
        c.commit()
    yield p
    p.close()


def _lead(pool, vendedor_id=None, contato="Camila"):
    with pool.connection() as c:
        r = c.execute("""insert into prospeccao (conta_id, vendedor_id, empresa, contato)
                         values (%s,%s,'Festa da Camila',%s) returning id""",
                      (CONTA, vendedor_id, contato)).fetchone()
        c.commit()
    return r[0]


def _membro(pool, papel="vendedor", nome="Pedro"):
    with pool.connection() as c:
        r = c.execute("""insert into membros (conta_id, nome, email, papel)
                         values (%s,%s,%s,%s) returning id""",
                      (CONTA, nome, f"{nome.lower()}@x.com", papel)).fetchone()
        c.commit()
    return r[0]


def _ocupa(pool, inicio, dur_min=60):
    with pool.connection() as c:
        c.execute("""insert into eventos_agenda (conta_id, titulo, inicio, fim, status)
                     values (%s,'Festa',%s,%s,'ativo')""",
                  (CONTA, inicio, inicio + timedelta(minutes=dur_min)))
        c.commit()


# ------------------------------------------------------------------ a chave

def test_a_chave_nasce_desligada_e_valor_torto_tambem():
    """Config sem o campo, config vazia e valor inventado: os três dão 'off'.

    Não é zelo — é o caso real. `pode_agendar` está `true` nas duas contas com
    agente e nunca fez nada; qualquer caminho que transformasse isso em 'propoe'
    ligaria a função em quem nunca pediu."""
    assert av.modo(None) == "off"
    assert av.modo({}) == "off"
    assert av.modo({"agendar_modo": "ligado"}) == "off"
    assert av.modo({"agendar_modo": ""}) == "off"
    assert av.modo({"pode_agendar": True}) == "off"


@pytest.mark.parametrize("m", ["off", "propoe", "marca"])
def test_os_tres_modos_valem(m):
    assert av.modo({"agendar_modo": m}) == m
    assert av.MODOS == ("off", "propoe", "marca")


# ------------------------------------------------------------------ a janela

@pytest.mark.parametrize("quando, dentro", [
    (SEG_10, True),                                             # segunda 10h
    (datetime(2026, 9, 19, 10, 0, tzinfo=BRT), True),           # sábado 10h
    (datetime(2026, 9, 19, 17, 0, tzinfo=BRT), True),           # sábado 17h
    (datetime(2026, 9, 20, 10, 0, tzinfo=BRT), False),          # domingo
    (datetime(2026, 9, 14, 7, 59, tzinfo=BRT), False),          # antes de abrir
    (datetime(2026, 9, 14, 18, 0, tzinfo=BRT), False),          # 18h já fechou
    (datetime(2026, 9, 19, 19, 0, tzinfo=BRT), False),          # sábado 19h
])
def test_a_janela_comercial(quando, dentro):
    """A decisão do dono, em tabela. Sábado 19h é FORA — e é justamente quando
    o cliente da Prime pede: por isso a outra metade (avisar gente) existe."""
    assert av.na_janela(quando) is dentro


def test_desligado_nao_age_nem_dentro_da_janela():
    assert av.pode_agora({"agendar_modo": "off"}, SEG_10) is False
    assert av.pode_agora({"agendar_modo": "propoe"}, SEG_10) is True
    assert av.pode_agora({"agendar_modo": "marca"}, SEG_10) is True


def test_ligado_nao_age_fora_da_janela():
    domingo = datetime(2026, 9, 20, 10, 0, tzinfo=BRT)
    assert av.pode_agora({"agendar_modo": "marca"}, domingo) is False


# ------------------------------------------------- o cliente pedindo pra visitar

@pytest.mark.parametrize("texto", [
    "queria conhecer o espaço", "dá pra fazer uma visita?", "posso visitar amanhã?",
    "gostaria de conhecer o local", "quero ver o espaço antes de fechar",
])
def test_reconhece_o_pedido_de_visita(texto):
    """A MESMA régua com que o problema foi medido (46 leads em 510 conversas na
    Prime). O que a medição contou é o que o código reconhece."""
    assert av.pediu_visita(texto) is True


@pytest.mark.parametrize("texto", ["qual o valor do pacote?", "vocês têm data em dezembro?",
                                   "", None])
def test_o_que_nao_e_pedido_de_visita(texto):
    assert av.pediu_visita(texto) is False


# ------------------------------------------------------------------ a agenda

def test_horario_ocupado_nao_esta_livre(pool):
    _ocupa(pool, SEG_10)
    assert av.livre(pool, CONTA, SEG_10) is False
    assert av.livre(pool, CONTA, SEG_10 + timedelta(hours=2)) is True


def test_sobreposicao_parcial_tambem_ocupa(pool):
    """Começar meia hora depois do outro começar é o choque mais provável, não o
    menos — a mesma convenção que `agenda.conflitos` já usa."""
    _ocupa(pool, SEG_10, dur_min=120)
    assert av.livre(pool, CONTA, SEG_10 + timedelta(minutes=30)) is False


def test_banco_ruim_responde_ocupado(pool):
    """Na dúvida, NÃO oferece. Oferecer horário sem saber se está livre é como se
    vende a mesma data duas vezes."""
    class PoolRuim:
        def connection(self):
            raise RuntimeError("banco fora")
    assert av.livre(PoolRuim(), CONTA, SEG_10) is False


def test_sugestoes_so_saem_dentro_da_janela_e_livres(pool):
    """Sexta 17h: as sugestões não podem cair no domingo nem antes das 8h."""
    sexta_17 = datetime(2026, 9, 18, 17, 0, tzinfo=BRT)
    livres = av.sugestoes(pool, CONTA, sexta_17, quantas=3)
    assert livres and all(av.na_janela(x) for x in livres)
    assert all(x > sexta_17 for x in livres)


def test_sugestoes_pulam_o_que_esta_ocupado(pool):
    manha = datetime(2026, 9, 14, 9, 0, tzinfo=BRT)
    for h in (10, 11, 12):
        _ocupa(pool, manha.replace(hour=h))
    livres = av.sugestoes(pool, CONTA, manha, quantas=2)
    ocupadas = {manha.replace(hour=h) for h in (10, 11, 12)}
    assert livres and not (set(livres) & ocupadas)


# ------------------------------------------------------------- a quem se avisa

def test_avisa_o_vendedor_dono_do_lead(pool):
    v = _membro(pool)
    lead = _lead(pool, vendedor_id=v)
    with pool.connection() as c:
        assert av.quem_avisar(c, CONTA, lead) == [v]


def test_lead_sem_vendedor_escala_pra_gestao(pool):
    """Na Prime, 0 dos 341 leads abertos está sem vendedor — esta porta é pro caso
    raro, e existe pra que o pedido do cliente nunca caia no vazio."""
    dono = _membro(pool, papel="dono", nome="Thompson")
    gestor = _membro(pool, papel="gestor", nome="Ana")
    _membro(pool, papel="vendedor", nome="Outro")     # não é do lead: fica de fora
    lead = _lead(pool, vendedor_id=None)
    with pool.connection() as c:
        assert sorted(av.quem_avisar(c, CONTA, lead)) == sorted([dono, gestor])


# ------------------------------------------------------------- fora de hora

def test_fora_de_hora_avisa_uma_vez_so(pool, monkeypatch):
    """Um cliente que escreve três mensagens às 22h não pode virar três pushes
    sobre a mesma coisa — notificação repetida é a que se aprende a ignorar."""
    enviados = []
    monkeypatch.setattr(av, "avisar", lambda *a, **k: enviados.append(a))
    lead = _lead(pool, vendedor_id=_membro(pool))
    assert av.fora_de_hora(pool, CONTA, lead, "Camila") is True
    assert av.fora_de_hora(pool, CONTA, lead, "Camila") is False
    assert len(enviados) == 1
    with pool.connection() as c:
        n = c.execute("""select count(*) from prospeccao_atividades
                          where prospeccao_id=%s and resultado='fora_de_hora'""",
                      (lead,)).fetchone()[0]
    assert n == 1


# ------------------------------------------------------------- a proposta

def test_propor_guarda_e_nao_toca_na_agenda(pool, monkeypatch):
    """O ponto inteiro do modo 'propoe': nada entra na agenda sem uma pessoa."""
    monkeypatch.setattr(av, "avisar", lambda *a, **k: None)
    v = _membro(pool)
    lead = _lead(pool, vendedor_id=v)
    r = av.propor(pool, CONTA, lead, SEG_10, quem="Camila")
    assert r["ok"] and r["quando"] == "14/09 às 10:00"
    with pool.connection() as c:
        est, membro = c.execute("""select estado, membro_id from agente_visitas
                                    where prospeccao_id=%s""", (lead,)).fetchone()
        eventos = c.execute("select count(*) from eventos_agenda where conta_id=%s",
                            (CONTA,)).fetchone()[0]
    assert est == "proposta" and membro == v
    assert eventos == 0, "a proposta criou compromisso na agenda"


def test_propor_de_novo_atualiza_em_vez_de_duplicar(pool, monkeypatch):
    monkeypatch.setattr(av, "avisar", lambda *a, **k: None)
    lead = _lead(pool, vendedor_id=_membro(pool))
    av.propor(pool, CONTA, lead, SEG_10, quem="Camila")
    av.propor(pool, CONTA, lead, SEG_10 + timedelta(hours=3), quem="Camila")
    with pool.connection() as c:
        linhas = c.execute("""select inicio from agente_visitas
                               where prospeccao_id=%s and estado='proposta'""",
                           (lead,)).fetchall()
    assert len(linhas) == 1, "o vendedor recebeu dois cartões do mesmo cliente"
    assert linhas[0][0].astimezone(BRT).hour == 13


def test_pendentes_so_traz_as_do_vendedor(pool, monkeypatch):
    monkeypatch.setattr(av, "avisar", lambda *a, **k: None)
    v1, v2 = _membro(pool, nome="Pedro"), _membro(pool, nome="Bia")
    l1, l2 = _lead(pool, vendedor_id=v1), _lead(pool, vendedor_id=v2, contato="Ana")
    av.propor(pool, CONTA, l1, SEG_10)
    av.propor(pool, CONTA, l2, SEG_10 + timedelta(hours=2))
    assert [x["lead_id"] for x in av.pendentes(pool, CONTA, v1)] == [l1]
    assert len(av.pendentes(pool, CONTA)) == 2


def test_proposta_velha_some_da_fila(pool, monkeypatch):
    """Combinação de dois dias atrás que ninguém decidiu não é mais combinação."""
    monkeypatch.setattr(av, "avisar", lambda *a, **k: None)
    lead = _lead(pool, vendedor_id=_membro(pool))
    av.propor(pool, CONTA, lead, SEG_10)
    with pool.connection() as c:
        c.execute("update agente_visitas set criado_em = now() - interval '72 hours'")
        c.commit()
    assert av.pendentes(pool, CONTA) == []


def test_descartar_tira_da_fila(pool, monkeypatch):
    monkeypatch.setattr(av, "avisar", lambda *a, **k: None)
    v = _membro(pool)
    lead = _lead(pool, vendedor_id=v)
    av.propor(pool, CONTA, lead, SEG_10)
    pid = av.pendentes(pool, CONTA)[0]["id"]
    assert av.descartar(pool, CONTA, v, pid)["ok"] is True
    assert av.pendentes(pool, CONTA) == []
    # descartar de novo não "dá certo" duas vezes
    assert av.descartar(pool, CONTA, v, pid)["ok"] is False


# ------------------------------------------------- confirmar e marcar

def _finge_agendar(monkeypatch, pool, chamadas):
    """Troca o motor do Cockpit por um espião que grava o evento na agenda.

    O que se testa aqui é o CONTRATO — que confirmar passa pelo mesmo caminho do
    botão manual e anota quem marcou. O motor em si tem os testes dele; recriar
    meia dúzia de tabelas (clientes, contas com endereço, ics) só pra reexercitá-lo
    ensinaria o teste a quebrar por motivo alheio."""
    from finance import cockpit as ck

    def falso(p, conta_id, membro_id, lead_id, **kw):
        chamadas.append(dict(membro_id=membro_id, lead_id=lead_id, **kw))
        with pool.connection() as c:
            r = c.execute("""insert into eventos_agenda (conta_id, titulo, inicio, prospeccao_id)
                             values (%s,'Visita — Camila', now(), %s) returning id""",
                          (conta_id, lead_id)).fetchone()
            c.commit()
        return {"ok": True, "evento_id": r[0], "quando": "14/09 às 10:00"}
    monkeypatch.setattr(ck, "agendar_visita", falso)


def test_confirmar_vira_compromisso_pelo_caminho_de_sempre(pool, monkeypatch):
    """A proposta não é um segundo jeito de marcar visita: é o vendedor apertando
    o botão que já existia, sem preencher nada."""
    monkeypatch.setattr(av, "avisar", lambda *a, **k: None)
    chamadas = []
    _finge_agendar(monkeypatch, pool, chamadas)
    v = _membro(pool)
    lead = _lead(pool, vendedor_id=v)
    av.propor(pool, CONTA, lead, SEG_10)
    pid = av.pendentes(pool, CONTA)[0]["id"]

    out = av.confirmar(pool, CONTA, v, pid)
    assert out["ok"] and chamadas and chamadas[0]["membro_id"] == v
    assert chamadas[0]["data"] == "2026-09-14" and chamadas[0]["hora"] == "10:00"
    with pool.connection() as c:
        est, ev = c.execute("select estado, evento_id from agente_visitas where id=%s",
                            (pid,)).fetchone()
        marcado = c.execute("select marcado_por from eventos_agenda where id=%s", (ev,)).fetchone()[0]
    assert est == "confirmada" and ev
    assert marcado == "vendedor", "quem apertou o botão foi gente, e o placar tem que dizer isso"
    assert av.pendentes(pool, CONTA) == []


def test_confirmar_proposta_que_ja_foi_decidida_nao_marca_de_novo(pool, monkeypatch):
    """Dois toques no mesmo cartão não podem virar dois compromissos pro mesmo
    cliente — o botão já se desabilita na tela, e aqui é a trava de verdade."""
    monkeypatch.setattr(av, "avisar", lambda *a, **k: None)
    chamadas = []
    _finge_agendar(monkeypatch, pool, chamadas)
    v = _membro(pool)
    lead = _lead(pool, vendedor_id=v)
    av.propor(pool, CONTA, lead, SEG_10)
    pid = av.pendentes(pool, CONTA)[0]["id"]
    av.confirmar(pool, CONTA, v, pid)
    out = av.confirmar(pool, CONTA, v, pid)
    assert out["ok"] is False and len(chamadas) == 1


def test_marcar_anota_que_foi_a_ia(pool, monkeypatch):
    """O placar do modo 'marca': sem isto, virar a chave seria voto de fé."""
    monkeypatch.setattr(av, "avisar", lambda *a, **k: None)
    chamadas = []
    _finge_agendar(monkeypatch, pool, chamadas)
    v = _membro(pool)
    lead = _lead(pool, vendedor_id=v)
    out = av.marcar(pool, CONTA, lead, SEG_10, quem="Camila")
    assert out["ok"] and chamadas[0]["membro_id"] == v
    with pool.connection() as c:
        marcado = c.execute("select marcado_por from eventos_agenda where id=%s",
                            (out["evento_id"],)).fetchone()[0]
    assert marcado == "ia"


def test_marcar_sem_vendedor_vira_proposta_pra_gestao(pool, monkeypatch):
    """`agendar_visita` revalida a posse: sem dono não há posse a revalidar, e
    inventar um poria a visita na agenda de quem não vai receber o cliente."""
    monkeypatch.setattr(av, "avisar", lambda *a, **k: None)
    chamadas = []
    _finge_agendar(monkeypatch, pool, chamadas)
    _membro(pool, papel="dono", nome="Thompson")
    lead = _lead(pool, vendedor_id=None)
    out = av.marcar(pool, CONTA, lead, SEG_10, quem="Camila")
    assert out["ok"] and not chamadas, "marcou visita sem dono do lead"
    assert len(av.pendentes(pool, CONTA)) == 1


# ------------------------------- a trava: só o horário que a própria IA ofereceu

def test_so_vale_o_horario_que_foi_oferecido():
    """Separa "a IA agendou" de "a IA inventou". A lista saiu da agenda segundos
    antes, já sem conflito — aceitar fora dela é marcar em cima de festa."""
    livres = [SEG_10, SEG_10 + timedelta(hours=3)]
    assert av.foi_oferecido(SEG_10, livres) is True
    assert av.foi_oferecido(SEG_10 + timedelta(hours=3), livres) is True
    # meia hora depois do que se ofereceu já é outro horário
    assert av.foi_oferecido(SEG_10 + timedelta(minutes=30), livres) is False
    # o erro clássico de modelo de linguagem: mês trocado
    assert av.foi_oferecido(SEG_10.replace(month=10), livres) is False


def test_sem_horario_ou_sem_oferta_nao_marca_nada():
    """A IA que devolve acao=visita sem data, ou quando não havia oferta nenhuma,
    não pode virar compromisso — responde e a conversa segue."""
    assert av.foi_oferecido(None, [SEG_10]) is False
    assert av.foi_oferecido(SEG_10, []) is False


def test_segundos_nao_derrubam_o_casamento():
    """O que volta é texto remontado, não o mesmo objeto."""
    assert av.foi_oferecido(SEG_10.replace(second=41), [SEG_10]) is True
