"""O primeiro portão de CONTA — e por que ele precisou existir.

O SISTEMA DE AVISOS MIRAVA SÓ POR NICHO
`PUBLICOS` era um dicionário de funções de SLUG: "esta mudança vale pra quem vende
serviço", "pra quem vende data". Isso cobre a esmagadora maioria dos casos, e é o
desenho certo — a regra vale pra qualquer conta daquele ramo, hoje e no ano que vem.

DUAS MUDANÇAS DE AGOSTO NÃO CABEM NISSO
O microfone no app do vendedor (#501) e o sumiço do atalho "Mandar no WhatsApp"
(#506) não valem por ramo de negócio: valem por CANAL.

  • QR (WhatsApp da própria empresa): sem janela de 24h. O microfone aparece e o
    atalho sumiu porque não faz falta.
  • Twilio / Cloud API: janela de 24h. O microfone não aparece e o atalho continua —
    fechá-lo deixaria o vendedor sem meio de responder.

Mirar por nicho erraria dos dois lados: prometeria microfone pra quem não tem e
calaria sobre um botão que sumiu pra quem perdeu.

E 'eventos' ACERTARIA hoje, por coincidência — as duas contas com QR e equipe são de
eventos. Mas a conta 34 tem QR e Twilio configurados ao mesmo tempo: o nicho não
decide o canal. Um teste que passasse por essa coincidência estaria medindo a base
de hoje, não a regra.
"""
import os
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from finance import novidades as nv

BASE = Path(__file__).resolve().parent.parent / "db" / "migracoes"


@pytest.fixture()
def pool(monkeypatch):
    dbname = "zaq_novidade_canal"
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1,
                           open=True, kwargs={"autocommit": True, "prepare_threshold": None})
    with admin.connection() as c:
        c.execute("select pg_terminate_backend(pid) from pg_stat_activity "
                  "where datname=%s and pid <> pg_backend_pid()", (dbname,))
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=3, open=True,
                       kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute("""create table nichos (id bigserial primary key, nome text,
                     slug text unique, tipo text)""")
        c.execute("""create table contas (id bigserial primary key, nome text,
                     nicho_id bigint references nichos(id),
                     criado_em timestamptz not null default now(), chip_de bigint)""")
        c.execute((BASE / "174_novidades.sql").read_text(encoding="utf-8"))
        c.execute((BASE / "184_novidade_voz_e_porta_fechada.sql").read_text(encoding="utf-8"))
        # 199: pra_quem, resumo, link — `listar` lê as três colunas.
        c.execute((BASE / "199_novidades_pra_quem.sql").read_text(encoding="utf-8"))
        for slug in ("eventos", "consultoria"):
            c.execute("insert into nichos (nome, slug) values (%s,%s)", (slug, slug))
        c.execute("""insert into contas (id, nome, nicho_id, criado_em) values
            (1,'Prime (QR)',      (select id from nichos where slug='eventos'),     now() - interval '60 days'),
            (2,'Consultoria (QR)',(select id from nichos where slug='consultoria'), now() - interval '60 days'),
            (3,'Prime (Twilio)',  (select id from nichos where slug='eventos'),     now() - interval '60 days'),
            (4,'Sem nicho (QR)',  null,                                             now() - interval '60 days')""")
        c.commit()
    yield p
    p.close()


@pytest.fixture()
def canal(monkeypatch):
    """Quem está em qual canal. O portão pergunta a `whatsapp_out.provedor_da_conta`,
    que é a MESMA função que decide se o microfone aparece."""
    mapa = {1: "qr", 2: "qr", 3: "twilio", 4: "qr"}
    import finance.whatsapp_out as wo
    monkeypatch.setattr(wo, "provedor_da_conta", lambda c, conta_id: mapa.get(conta_id, "cloud"))
    return mapa


def _chaves(pool, conta_id):
    return {n["chave"] for n in nv.listar(pool, conta_id)}


# ═════════════════ o portão em si ═════════════════

def test_o_portao_e_a_mesma_pergunta_que_decide_o_microfone(pool, canal):
    """Não é uma regra paralela: é `provedor_da_conta`, a função que o Cockpit já
    consulta. Lista paralela acerta no primeiro dia e diverge no terceiro."""
    assert nv._canal_proprio(pool, 1) is True
    assert nv._canal_proprio(pool, 3) is False


def test_quem_esta_no_qr_recebe_os_dois_avisos(pool, canal):
    assert {"voz-no-app-do-vendedor", "atalho-whatsapp-fechado"} <= _chaves(pool, 1)


def test_quem_esta_no_twilio_nao_recebe_nenhum(pool, canal):
    """Lá o microfone não existe e o atalho continua no lugar. Avisar seria mentir
    duas vezes."""
    assert _chaves(pool, 3) == set()


def test_o_nicho_nao_decide_o_canal(pool, canal):
    """Duas contas de EVENTOS, canais diferentes, resultados diferentes. É este
    teste que impede alguém de trocar o portão por 'eventos' — que acertaria na base
    de hoje e erraria na semana que vem."""
    assert "voz-no-app-do-vendedor" in _chaves(pool, 1)      # eventos + QR
    assert "voz-no-app-do-vendedor" not in _chaves(pool, 3)  # eventos + Twilio


def test_conta_sem_nicho_no_qr_recebe(pool, canal):
    """Portão de conta não passa pelo `_declarou`: quem ainda não escolheu ramo mas
    já conectou o WhatsApp perdeu o botão do mesmo jeito."""
    assert "atalho-whatsapp-fechado" in _chaves(pool, 4)


def test_consultoria_no_qr_tambem_recebe(pool, canal):
    """A prova pelo outro lado: não é sobre eventos, é sobre canal."""
    assert {"voz-no-app-do-vendedor", "atalho-whatsapp-fechado"} <= _chaves(pool, 2)


# ═════════════════ falha fechada ═════════════════

def test_sem_saber_o_canal_o_aviso_NAO_sai(pool, monkeypatch):
    """Melhor um aviso que não sai do que um que sai pra quem não devia — a mesma
    escolha do público desconhecido."""
    import finance.whatsapp_out as wo

    def explode(*a, **k):
        raise RuntimeError("sem canais_config")
    monkeypatch.setattr(wo, "provedor_da_conta", explode)
    assert nv._canal_proprio(pool, 1) is False
    assert _chaves(pool, 1) == set()


def test_alcanca_sem_banco_responde_nao(pool, canal):
    """Quem chamar `alcanca` sem pool/conta não pode receber um "sim" por engano —
    é disso que `nichos_alcancados` depende pra continuar respondendo sobre slugs."""
    assert nv.alcanca("canal_proprio", "eventos") is False
    assert nv.alcanca("canal_proprio", "eventos", pool, 1) is True


def test_nichos_alcancados_nao_inventa_resposta(pool, canal):
    """A mesma padaria pode estar no QR ou na API oficial: a pergunta "que slugs este
    público alcança" não se responde. Vazio é a verdade; um conjunto seria invenção."""
    assert nv.nichos_alcancados("canal_proprio") == set()
    assert nv.nichos_alcancados("eventos") != set(), "os portões de nicho continuam respondendo"


# ═════════════════ os portões antigos não mudaram ═════════════════

def test_os_cinco_portoes_de_nicho_continuam_iguais(pool, canal):
    assert set(nv.PUBLICOS_NICHO) == {"todos", "produto", "servico", "eventos", "recorrente"}
    assert set(nv.PUBLICOS) == set(nv.PUBLICOS_NICHO) | {"canal_proprio"}


def test_aviso_de_nicho_nao_passou_a_depender_do_canal(pool, canal):
    """A conta 3 é Twilio e não recebe os avisos de canal — mas continua recebendo
    os de nicho. Um portão novo não pode ter estreitado os antigos."""
    with pool.connection() as c:
        c.execute("""insert into novidades (chave, tipo, publico, titulo, corpo, publicado_em)
                     values ('x-eventos','novidade','eventos','T','C', now() - interval '1 day')""")
        c.commit()
    assert "x-eventos" in _chaves(pool, 3)


# ═════════════════ o que os dois avisos dizem ═════════════════

def test_o_atalho_fechado_e_MUDANCA_e_a_voz_e_NOVIDADE(pool, canal):
    """O tipo não é sobre tamanho, é sobre o que a pessoa precisa fazer. O vendedor
    PERDEU um botão: 'mudanca' exige o "Entendi", e é isso que permite saber quem já
    viu — a pergunta que a Doce Mell criou quando perdeu o "Fechar contrato"."""
    with pool.connection() as c:
        linhas = dict(c.execute("select chave, tipo from novidades "
                                "where publico='canal_proprio'").fetchall())
    assert linhas == {"voz-no-app-do-vendedor": "novidade",
                      "atalho-whatsapp-fechado": "mudanca"}


def test_o_aviso_da_porta_explica_onde_o_atalho_CONTINUA(pool, canal):
    """Quem lê precisa entender que não é um sumiço universal — senão vira medo de
    que o botão suma no resto também."""
    with pool.connection() as c:
        corpo = c.execute("select corpo from novidades "
                          "where chave='atalho-whatsapp-fechado'").fetchone()[0]
    assert "24 horas" in corpo
    assert "continua" in corpo.lower()


def test_a_voz_veio_depois_da_porta_e_os_avisos_respeitam_a_ordem(pool, canal):
    """O botão só saiu depois de o microfone entrar — antes disso, fechar a porta
    seria tirar do vendedor a única forma de mandar áudio. A ordem dos avisos na
    tela conta essa história."""
    with pool.connection() as c:
        voz, porta = c.execute(
            """select (select publicado_em from novidades where chave='voz-no-app-do-vendedor'),
                      (select publicado_em from novidades where chave='atalho-whatsapp-fechado')"""
        ).fetchone()
    assert voz < porta


def test_reaplicar_a_migracao_nao_duplica_nem_troca_o_id(pool, canal):
    """Se o id mudasse, quem já leu veria tudo outra vez."""
    with pool.connection() as c:
        antes = dict(c.execute("select chave, id from novidades").fetchall())
        c.execute((BASE / "184_novidade_voz_e_porta_fechada.sql").read_text(encoding="utf-8"))
        c.commit()
        assert dict(c.execute("select chave, id from novidades").fetchall()) == antes
