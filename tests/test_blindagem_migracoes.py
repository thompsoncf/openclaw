"""Blindagem: aplicar_migracoes roda as pendentes no deploy, é idempotente e
serializado por advisory lock (não quebra se dois processos subirem juntos).

Simula o cenário de PRODUÇÃO: banco com as tabelas base mas SEM as colunas novas,
migrações antigas já marcadas como aplicadas, só 088/089 pendentes.
"""
import os
import glob

import pytest
from psycopg_pool import ConnectionPool

from db.aplicar_migracoes import aplicar_migracoes

# tabelas base mínimas que as migrações 088/089 referenciam (FKs)
_BASE_SQL = """
create table contas (id bigserial primary key, tipo text, nome text, chip_de bigint);
create table membros (id bigserial primary key, conta_id bigint, nome text);
create table lancamentos (id bigserial primary key, conta_id bigint references contas(id),
  membro_id bigint, tipo text, valor_centavos bigint, categoria text, descricao text default '',
  data date, pagamento text default '', origem text default 'manual', comprovante text default '',
  chave varchar(44), natureza text, criado_em timestamptz default now());
-- funcionarios vem da 053 (marcada como aplicada no baseline). admitido_em é dessa
-- mesma migração e precisa existir aqui: o backfill da 150 lê essa coluna pra datar
-- a vigência inicial de salário de quem já estava cadastrado.
-- titulos também vem da 053: a 162 (vínculo título↔parcela do orçamento) altera
-- essa tabela, e migração que roda depois de um baseline não pode inventar o que o
-- baseline diz que já existe.
create table titulos (id bigserial primary key, conta_id bigint references contas(id),
  tipo text not null, descricao text not null, contraparte text not null default '',
  valor_centavos int not null, vencimento date not null, status text default 'aberto',
  recorrente boolean default false, categoria text default '', lancamento_id bigint,
  cobranca_link_url text, pago_em date, criado_por bigint,
  criado_em timestamptz default now());
-- app_config vem da 054 (dentro do baseline). A 181 grava a chave do aviso de
-- vencimento aqui: sem a tabela no baseline ela quebraria neste teste — e só
-- neste teste, porque em produção a 054 já rodou. O conserto certo é declarar a
-- tabela aqui, não fazer a 181 recriar o que o baseline garante que existe.
create table app_config (chave text primary key, valor text not null,
  atualizado_em timestamptz not null default now());
-- tokens_reset_senha vem da 041 (dentro do baseline). A 185 a altera pra o token
-- poder apontar pra um MEMBRO — sem ela declarada aqui, a 185 quebraria só neste
-- teste. Na forma exata da 041: conta_id NOT NULL e sem membro_id, que é o estado
-- de onde a 185 parte em produção.
create table tokens_reset_senha (token text primary key,
  conta_id bigint not null references contas(id),
  criado_em timestamptz not null default now(),
  expira_em timestamptz not null, usado boolean not null default false);
create table funcionarios (id bigserial primary key, conta_id bigint references contas(id),
  nome text, salario_centavos int default 0, pro_labore boolean default false,
  ativo boolean default true, admitido_em date);
create table folha_eventos (id bigserial primary key, conta_id bigint references contas(id),
  funcionario_id bigint references funcionarios(id),
  tipo text not null check (tipo in ('vale','extra','desconto','pagamento')),
  valor_centavos int, competencia date, descricao text default '', lancamento_id bigint,
  criado_em timestamptz default now());
create table nichos (id bigserial primary key, nome text, slug text unique, tipo text,
  ativo boolean not null default true);
-- canais_config vem da 081 (marcada como aplicada no baseline); a 096 a altera,
-- então precisa existir aqui. Colunas mínimas que a 096 assume (+ token da 084).
create table canais_config (id bigserial primary key, conta_id bigint, canal text,
  identificador text, ativo boolean default true, token text);
-- prospeccao vem da 075 (marcada como aplicada); a 102 adiciona colunas de decisor.
-- whatsapp/telefone também são da 075: a 148 lê os dois pra reconstruir o número
-- que a campanha já tentou.
create table prospeccao (id bigserial primary key, conta_id bigint,
  whatsapp text, telefone text);
-- prospeccao_atividades vem da 075 também (marcada como aplicada); a 127 recria
-- o check de tipo pra incluir 'bounce'.
create table prospeccao_atividades (id bigserial primary key, prospeccao_id bigint,
  tipo text not null check (tipo in ('ligacao','whatsapp','email','reuniao','visita','nota')));
-- campanhas vem da 086 (marcada como aplicada); a 104/105 adicionam colunas e a
-- 170 lê modelo_codigo pra recuperar a atribuição de quem nasceu sem ela.
create table campanhas (id bigserial primary key, conta_id bigint, modelo_codigo text);
-- campanha_passos também vem da 086: a 170 compara os passos da campanha com os do
-- modelo padrão pra decidir se pode marcá-la como 'generico' sem inventar a origem.
create table campanha_passos (id bigserial primary key, campanha_id bigint, ordem int,
  assunto text, corpo text, usar_ia boolean default false);
-- campanha_alvos vem da 086 (com status); a 105 adiciona wa_status/wa_em e a 107 indexa.
create table campanha_alvos (id bigserial primary key, campanha_id bigint, prospeccao_id bigint,
  status text);
-- mensagens vem da 080 (marcada como aplicada); a 116 adiciona a coluna 'status'.
create table mensagens (id bigserial primary key, conversa_id bigint, canal text,
  direcao text, autor text, texto text, provider_sid text, membro_id bigint,
  criado_em timestamptz default now(),
  midia_ref jsonb, midia_tipo text, midia_meta jsonb);
-- conversas vem da 080 também (marcada como aplicada); a 140 adiciona contato_nome.
create table conversas (id bigserial primary key, conta_id bigint, prospeccao_id bigint,
  canal text, contato_ref text, status text default 'aberta',
  agente_ativo boolean default false, responsavel_membro_id bigint,
  janela_expira_em timestamptz, ultima_msg_em timestamptz default now(),
  criado_em timestamptz default now(), chip_id bigint, visto_ate_id bigint);
-- orcamentos vem da 045 (marcada como aplicada); a 147 dá a ela o modo evento
-- (colunas do evento/parcelas, numeração por conta e o backfill do número).
create table orcamentos (id bigserial primary key, conta_id bigint references contas(id));
-- servicos_catalogo é criada em runtime (finance/servicos_catalogo.garantir_tabela);
-- a 148 adiciona categoria/foto do item.
create table servicos_catalogo (id bigserial primary key, conta_id bigint references contas(id),
  slug text, nome text);
-- pessoas vem da 066 (marcada como aplicada); a 131 adiciona cnpj/tipo.
create table pessoas (id bigserial primary key, cpf text, celular text,
  nome text not null default '', email text);
-- clientes (a relação loja↔pessoa) vem da 064; a 149 adiciona cidade/uf.
create table clientes (id bigserial primary key, dono_id bigint references contas(id),
  pessoa_id bigint references pessoas(id), nome text, telefone text, email text,
  obs text, ativo boolean not null default true);
create table schema_migrations (id serial primary key, nome text unique not null,
  executada_em timestamptz default now());
"""


def _pendentes_a_partir_de(corte: str) -> list[str]:
    """Nomes das migrações >= corte (as que ficam pendentes após o baseline)."""
    nomes = sorted(os.path.basename(f) for f in glob.glob("db/migracoes/*.sql"))
    return [n for n in nomes if n >= corte]


@pytest.fixture()
def pool():
    url = os.environ["TEST_DATABASE_URL"]
    # banco dedicado e descartável pra este teste (não colide com outros)
    admin = ConnectionPool(url, min_size=1, max_size=1, open=True)
    dbname = "zaq_blindagem_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    test_url = url.rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(test_url, min_size=1, max_size=3, open=True)
    with p.connection() as c:
        c.execute(_BASE_SQL)
        c.commit()
    yield p
    p.close()


def _baseline_ate_087(pool):
    """Marca todas as migrações < 088 como já aplicadas (como no prod real)."""
    nomes = sorted(os.path.basename(f) for f in glob.glob("db/migracoes/*.sql"))
    antigas = [n for n in nomes if n < "088"]
    with pool.connection() as c:
        for n in antigas:
            c.execute("insert into schema_migrations(nome) values(%s) on conflict do nothing", (n,))
        c.commit()
    return antigas


def _col_existe(pool, tabela, coluna):
    with pool.connection() as c:
        r = c.execute(
            "select 1 from information_schema.columns where table_name=%s and column_name=%s",
            (tabela, coluna)).fetchone()
    return r is not None


def _tabela_existe(pool, tabela):
    with pool.connection() as c:
        r = c.execute(
            "select 1 from information_schema.tables where table_name=%s", (tabela,)).fetchone()
    return r is not None


def test_aplica_so_pendentes_e_cria_colunas(pool):
    _baseline_ate_087(pool)
    # antes: colunas novas não existem
    assert not _col_existe(pool, "lancamentos", "forma_pagamento")
    assert not _col_existe(pool, "funcionarios", "vale_transporte")

    esperado = len(_pendentes_a_partir_de("088"))  # 088, 089, 090, ...
    n = aplicar_migracoes(pool)
    assert n == esperado  # aplicou exatamente as pendentes

    # depois: colunas/tabela das migrações sob teste foram criadas
    assert _col_existe(pool, "lancamentos", "forma_pagamento")   # 088
    assert _tabela_existe(pool, "parcelas_cartao")               # 088
    assert _col_existe(pool, "funcionarios", "vale_transporte")  # 089
    assert _col_existe(pool, "contas", "cnae")                   # 090


def test_idempotente_segunda_rodada_nao_aplica_nada(pool):
    _baseline_ate_087(pool)
    assert aplicar_migracoes(pool) == len(_pendentes_a_partir_de("088"))
    # rodar de novo (ex.: outro deploy/instância) não aplica nada e não quebra
    assert aplicar_migracoes(pool) == 0


def test_lock_nao_bloqueia_quando_ja_ocupado(pool):
    """Com o lock preso por outra sessão, _obter_lock NÃO bloqueia (evita o
    'canceling statement due to statement timeout' dos dois preDeploy do Render):
    tenta poucas vezes e desiste."""
    from db import aplicar_migracoes as am
    with pool.connection() as dono:
        dono.execute("select pg_advisory_lock(%s)", (am._LOCK_MIGRACOES,))
        dono.commit()
        try:
            with pool.connection() as outra:
                assert am._obter_lock(outra, tentativas=2, espera=0.05) is False
        finally:
            dono.execute("select pg_advisory_unlock(%s)", (am._LOCK_MIGRACOES,))
            dono.commit()


def test_segue_e_aplica_mesmo_sem_o_lock(pool, monkeypatch):
    """Se o lock não vier (outra instância migrando/lock vazado), as migrações
    ainda são aplicadas — são idempotentes e o Postgres serializa o DDL."""
    from db import aplicar_migracoes as am
    _baseline_ate_087(pool)
    monkeypatch.setattr(am, "_obter_lock", lambda *a, **k: False)
    n = am.aplicar_migracoes(pool)
    assert n == len(_pendentes_a_partir_de("088"))
    assert _col_existe(pool, "funcionarios", "demitido_em")   # 094 aplicou


# ------------------------------------- "não sei" não pode virar "não rodou"

def test_erro_na_verificacao_aborta_em_vez_de_reaplicar(pool):
    """O incidente de 17/ago: este SELECT falhou, o `except` respondeu "não rodou"
    e o runner REEXECUTOU três migrações registradas desde julho. Uma delas (127)
    derruba e recria um check constraint — e o pre-deploy morreu, deixando o
    serviço no código anterior.

    Reexecutar migração é destrutivo por natureza (drop/recreate, backfill que
    soma de novo). Diante de erro, a resposta certa é PARAR, não chutar."""
    from db.aplicar_migracoes import migracao_ja_rodou
    with pool.connection() as c:
        c.execute("alter table schema_migrations rename to schema_migrations_escondida")
        c.commit()
    try:
        with pytest.raises(Exception) as exc:
            migracao_ja_rodou(pool, "127_prospeccao_atividade_bounce.sql")
        assert "já rodou" in str(exc.value)      # a mensagem diz o que não se sabe
    finally:
        with pool.connection() as c:
            c.execute("alter table schema_migrations_escondida rename to schema_migrations")
            c.commit()


def test_responde_certo_quando_sabe(pool):
    """Contraprova: sem erro, segue respondendo o que o registro diz."""
    from db.aplicar_migracoes import migracao_ja_rodou
    with pool.connection() as c:
        c.execute("insert into schema_migrations (nome) values ('999_so_pro_teste.sql') "
                  "on conflict do nothing")
        c.commit()
    assert migracao_ja_rodou(pool, "999_so_pro_teste.sql") is True
    assert migracao_ja_rodou(pool, "998_nunca_existiu.sql") is False


def test_127_permite_engajamento():
    """A 127 derruba e recria o check, então precisa listar tudo que é válido hoje
    — inclusive o valor que a 169 introduziu depois dela. Esquecer isso é o que
    derrubou o deploy."""
    from pathlib import Path
    sql = (Path(__file__).resolve().parent.parent / "db" / "migracoes"
           / "127_prospeccao_atividade_bounce.sql").read_text("utf-8")
    assert "'engajamento'" in sql, "reexecutar a 127 vai quebrar em banco com engajamento"
    assert "'bounce'" in sql
