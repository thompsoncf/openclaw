"""A CARTEIRA DE APÓLICES e a régua de renovação (migração 278).

As quatro decisões do dono em 17/09/2026 — "60/30/15, percentual por seguradora,
alerta pro corretor, começa por auto" — são regras de negócio, não preferências de
tela. Cada uma tem teste aqui, e é isto que este arquivo existe pra impedir de
voltar:

* `test_a_carteira_recem_carregada_nao_fica_calada` — o caso REAL da Liberal, que
  vai digitar a carteira inteira de uma vez, com apólices já vencendo em 20 dias.
  Um `degrau_de` que devolvesse só o degrau mais próximo deixaria essa carga
  inteira em silêncio.
* `test_a_comissao_sai_do_LIQUIDO_e_nao_do_total_com_IOF` — o erro de conta do
  meu próprio mockup, que mostrava R$ 817,71 onde o certo é R$ 761,51. O IOF é
  imposto repassado ao governo; comissão sobre ele é comissão inventada.
* `test_lembretes_enviados_aceita_o_tipo_renovacao` — o incidente das migrações
  128 e 171, pela terceira vez: o CHECK que não conhece o tipo novo derruba o
  tick INTEIRO do ticker, não só o aviso da corretora.
"""
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from psycopg.errors import UniqueViolation
from psycopg_pool import ConnectionPool

from finance import apolices as ap

MIG = Path(__file__).resolve().parent.parent / "db" / "migracoes"
CONTA = 37          # a Liberal Seguros, que deu origem a tudo isto
OUTRA = 38

_BASE = """
create table contas (id bigint primary key, nome text);
create table membros (id bigserial primary key, conta_id bigint references contas(id),
  nome text, email text, papel text not null default 'membro', ativo boolean not null default true);
create table clientes (id bigserial primary key, dono_id bigint references contas(id),
  nome text not null, ativo boolean not null default true);
create table lembretes_enviados (id bigserial primary key,
  conta_id bigint not null references contas(id) on delete cascade,
  tipo text not null check (tipo in ('resumo','aviso')),
  chave text not null, enviado_em timestamptz not null default now(),
  unique (conta_id, tipo, chave));
insert into contas (id, nome) values (37,'Liberal Seguros'), (38,'Outra Corretora');
"""

HOJE = date(2026, 9, 17)


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_apolices_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=3, open=True, kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_BASE)
        c.execute((MIG / "278_apolices.sql").read_text(encoding="utf-8"))
        c.commit()
    yield p
    p.close()


@pytest.fixture
def limpo(pool):
    with pool.connection() as c:
        c.execute("delete from apolices")
        c.execute("delete from seguros_comissao")
        c.execute("delete from lembretes_enviados")
        c.execute("delete from clientes")
        c.execute("delete from membros")
        c.commit()
    return pool


def _apolice(pool, **kw):
    d = {"seguradora": "Allianz", "ramo": "auto", "situacao": "vigente",
         "vigencia_fim": HOJE + timedelta(days=45), "premio_centavos": 380757,
         "iof_centavos": 28100, "bem": {}, "condutor": {}, "coberturas": []}
    d.update(kw)
    return ap.salvar(pool, d.pop("conta_id", CONTA), d)


# ------------------------------------------------------------------ a régua

def test_a_regua_tem_tres_degraus_e_nao_cinco():
    """"60/30/15" — decisão do dono. A escada de mercado é 90/60/45/30/15, e cinco
    avisos sobre a mesma apólice viram ruído até o corretor ignorar todos."""
    assert ap.DEGRAUS == (60, 30, 15)


def test_os_90_dias_sao_LISTA_e_nao_alerta():
    """O horizonte da tela é maior que o primeiro degrau de propósito: entre 90 e
    60 dias a apólice aparece e não incomoda ninguém."""
    assert ap.HORIZONTE == 90
    assert ap.HORIZONTE > ap.DEGRAUS[0]
    assert ap.degrau_de(75) is None


@pytest.mark.parametrize("dias, esperado", [
    (91, None), (61, None), (60, 60), (59, 60), (31, 60),
    (30, 30), (16, 30), (15, 15), (1, 15), (0, 15),
])
def test_cada_faixa_cai_no_seu_degrau(dias, esperado):
    assert ap.degrau_de(dias) == esperado


def test_a_carteira_recem_carregada_nao_fica_calada():
    """O caso REAL da Liberal: ela vai digitar a carteira inteira de uma vez, e
    parte dela já vence em menos de 60 dias.

    `degrau_de` devolve o degrau mais APERTADO já alcançado: faltando 20 dias ela
    cai no 30 e avisa hoje. Devolvendo o MAIOR ("ela passou do 60") a régua
    quebraria em dois lugares de uma vez — a carga nova só avisaria daqui a cinco
    dias, e, pior, o dedup por (apólice, degrau) travaria em 60 pra sempre e os
    avisos de 30 e de 15 nunca sairiam. Três toques virariam um.
    """
    assert ap.degrau_de(20) == 30
    assert ap.degrau_de(50) == 60


def test_quem_ja_venceu_nao_entra_na_regua():
    """Não é aviso de renovação — é outra conversa, e ela não se resolve com push."""
    assert ap.degrau_de(-1) is None
    assert ap.degrau_de(-400) is None
    assert ap.degrau_de(None) is None


# --------------------------------------------------------------- a comissão

def test_a_comissao_sai_do_LIQUIDO_e_nao_do_total_com_IOF():
    """O papel da Allianz: líquido R$ 3.807,57 + IOF R$ 281,00 = R$ 4.088,57.

    A comissão do corretor incide sobre o LÍQUIDO. O mockup somou os dois e
    anunciou R$ 817,71; o certo, com o mesmo papel, é R$ 761,51. São 56 reais de
    diferença numa apólice só — numa carteira inteira é a comissão parecendo maior
    do que é, todo mês, no número em que o Raio-X da corretora se apoia.
    """
    assert ap.comissao_de(380757, 20) == 76151
    assert ap.comissao_de(380757 + 28100, 20) == 81771   # a conta ERRADA, pra fixar a diferença


def test_sem_percentual_cadastrado_a_comissao_e_None_e_nao_zero():
    """Zero diria "esta apólice não paga comissão", que é outra afirmação — e é a
    que faria a tela mentir com cara de dado."""
    assert ap.comissao_de(380757, None) is None
    assert ap.comissao_de(0, None) is None


def test_o_valor_digitado_ganha_do_percentual():
    """Quando a corretora recebe o extrato da seguradora e digita o número exato,
    esse número é FATO. Um percentual estimado não sobrescreve um fato."""
    assert ap.comissao_de(380757, 20, comissao_centavos=70000) == 70000
    # inclusive quando o fato é zero: comissão zerada existe (apólice cortesia,
    # repasse, acerto) e não pode virar 20% de novo na próxima leitura.
    assert ap.comissao_de(380757, 20, comissao_centavos=0) == 0


def test_o_ramo_exato_ganha_do_curinga(limpo):
    """"Allianz 20%" vale pra tudo; "Allianz vida 12%" manda em vida. É assim que a
    corretora cadastra uma linha por seguradora e só detalha onde foge."""
    ap.salvar_comissao(limpo, CONTA, "Allianz", "", 20)
    ap.salvar_comissao(limpo, CONTA, "Allianz", "vida", "12,5")
    with limpo.connection() as c:
        assert float(ap.pct_padrao(c, CONTA, "Allianz", "auto")) == 20.0
        assert float(ap.pct_padrao(c, CONTA, "Allianz", "vida")) == 12.5
        assert float(ap.pct_padrao(c, CONTA, "allianz", "auto")) == 20.0   # não é sensível a caixa
        assert ap.pct_padrao(c, CONTA, "Porto", "auto") is None


def test_o_percentual_de_uma_corretora_nao_vaza_pra_outra(limpo):
    ap.salvar_comissao(limpo, CONTA, "Allianz", "", 20)
    with limpo.connection() as c:
        assert ap.pct_padrao(c, OUTRA, "Allianz", "auto") is None


def test_percentual_fora_da_faixa_nao_grava(limpo):
    assert ap.salvar_comissao(limpo, CONTA, "Allianz", "", 101) is False
    assert ap.salvar_comissao(limpo, CONTA, "Allianz", "", -1) is False
    assert ap.salvar_comissao(limpo, CONTA, "", "", 20) is False
    assert ap.salvar_comissao(limpo, CONTA, "Allianz", "nao_existe", 20) is False
    assert ap.comissoes(limpo, CONTA) == []


def test_salvar_o_mesmo_par_atualiza_em_vez_de_duplicar(limpo):
    ap.salvar_comissao(limpo, CONTA, "Allianz", "auto", 20)
    ap.salvar_comissao(limpo, CONTA, "ALLIANZ", "auto", 22)
    linhas = ap.comissoes(limpo, CONTA)
    assert len(linhas) == 1 and float(linhas[0]["pct"]) == 22.0


# ---------------------------------------------------------------- a apólice

def test_apolice_sem_fim_de_vigencia_nao_grava(limpo):
    """Ela ficaria pra sempre na carteira parecendo coberta e nunca avisaria."""
    with pytest.raises(ValueError):
        ap.salvar(limpo, CONTA, {"seguradora": "Allianz", "vigencia_fim": None})
    with pytest.raises(ValueError):
        ap.salvar(limpo, CONTA, {"seguradora": "", "vigencia_fim": HOJE})
    with pytest.raises(ValueError):
        ap.salvar(limpo, CONTA, {"seguradora": "A", "vigencia_fim": HOJE, "ramo": "foguete"})


def test_o_bem_de_auto_vai_e_volta_inteiro(limpo):
    """O `bem` é jsonb porque auto tem placa e chassi, residencial tem metragem e
    vida tem beneficiário — e uma coluna por campo de cada ramo seria uma tabela
    com sessenta colunas nulas em toda linha."""
    i = _apolice(limpo, bem={"placa": "ABC1D23", "modelo": "GEELY EX2 MAX", "ano": "2026"})
    a = ap.uma(limpo, CONTA, i, hoje=HOJE)
    assert a["bem"]["placa"] == "ABC1D23"
    assert a["bem"]["modelo"] == "GEELY EX2 MAX"


def test_a_mesma_apolice_nao_entra_duas_vezes(limpo):
    """Cadastro duplicado conta o dobro de prêmio E dispara o alerta duplicado."""
    _apolice(limpo, numero_apolice="139041981")
    with pytest.raises(UniqueViolation):
        _apolice(limpo, numero_apolice="139041981")


def test_duas_PROPOSTAS_sem_numero_convivem(limpo):
    """Na fase de proposta o número da apólice ainda não foi emitido — e a trava de
    duplicidade não pode impedir a corretora de cadastrar duas propostas."""
    _apolice(limpo, situacao="proposta", numero_proposta="1")
    _apolice(limpo, situacao="proposta", numero_proposta="2")
    assert len(ap.listar(limpo, CONTA, hoje=HOJE)) == 2


def test_a_comissao_da_apolice_sobrescreve_o_padrao_da_seguradora(limpo):
    ap.salvar_comissao(limpo, CONTA, "Allianz", "", 20)
    padrao = _apolice(limpo)
    especial = _apolice(limpo, comissao_pct="25", numero_apolice="X")
    por_id = {a["id"]: a for a in ap.listar(limpo, CONTA, hoje=HOJE)}
    assert por_id[padrao]["comissao_estimada"] == 76151      # 20% do líquido
    assert por_id[especial]["comissao_estimada"] == 95189    # 25% do líquido


# ----------------------------------------------------------------- a fila

def test_a_fila_vem_do_mais_urgente_pro_mais_folgado(limpo):
    _apolice(limpo, vigencia_fim=HOJE + timedelta(days=80), numero_apolice="C")
    _apolice(limpo, vigencia_fim=HOJE + timedelta(days=10), numero_apolice="A")
    _apolice(limpo, vigencia_fim=HOJE + timedelta(days=40), numero_apolice="B")
    fila = ap.a_vencer(limpo, CONTA, hoje=HOJE)
    assert [a["numero_apolice"] for a in fila] == ["A", "B", "C"]
    assert [a["degrau"] for a in fila] == [15, 60, None]


def test_o_que_vence_depois_do_horizonte_fica_de_fora(limpo):
    _apolice(limpo, vigencia_fim=HOJE + timedelta(days=200))
    assert ap.a_vencer(limpo, CONTA, hoje=HOJE) == []


def test_renovada_e_cancelada_saem_da_fila(limpo):
    """Elas já acabaram a vida útil. Continuar avisando sobre uma apólice renovada
    é o sistema pedindo pra ser ignorado."""
    _apolice(limpo, situacao="renovada", numero_apolice="R")
    _apolice(limpo, situacao="cancelada", numero_apolice="C")
    _apolice(limpo, situacao="vigente", numero_apolice="V")
    assert [a["numero_apolice"] for a in ap.a_vencer(limpo, CONTA, hoje=HOJE)] == ["V"]


def test_a_que_ja_venceu_e_ninguem_tocou_CONTINUA_aparecendo(limpo):
    """Escondê-la deixaria a corretora com um cliente descoberto e a tela dizendo
    que está tudo bem. Ela aparece, e com `degrau` None: aparece sem avisar."""
    _apolice(limpo, vigencia_fim=HOJE - timedelta(days=5))
    fila = ap.a_vencer(limpo, CONTA, hoje=HOJE)
    assert len(fila) == 1 and fila[0]["dias"] == -5 and fila[0]["degrau"] is None


def test_a_fila_do_corretor_e_so_dele(limpo):
    """Decisão do dono: o corretor vê a fila dele, o dono vê a carteira inteira."""
    with limpo.connection() as c:
        ana = c.execute("insert into membros (conta_id, nome, papel) "
                        "values (%s,'Ana','vendedor') returning id", (CONTA,)).fetchone()[0]
        bruno = c.execute("insert into membros (conta_id, nome, papel) "
                          "values (%s,'Bruno','vendedor') returning id", (CONTA,)).fetchone()[0]
        c.commit()
    _apolice(limpo, corretor_id=ana, numero_apolice="A")
    _apolice(limpo, corretor_id=bruno, numero_apolice="B")
    assert [a["numero_apolice"] for a in ap.a_vencer(limpo, CONTA, hoje=HOJE, corretor_id=ana)] == ["A"]
    assert len(ap.a_vencer(limpo, CONTA, hoje=HOJE)) == 2


def test_a_carteira_de_uma_corretora_nao_aparece_na_outra(limpo):
    _apolice(limpo, conta_id=CONTA)
    assert ap.a_vencer(limpo, OUTRA, hoje=HOJE) == []
    assert ap.listar(limpo, OUTRA, hoje=HOJE) == []


def test_o_nome_do_cliente_vem_junto(limpo):
    with limpo.connection() as c:
        cli = c.execute("insert into clientes (dono_id, nome) values (%s,'Maria Fernanda') "
                        "returning id", (CONTA,)).fetchone()[0]
        c.commit()
    _apolice(limpo, cliente_id=cli)
    assert ap.a_vencer(limpo, CONTA, hoje=HOJE)[0]["cliente"] == "Maria Fernanda"


def test_apolice_sem_cliente_na_carteira_ainda_assim_grava(limpo):
    """A corretora digita a apólice antes de o cliente existir na carteira mais
    vezes do que o contrário. Recusar seria o sistema escolhendo a ordem do
    trabalho dela."""
    _apolice(limpo, cliente_id=None)
    assert ap.a_vencer(limpo, CONTA, hoje=HOJE)[0]["cliente"] == "—"


# ------------------------------------------------- o vazio que informa (tela)

def test_a_proxima_e_a_que_vem_DEPOIS_do_horizonte(limpo):
    """O caso real da Liberal: uma apólice na carteira, vencendo em 23/07/2027 —
    309 dias a partir do HOJE deste arquivo (17/09). A fila fica vazia e a tela dizia "nenhuma apólice
    vencendo", o que é verdade e é inútil: convida a cadastrar de novo o que já
    está lá. `proxima` é o que transforma o vazio em informação."""
    _apolice(limpo, vigencia_fim=date(2027, 7, 23), numero_apolice="A")
    assert ap.a_vencer(limpo, CONTA, hoje=HOJE) == []
    p = ap.proxima(limpo, CONTA, hoje=HOJE)
    assert p is not None and p["dias"] == 309
    # e responde a pergunta seguinte sem obrigar ninguém a fazer a conta de cabeça
    assert p["entra_em"] == date(2027, 5, 24)      # 60 dias antes de vencer


def test_a_proxima_ignora_o_que_ja_esta_na_fila(limpo):
    """Ela é o que vem DEPOIS do horizonte. Devolver quem já está na lista faria a
    tela mostrar a mesma apólice duas vezes."""
    _apolice(limpo, vigencia_fim=HOJE + timedelta(days=30), numero_apolice="perto")
    assert ap.proxima(limpo, CONTA, hoje=HOJE) is None


def test_a_proxima_nao_traz_renovada_nem_vencida(limpo):
    _apolice(limpo, vigencia_fim=date(2027, 1, 1), situacao="renovada", numero_apolice="R")
    _apolice(limpo, vigencia_fim=date(2027, 6, 1), situacao="cancelada", numero_apolice="C")
    assert ap.proxima(limpo, CONTA, hoje=HOJE) is None


def test_a_proxima_respeita_o_corretor(limpo):
    with limpo.connection() as c:
        ana = c.execute("insert into membros (conta_id, nome, papel) "
                        "values (%s,'Ana','vendedor') returning id", (CONTA,)).fetchone()[0]
        c.commit()
    _apolice(limpo, corretor_id=ana, vigencia_fim=date(2027, 7, 23), numero_apolice="A")
    assert ap.proxima(limpo, CONTA, hoje=HOJE, corretor_id=ana) is not None
    assert ap.proxima(limpo, CONTA, hoje=HOJE, corretor_id=ana + 999) is None


# --------------------------------------------------------- a busca da carteira

def test_a_busca_acha_por_nome_seguradora_placa_e_numero(limpo):
    """Com trezentas apólices a tabela sem busca é ilegível — e trezentas é o
    tamanho de uma corretora pequena."""
    with limpo.connection() as c:
        cli = c.execute("insert into clientes (dono_id, nome) values (%s,'Maria Fernanda') "
                        "returning id", (CONTA,)).fetchone()[0]
        c.commit()
    _apolice(limpo, cliente_id=cli, numero_apolice="139041981",
             bem={"placa": "ABC1D23", "modelo": "GEELY EX2 MAX"})
    _apolice(limpo, seguradora="Porto", numero_apolice="999", bem={"placa": "XYZ9Z99"})
    def acha(termo):
        return [a["numero_apolice"] for a in ap.listar(limpo, CONTA, hoje=HOJE, busca=termo)]
    assert acha("maria") == ["139041981"]          # nome do cliente
    assert acha("porto") == ["999"]                # seguradora
    assert acha("ABC1D23") == ["139041981"]        # placa, que está no jsonb
    assert acha("geely") == ["139041981"]          # modelo, idem
    assert acha("13904") == ["139041981"]          # número
    assert acha("nao existe nada assim") == []


def test_a_busca_vazia_traz_tudo(limpo):
    _apolice(limpo, numero_apolice="A")
    _apolice(limpo, numero_apolice="B")
    assert len(ap.listar(limpo, CONTA, hoje=HOJE, busca="")) == 2
    assert len(ap.listar(limpo, CONTA, hoje=HOJE, busca=None)) == 2
    assert len(ap.listar(limpo, CONTA, hoje=HOJE)) == 2


def test_a_busca_nao_atravessa_a_conta(limpo):
    _apolice(limpo, conta_id=CONTA, seguradora="Allianz", numero_apolice="A")
    assert ap.listar(limpo, OUTRA, hoje=HOJE, busca="allianz") == []


def test_o_total_da_carteira_ignora_a_busca(limpo):
    """A aba mostra o número de tudo mesmo com filtro ligado: "Carteira (1)" com uma
    busca ativa faria a corretora achar que perdeu as outras 299."""
    _apolice(limpo, seguradora="Allianz", numero_apolice="A")
    _apolice(limpo, seguradora="Porto", numero_apolice="B")
    assert ap.total_da_carteira(limpo, CONTA) == 2
    assert len(ap.listar(limpo, CONTA, hoje=HOJE, busca="porto")) == 1
    assert ap.total_da_carteira(limpo, CONTA) == 2
    assert ap.total_da_carteira(limpo, OUTRA) == 0


# ------------------------------------------------------------ marcar vencidas

def test_marcar_vencidas_so_mexe_no_que_o_tempo_ja_mudou(limpo):
    viva = _apolice(limpo, vigencia_fim=HOJE + timedelta(days=10), numero_apolice="V")
    velha = _apolice(limpo, vigencia_fim=HOJE - timedelta(days=1), numero_apolice="X")
    renov = _apolice(limpo, vigencia_fim=HOJE - timedelta(days=90),
                     situacao="renovada", numero_apolice="R")
    assert ap.marcar_vencidas(limpo, HOJE) == 1
    por_id = {a["id"]: a for a in ap.listar(limpo, CONTA, hoje=HOJE)}
    assert por_id[velha]["situacao"] == "vencida"
    assert por_id[viva]["situacao"] == "vigente"
    assert por_id[renov]["situacao"] == "renovada"   # renovar não é vencer


def test_marcar_vencidas_e_idempotente(limpo):
    _apolice(limpo, vigencia_fim=HOJE - timedelta(days=1))
    assert ap.marcar_vencidas(limpo, HOJE) == 1
    assert ap.marcar_vencidas(limpo, HOJE) == 0


# ------------------------------------------------------------------ o alerta

def test_lembretes_enviados_aceita_o_tipo_renovacao(limpo):
    """PELA TERCEIRA VEZ (migrações 128 e 171): o CHECK que não conhece o tipo novo
    derruba o tick INTEIRO do ticker — `_primeira_vez` insere numa conexão própria
    e a CheckViolation sobe até `_rodar()`, matando o resumo do dia de todas as
    contas seguintes. Não é um aviso que se perde; são todos."""
    with limpo.connection() as c:
        c.execute("insert into lembretes_enviados (conta_id, tipo, chave) "
                  "values (%s,'renovacao','1:60')", (CONTA,))
        c.commit()


def _agora(h=9):
    return datetime(2026, 9, 17, h, 0, tzinfo=timezone.utc)


def _espiao(monkeypatch):
    enviados = []
    monkeypatch.setattr("finance.cockpit.enviar_push",
                        lambda pool, conta_id, membro_id, titulo, corpo, url=None, badge=None:
                        (enviados.append((conta_id, membro_id, titulo)), 1)[1])
    monkeypatch.setattr("finance.aviso_log.registrar", lambda *a, **k: None)
    return enviados


def test_o_alerta_vai_pro_corretor_da_apolice(limpo, monkeypatch):
    """Decisão do dono, verbatim: "alerta pro corretor"."""
    from finance import lembretes as lb
    with limpo.connection() as c:
        ana = c.execute("insert into membros (conta_id, nome, papel) "
                        "values (%s,'Ana','vendedor') returning id", (CONTA,)).fetchone()[0]
        c.execute("insert into membros (conta_id, nome, papel) values (%s,'Dono','dono')", (CONTA,))
        c.commit()
    _apolice(limpo, corretor_id=ana, vigencia_fim=HOJE + timedelta(days=30))
    enviados = _espiao(monkeypatch)
    assert lb._renovacoes(limpo, _agora()) == 1
    assert [m for (_c, m, _t) in enviados] == [ana]


def test_apolice_sem_corretor_cai_no_dono_e_nos_gestores(limpo, monkeypatch):
    """Aviso que não chega a ninguém é pior que aviso nenhum: some calado."""
    from finance import lembretes as lb
    with limpo.connection() as c:
        dono = c.execute("insert into membros (conta_id, nome, papel) "
                         "values (%s,'Dono','dono') returning id", (CONTA,)).fetchone()[0]
        c.execute("insert into membros (conta_id, nome, papel) values (%s,'Ana','vendedor')", (CONTA,))
        c.commit()
    _apolice(limpo, corretor_id=None, vigencia_fim=HOJE + timedelta(days=30))
    enviados = _espiao(monkeypatch)
    lb._renovacoes(limpo, _agora())
    assert [m for (_c, m, _t) in enviados] == [dono]


def test_o_mesmo_degrau_nao_avisa_duas_vezes(limpo, monkeypatch):
    """O ticker roda a cada 2 minutos. Sem o dedup seriam 30 pushes por hora, sobre
    a mesma apólice, pro mesmo corretor."""
    from finance import lembretes as lb
    with limpo.connection() as c:
        c.execute("insert into membros (conta_id, nome, papel) values (%s,'Dono','dono')", (CONTA,))
        c.commit()
    _apolice(limpo, vigencia_fim=HOJE + timedelta(days=30))
    enviados = _espiao(monkeypatch)
    assert lb._renovacoes(limpo, _agora()) == 1
    assert lb._renovacoes(limpo, _agora()) == 0
    assert len(enviados) == 1


def test_degrau_novo_avisa_de_novo(limpo, monkeypatch):
    """Três toques por apólice, não um: o dedup é por (apólice, degrau)."""
    from finance import lembretes as lb
    with limpo.connection() as c:
        c.execute("insert into membros (conta_id, nome, papel) values (%s,'Dono','dono')", (CONTA,))
        c.commit()
    _apolice(limpo, vigencia_fim=date(2026, 11, 16))    # 60 dias a partir de 17/09
    enviados = _espiao(monkeypatch)
    lb._renovacoes(limpo, _agora())
    # trinta dias depois, a MESMA apólice alcança o degrau 30
    lb._renovacoes(limpo, datetime(2026, 10, 17, 9, 0, tzinfo=timezone.utc))
    assert len(enviados) == 2


def test_fora_da_hora_o_alerta_nao_sai(limpo, monkeypatch):
    from finance import lembretes as lb
    _apolice(limpo, vigencia_fim=HOJE + timedelta(days=30))
    enviados = _espiao(monkeypatch)
    assert lb._renovacoes(limpo, _agora(h=14)) == 0
    assert enviados == []


def test_o_que_esta_alem_de_60_dias_nao_vira_push(limpo, monkeypatch):
    """Os 90 dias são a lista; o alerta começa em 60. É a diferença entre consulta
    e interrupção — e é por isso que o dono pediu três toques e não cinco."""
    from finance import lembretes as lb
    with limpo.connection() as c:
        c.execute("insert into membros (conta_id, nome, papel) values (%s,'Dono','dono')", (CONTA,))
        c.commit()
    _apolice(limpo, vigencia_fim=HOJE + timedelta(days=80))
    enviados = _espiao(monkeypatch)
    assert lb._renovacoes(limpo, _agora()) == 0
    assert enviados == []
