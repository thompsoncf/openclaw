"""O rodízio com DOIS CHIPS — a combinação que nenhum teste cobria.

POR QUE ISTO EXISTE
`test_distribuicao.py` prova o rodízio; `test_wa_qr_segundo_chip.py` prova os chips.
Nenhum dos dois cruza os dois assuntos, e é exatamente o cruzamento que a Prime
Eventos vive: uma empresa, três vendedores e dois números de WhatsApp — o principal
e o "Campanha Thiago".

A pergunta que o dono fez — "o rodízio está entregando a fila certo, mesmo com os
dois chips?" — não dava pra responder pelo histórico, porque os leads foram
reatribuídos à mão. Este arquivo responde pelo mecanismo.

O QUE O DESENHO PROMETE (migração 171_conta_chip)
Uma fila SÓ por empresa, e o chip deliberadamente fora dela. A migração diz por quê:
criar uma segunda empresa pro segundo número daria "dois rodízios entre os mesmos
três vendedores" — cada um com o próprio ponteiro, e o vendedor recebendo o dobro
sem ninguém entender de onde.

É essa promessa que se verifica aqui: quem responde por um lead não depende de por
qual número ele entrou.
"""
import os

import pytest
from psycopg_pool import ConnectionPool

from finance import distribuicao as dist

_SQL = """
create table contas (id bigserial primary key, nome text, chip_de bigint);
create table membros (id bigserial primary key, conta_id bigint, nome text, email text,
  ativo boolean default true, whatsapp text, cockpit_pausado boolean default false);
create table prospeccao (id bigserial primary key, conta_id bigint, empresa text,
  vendedor_id bigint, atualizado_em timestamptz default now());
create table conversas (id bigserial primary key, conta_id bigint, prospeccao_id bigint,
  responsavel_membro_id bigint, chip_id bigint, visto_ate_id bigint);
create table distribuicao (conta_id bigint primary key, ativo boolean not null default false,
  ponteiro int not null default 0, avisar boolean not null default true,
  aviso_template_sid text, atualizado_em timestamptz not null default now());
create table distribuicao_fila (conta_id bigint, membro_id bigint, ordem int not null default 0,
  primary key (conta_id, membro_id));
"""


@pytest.fixture()
def pool():
    dbname = "zaq_dist_chips"
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
        c.execute(_SQL)
        c.commit()
    yield p
    p.close()


@pytest.fixture()
def prime(pool):
    """A Prime de verdade: empresa + chip filho + os três vendedores na fila.

    A ordem é a que está gravada na produção em 21/08/2026 — Jacqueline, Pedro,
    Thiago — pra o teste falar da configuração real e não de uma inventada.
    """
    with pool.connection() as c:
        empresa = c.execute("insert into contas (nome) values ('Prime Eventos') "
                            "returning id").fetchone()[0]
        chip2 = c.execute("insert into contas (nome, chip_de) values ('Campanha Thiago',%s) "
                          "returning id", (empresa,)).fetchone()[0]
        ids = {}
        for nome in ("JACQUELINE", "PEDRO YAN", "THIAGO"):
            ids[nome] = c.execute(
                "insert into membros (conta_id, nome, email) values (%s,%s,%s) returning id",
                (empresa, nome, f"{nome.split()[0].lower()}@prime.com")).fetchone()[0]
        dist.salvar(c, empresa, True, True, [ids["JACQUELINE"], ids["PEDRO YAN"], ids["THIAGO"]])
        c.commit()
    return {"empresa": empresa, "chip2": chip2, "ids": ids,
            "nomes": {v: k for k, v in ids.items()}}


def _chega_lead(c, empresa, chip_id):
    """Um lead entrando por um chip — como o inbound faz: a conversa guarda o chip,
    o rodízio roda pela EMPRESA."""
    lead = c.execute("insert into prospeccao (conta_id, empresa) values (%s,'Cliente') "
                     "returning id", (empresa,)).fetchone()[0]
    c.execute("insert into conversas (conta_id, prospeccao_id, chip_id) values (%s,%s,%s)",
              (empresa, lead, chip_id))
    return lead, dist.atribuir_se_sem_dono(c, empresa, lead)


# ═══════════════════ a fila é uma só ═══════════════════

def test_leads_dos_dois_chips_entram_na_MESMA_fila(prime, pool):
    """Seis leads alternando entre os dois números: a rotação continua de onde
    parou, sem reiniciar por chip.

    Se cada chip tivesse fila própria, o primeiro de cada um cairia na Jacqueline e
    o resultado seria 2/2/2 por acaso — mas na ordem errada. É a ORDEM que prova."""
    e, c2, nomes = prime["empresa"], prime["chip2"], prime["nomes"]
    with pool.connection() as c:
        recebeu = [nomes[_chega_lead(c, e, chip)[1]]
                   for chip in (None, c2, None, c2, None, c2)]
        c.commit()
    assert recebeu == ["JACQUELINE", "PEDRO YAN", "THIAGO",
                       "JACQUELINE", "PEDRO YAN", "THIAGO"]


def test_o_chip_nao_reserva_vendedor(prime, pool):
    """O chip se chama "Campanha Thiago" — e isso NÃO amarra o Thiago a ele.

    É a leitura que o nome sugere e o sistema não faz: a fila é da empresa. Se um dia
    a intenção for reservar, o lugar de mudar é a fila, não este teste."""
    e, c2, nomes = prime["empresa"], prime["chip2"], prime["nomes"]
    with pool.connection() as c:
        recebeu = [nomes[_chega_lead(c, e, c2)[1]] for _ in range(3)]
        c.commit()
    assert recebeu == ["JACQUELINE", "PEDRO YAN", "THIAGO"], \
        "o chip passou a decidir o vendedor"


def test_o_ponteiro_e_um_so_pros_dois_chips(prime, pool):
    """Um ponteiro, uma linha em `distribuicao`. Dois seriam dois rodízios — o
    defeito que a migração 171 evitou ao não criar uma segunda empresa."""
    e, c2 = prime["empresa"], prime["chip2"]
    with pool.connection() as c:
        _chega_lead(c, e, None)
        _chega_lead(c, e, c2)
        c.commit()
        linhas, ponteiro = c.execute(
            "select count(*), max(ponteiro) from distribuicao where conta_id=%s", (e,)).fetchone()
    assert linhas == 1
    assert ponteiro == 2, "o segundo lead não avançou o ponteiro do primeiro"


# ═══════════════════ a divisão em volume ═══════════════════

def test_trinta_leads_dos_dois_chips_dividem_em_tres_partes_iguais(prime, pool):
    """A pergunta do dono, em número: com a fila de três, ninguém leva mais que o
    terço — não importa por qual número os leads entraram."""
    e, c2, nomes = prime["empresa"], prime["chip2"], prime["nomes"]
    conta = {"JACQUELINE": 0, "PEDRO YAN": 0, "THIAGO": 0}
    with pool.connection() as c:
        for i in range(30):
            # 2 de cada 3 pelo chip principal, 1 pelo da campanha — proporção
            # parecida com a real, e de propósito NÃO divisível por 3, pra um viés
            # por chip aparecer como desequilíbrio.
            chip = c2 if i % 3 == 2 else None
            conta[nomes[_chega_lead(c, e, chip)[1]]] += 1
        c.commit()
    assert conta == {"JACQUELINE": 10, "PEDRO YAN": 10, "THIAGO": 10}


def test_vendedor_pausado_sai_da_vez_nos_dois_chips(prime, pool):
    """`cockpit_pausado` é o botão do próprio vendedor. Ele tem que valer pra fila
    inteira — não faria sentido pausar num número e continuar recebendo no outro.

    (O teste dedicado do rodízio não cobre `cockpit_pausado` — a coluna está no
    schema dele e nunca é exercitada.)"""
    e, c2, ids, nomes = prime["empresa"], prime["chip2"], prime["ids"], prime["nomes"]
    with pool.connection() as c:
        c.execute("update membros set cockpit_pausado=true where id=%s", (ids["PEDRO YAN"],))
        recebeu = [nomes[_chega_lead(c, e, chip)[1]] for chip in (None, c2, None, c2)]
        c.commit()
    assert "PEDRO YAN" not in recebeu
    assert set(recebeu) == {"JACQUELINE", "THIAGO"}


def test_quem_volta_de_pausa_volta_pra_fila(prime, pool):
    e, ids, nomes = prime["empresa"], prime["ids"], prime["nomes"]
    with pool.connection() as c:
        c.execute("update membros set cockpit_pausado=true where id=%s", (ids["THIAGO"],))
        [_chega_lead(c, e, None) for _ in range(4)]
        c.execute("update membros set cockpit_pausado=false where id=%s", (ids["THIAGO"],))
        recebeu = [nomes[_chega_lead(c, e, None)[1]] for _ in range(6)]
        c.commit()
    assert recebeu.count("THIAGO") == 2, "o Thiago não voltou pra rotação"


# ═══════════════════ o que NÃO pode acontecer ═══════════════════

def test_lead_que_ja_tem_dono_nao_e_roubado_por_chegar_pelo_outro_chip(prime, pool):
    """O mesmo cliente escrevendo pro segundo número não muda de vendedor. Sem isto,
    quem tem os dois números salvos trocaria de dono a cada mensagem."""
    e, c2, ids = prime["empresa"], prime["chip2"], prime["ids"]
    with pool.connection() as c:
        lead, dono = _chega_lead(c, e, None)
        assert dono is not None
        c.execute("insert into conversas (conta_id, prospeccao_id, chip_id) values (%s,%s,%s)",
                  (e, lead, c2))
        assert dist.atribuir_se_sem_dono(c, e, lead) is None
        c.commit()
        assert c.execute("select vendedor_id from prospeccao where id=%s",
                         (lead,)).fetchone()[0] == dono


def test_o_chip_nao_atravessa_empresa(prime, pool):
    """Chip de uma empresa não pode puxar vendedor de outra."""
    e, nomes = prime["empresa"], prime["nomes"]
    with pool.connection() as c:
        vizinha = c.execute("insert into contas (nome) values ('Vizinha') returning id").fetchone()[0]
        outro = c.execute("insert into membros (conta_id, nome, email) "
                          "values (%s,'De fora','fora@x.com') returning id", (vizinha,)).fetchone()[0]
        dist.salvar(c, vizinha, True, True, [outro])
        recebeu = [nomes[_chega_lead(c, e, None)[1]] for _ in range(3)]
        c.commit()
    assert set(recebeu) == {"JACQUELINE", "PEDRO YAN", "THIAGO"}


def test_com_o_rodizio_desligado_o_lead_nasce_sem_dono_nos_dois_chips(prime, pool):
    """É o estado em que a Prime esteve de 13 a 16/08: 14 leads órfãos. Desligado é
    desligado — mas o lead continua existindo, e é isso que permite recuperá-lo."""
    e, c2 = prime["empresa"], prime["chip2"]
    with pool.connection() as c:
        c.execute("update distribuicao set ativo=false where conta_id=%s", (e,))
        for chip in (None, c2):
            lead, dono = _chega_lead(c, e, chip)
            assert dono is None
            assert c.execute("select 1 from prospeccao where id=%s", (lead,)).fetchone()
        c.commit()


def test_a_conversa_do_chip_certo_recebe_o_responsavel(prime, pool):
    """O rodízio marca `conversas.responsavel_membro_id` — e tem que marcar a
    conversa que entrou pelo chip, senão o inbox daquele número segue dizendo "sem
    responsável"."""
    e, c2 = prime["empresa"], prime["chip2"]
    with pool.connection() as c:
        lead, dono = _chega_lead(c, e, c2)
        c.commit()
        r = c.execute("select chip_id, responsavel_membro_id from conversas "
                      "where prospeccao_id=%s", (lead,)).fetchone()
    assert r == (c2, dono)
