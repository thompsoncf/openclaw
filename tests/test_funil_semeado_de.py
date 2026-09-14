"""DE ONDE VEIO O RÓTULO: a semente do ramo, ou o dono (migração 254).

O QUE ESTE ARQUIVO IMPEDE DE VOLTAR, medido em 14/09/2026
(docs/mockups/funil_semente_do_ramo.html): de 8 contas com funil, UMA tinha as
colunas do próprio ramo — e era a Prime, que o dono reconstruiu na mão. A Doce
Mell, citada pelo nome no docstring do `funil_modelo` como o caso que o módulo
veio resolver, seguia nas seis genéricas três dias depois.

E o agravante que fechava o círculo: quando o bloco enfim propunha corrigir, ele
vinha DESMARCADO com a nota "você já renomeou esta etapa". Era `_generico()`
comparando o rótulo com a lista de antes de 11/09 — e desde 11/09 as contas
nascem semeadas PELO PERFIL. A Liberal (conta 37) recebeu "Reunião marcada" do
próprio sistema e a tela ia lhe dizer que ela tinha renomeado.

O teste que guarda a razão de tudo é
`test_o_que_a_semente_nomeou_vem_MARCADO_e_o_que_o_dono_nomeou_nao`.
"""
import os
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from finance import funil_modelo as fm
from finance import funil_perda as fp
from finance import raio_x_perfil as rxp

MIG = Path(__file__).resolve().parent.parent / "db" / "migracoes"
CONTA = 91

_SQL = """
create table prospeccao (id bigserial primary key, conta_id bigint, empresa text,
  contato text, status text default 'novo', estagio text default 'lead',
  perda_motivo text, criado_em timestamptz default now());
create table funil_etapas (id bigserial primary key,
  -- 254: de onde veio o rótulo — a semente do ramo, ou o dono
  semeado_de text, conta_id bigint, chave text,
  rotulo text, ordem int not null default 0, fixa boolean not null default false,
  unique (conta_id, chave));
"""


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_semeado_de_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=3, open=True, kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_SQL)
        c.execute((MIG / "238_etapa_sai_do_quadro.sql").read_text(encoding="utf-8"))
        c.execute((MIG / "235_motivos_de_perda_da_conta.sql").read_text(encoding="utf-8"))
        c.execute((MIG / "254_funil_semeado_de.sql").read_text(encoding="utf-8"))
        c.commit()
    yield p
    p.close()


@pytest.fixture
def limpo(pool):
    with pool.connection() as c:
        c.execute("delete from funil_etapas")
        c.execute("delete from funil_motivos_perda")
        c.execute("delete from prospeccao")
        c.commit()
    return pool


def _antigas(pool, rotulos: dict, conta=CONTA):
    """Linhas como estavam ANTES da 254: `semeado_de` nulo."""
    with pool.connection() as c:
        for i, (ch, rot) in enumerate(rotulos.items()):
            c.execute("""insert into funil_etapas (conta_id, chave, rotulo, ordem)
                         values (%s,%s,%s,%s)""", (conta, ch, rot, i * 10))
        c.commit()


# ── a semente carimba ─────────────────────────────────────────────────────
def test_semear_grava_de_qual_ramo_veio(limpo):
    with limpo.connection() as c:
        fm.semear(c, CONTA, "seguros")
        c.commit()
        linhas = dict(c.execute(
            "select chave, semeado_de from funil_etapas where conta_id=%s", (CONTA,)).fetchall())
    assert linhas and set(linhas.values()) == {"seguros"}


def test_a_semente_dos_motivos_tambem_carimba(limpo):
    with limpo.connection() as c:
        fp.motivos(c, CONTA, "seguros", so_ativos=False)
        c.commit()
        linhas = dict(c.execute(
            "select chave, semeado_de from funil_motivos_perda where conta_id=%s",
            (CONTA,)).fetchall())
    assert linhas["renovou_direto"] == "seguros"


# ── o carimbo das linhas antigas ──────────────────────────────────────────
def test_carimbo_separa_a_semente_do_apelido_do_dono(limpo):
    """A regra: rótulo que bate com alguma semente conhecida é semente; o que não
    bate com nenhuma foi o dono que escreveu."""
    _antigas(limpo, {"novo": "Novo",                 # genérico
                     "qualificado": "Reunião marcada",  # semente do recorrente
                     "proposta": "Agendado Visita",  # semente de eventos, chave errada
                     "perdido": "Entregue"})         # apelido da conta 3
    with limpo.connection() as c:
        assert fm.carimbar(c, CONTA) == 4
        c.commit()
        r = dict(c.execute("select chave, semeado_de from funil_etapas where conta_id=%s",
                           (CONTA,)).fetchall())
    assert r["novo"] == "semente" and r["qualificado"] == "semente"
    assert r["perdido"] == fm.DO_DONO
    # rótulo de OUTRA chave não vale como semente desta: 'Agendado Visita' é de
    # `qualificado`, não de `proposta`
    assert r["proposta"] == fm.DO_DONO


def test_carimbar_de_novo_nao_mexe_em_nada(limpo):
    """Roda na leitura de toda tela: se não fosse idempotente, cada abertura
    reescreveria a linha e o `semeado_de` que o dono zerou voltaria."""
    _antigas(limpo, {"novo": "Novo"})
    with limpo.connection() as c:
        assert fm.carimbar(c, CONTA) == 1
        assert fm.carimbar(c, CONTA) == 0
        c.commit()


def test_carimbo_nao_alcanca_outra_conta(limpo):
    _antigas(limpo, {"novo": "Novo"}, conta=CONTA)
    _antigas(limpo, {"novo": "Novo"}, conta=CONTA + 1)
    with limpo.connection() as c:
        fm.carimbar(c, CONTA)
        c.commit()
        vizinha = c.execute("select semeado_de from funil_etapas where conta_id=%s",
                            (CONTA + 1,)).fetchone()[0]
    assert vizinha is None


# ── o que o carimbo muda na proposta ──────────────────────────────────────
def test_o_que_a_semente_nomeou_vem_MARCADO_e_o_que_o_dono_nomeou_nao(limpo):
    """A RAZÃO DO PR (ver o docstring do módulo). A Liberal recebeu "Reunião
    marcada" do sistema — a proposta de trocar tem que vir marcada, sem acusá-la
    de ter renomeado. O apelido do dono continua desmarcado, com a nota."""
    with limpo.connection() as c:
        fm.semear(c, CONTA, "recorrente")          # o que aconteceu na conta 37
        c.execute("""update funil_etapas set rotulo='Entregue', semeado_de=%s
                      where conta_id=%s and chave='perdido'""", (fm.DO_DONO, CONTA))
        c.commit()
        itens = {i["id"]: i for i in fm.plano(c, CONTA, "seguros")}
    assert itens["rotulo:qualificado"]["marcado"] is True
    assert itens["rotulo:qualificado"]["nota"] == ""
    assert itens["rotulo:perdido"]["marcado"] is False
    assert "já renomeou" in itens["rotulo:perdido"]["nota"]


def test_linha_sem_carimbo_erra_pro_lado_seguro(limpo):
    """NULL é "ainda não sei". Responder "foi o dono" só faz a proposta vir
    desmarcada — que é exatamente como era antes da 254."""
    _antigas(limpo, {"qualificado": "Reunião marcada"})
    with limpo.connection() as c:
        itens = {i["id"]: i for i in fm.plano(c, CONTA, "seguros")}
    assert itens["rotulo:qualificado"]["marcado"] is False


def test_adotar_o_modelo_recarimba_a_linha(limpo):
    """Depois de adotar, o nome é do ramo — e não pode voltar a ser proposto."""
    with limpo.connection() as c:
        fm.semear(c, CONTA, "recorrente")
        c.commit()
        fm.aplicar(c, CONTA, "seguros", ["rotulo:qualificado"])
        c.commit()
        r = c.execute("""select rotulo, semeado_de from funil_etapas
                          where conta_id=%s and chave='qualificado'""", (CONTA,)).fetchone()
        assert r == ("Cotação enviada", "seguros")
        assert "rotulo:qualificado" not in {i["id"] for i in fm.plano(c, CONTA, "seguros")}


# ── a faixa ───────────────────────────────────────────────────────────────
def test_a_faixa_conta_o_que_o_ramo_mudaria(limpo):
    with limpo.connection() as c:
        fm.semear(c, CONTA, "recorrente")
        c.commit()
        assert fm.desencontro(c, CONTA, "seguros") == 1      # "Reunião marcada"
        assert fm.desencontro(c, CONTA, "recorrente") == 0   # já está no dela


def test_a_faixa_NAO_conta_o_apelido_do_dono(limpo):
    """A Prime chama 'ganho' de "Evento Realizado" há meses. Uma faixa dizendo que
    o funil dela "não está no modelo" seria mentira, e viraria ruído diário até ela
    aprender a ignorar — que é como se estraga um aviso."""
    with limpo.connection() as c:
        fm.semear(c, CONTA, "eventos")
        c.execute("""update funil_etapas set rotulo='Evento Realizado', semeado_de=%s
                      where conta_id=%s and chave='ganho'""", (fm.DO_DONO, CONTA))
        c.commit()
        assert fm.desencontro(c, CONTA, "eventos") == 0


def test_a_faixa_conta_etapa_que_falta(limpo):
    """A Doce Mell: seis genéricas num ramo cujo modelo tem sete."""
    _antigas(limpo, {ch: rot for ch, rot, *_ in rxp.ETAPAS_GENERICAS})
    with limpo.connection() as c:
        fm.carimbar(c, CONTA)
        c.commit()
        # falta 'follow_up', e 'qualificado' se chamaria "Agendado Visita"
        assert fm.desencontro(c, CONTA, "eventos") >= 2


# ── o modelo do ramo pros MOTIVOS ─────────────────────────────────────────
def test_o_plano_dos_motivos_acrescenta_o_que_so_o_ramo_tem(limpo):
    """A Liberal com a lista do recorrente: o que falta é o que só seguro perde."""
    with limpo.connection() as c:
        fp.motivos(c, CONTA, "recorrente", so_ativos=False)
        c.commit()
        itens = {i["id"]: i for i in fp.plano_motivos(c, CONTA, "seguros")}
    assert "criar:renovou_direto" in itens and itens["criar:renovou_direto"]["marcado"] is True
    assert "criar:cobertura_nao_atendeu" in itens


def test_o_plano_dos_motivos_respeita_o_nome_que_o_dono_deu(limpo):
    with limpo.connection() as c:
        fp.motivos(c, CONTA, "recorrente", so_ativos=False)
        c.execute("""update funil_motivos_perda set rotulo='Sumiu', semeado_de=%s
                      where conta_id=%s and chave='sumiu_apos_proposta'""",
                  (fm.DO_DONO, CONTA))
        c.commit()
        itens = {i["id"]: i for i in fp.plano_motivos(c, CONTA, "seguros")}
    assert itens["rotulo:sumiu_apos_proposta"]["marcado"] is False
    assert "já renomeou" in itens["rotulo:sumiu_apos_proposta"]["nota"]


def test_adotar_os_motivos_nao_apaga_nenhum(limpo):
    """Motivo fora do modelo é DESLIGADO, nunca apagado: o lead perdido por ele
    continua lendo o rótulo na ficha."""
    with limpo.connection() as c:
        fp.motivos(c, CONTA, "recorrente", so_ativos=False)
        c.commit()
        antes = c.execute("select count(*) from funil_motivos_perda where conta_id=%s",
                          (CONTA,)).fetchone()[0]
        ids = [i["id"] for i in fp.plano_motivos(c, CONTA, "seguros")]
        fp.aplicar_motivos(c, CONTA, "seguros", ids)
        c.commit()
        depois = c.execute("select count(*) from funil_motivos_perda where conta_id=%s",
                           (CONTA,)).fetchone()[0]
        desligados = c.execute("""select count(*) from funil_motivos_perda
                                   where conta_id=%s and not ativo""", (CONTA,)).fetchone()[0]
    assert depois > antes          # os do ramo entraram
    assert desligados >= 1         # e o que não é do ramo saiu da lista, não do banco


def test_renomear_um_motivo_a_mao_limpa_o_carimbo(limpo):
    """Depois disso o bloco para de propor trocar o nome sem o dono marcar."""
    with limpo.connection() as c:
        fp.motivos(c, CONTA, "recorrente", so_ativos=False)
        c.commit()
        mid = c.execute("""select id from funil_motivos_perda
                            where conta_id=%s and chave='achou_caro'""", (CONTA,)).fetchone()[0]
        fp.salvar_motivo(c, CONTA, rotulo="Caro demais", ordem=10, ativo=True,
                  exige_descricao=False, motivo_id=mid)
        c.commit()
        r = c.execute("select semeado_de from funil_motivos_perda where id=%s", (mid,)).fetchone()[0]
    assert r == fm.DO_DONO


def test_salvar_sem_mexer_no_rotulo_nao_limpa_o_carimbo(limpo):
    """Mudar ordem ou a exigência de texto não é dar nome à etapa."""
    with limpo.connection() as c:
        fp.motivos(c, CONTA, "recorrente", so_ativos=False)
        c.commit()
        mid, rot = c.execute("""select id, rotulo from funil_motivos_perda
                                 where conta_id=%s and chave='achou_caro'""", (CONTA,)).fetchone()
        fp.salvar_motivo(c, CONTA, rotulo=rot, ordem=99, ativo=True,
                  exige_descricao=True, motivo_id=mid)
        c.commit()
        r = c.execute("select semeado_de from funil_motivos_perda where id=%s", (mid,)).fetchone()[0]
    assert r == "recorrente"
