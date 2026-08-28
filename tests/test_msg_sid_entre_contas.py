"""O mesmo id de mensagem em duas contas — quem recebe não pode perder a mensagem.

`provider_sid` é o id que o provedor dá à mensagem, e no WhatsApp ele é o MESMO
nas duas pontas: a mensagem que sai do celular A para o número B carrega um id só,
e as duas contas veem esse id. O índice era único GLOBAL, e os inserts usam
`on conflict (provider_sid) do nothing` pra não duplicar quando o Zaq envia e o eco
do Baileys volta.

Com duas contas do MESMO Zaq conversando entre si, isso virava perda silenciosa:

    1. a conta que enviou grava o eco (out) com o sid X;
    2. a conta que recebeu chega com o mesmo sid X;
    3. o `do nothing` descarta, e a caixa de quem recebeu fica vazia.

Aconteceu em produção em 15/08/2026, e é justamente o caminho de TESTE do dono —
ele manda do próprio celular (pareado numa conta) pro número do cliente (outra
conta). Três mensagens às 23:17:23/28/35: o log do serviço registrou
`messages.upsert recebido` e `entrada repassada ao webhook ✓` nos mesmos segundos,
e nenhuma apareceu na caixa de quem recebeu.

Aqui o schema é montado SEM índice nenhum e a migração 159 é aplicada de verdade —
senão o teste passaria por não ter a trava que ele existe pra exercitar.
"""
import os
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from web import painel_prospeccao as pp

BASE = Path(__file__).resolve().parents[1] / "db" / "migracoes"

CONTA_A = 23        # quem envia (o celular do dono, pareado nesta conta)
CONTA_B = 34        # quem recebe (a conta do cliente)
NUM_A = "558681885930"
NUM_B = "558694095516"
SID = "2A996A40A98D0A57B281"     # o id que as duas pontas veem

_SQL = """
create table prospeccao (id bigserial primary key, conta_id bigint, vendedor_id bigint,
  empresa text, contato text, tipo text default 'pj',
  telefone text, whatsapp text, email text, origem text,
  status text default 'novo', temperatura text default 'frio', estagio text default 'base',
  atualizado_em timestamptz default now(), criado_em timestamptz default now());
create table conversas (id bigserial primary key, conta_id bigint, prospeccao_id bigint,
  canal text default 'whatsapp', contato_ref text, contato_nome text,
  status text default 'aberta', agente_ativo boolean default true,
  janela_expira_em timestamptz,
  ultima_msg_em timestamptz default now(), criado_em timestamptz default now(), chip_id bigint);
create table mensagens (id bigserial primary key, conversa_id bigint, canal text,
  direcao text, autor text, texto text, membro_id bigint, provider_sid text,
  status text, criado_em timestamptz default now(),
  midia_ref jsonb, midia_tipo text, midia_meta jsonb);
create table wa_contatos (conta_id bigint, numero8 text, nome text,
  da_agenda boolean default false, primary key (conta_id, numero8));
create table membros (id bigserial primary key, conta_id bigint, nome text, email text,
  papel text, ativo boolean default true, cockpit_pausado boolean default false);
"""


@pytest.fixture()
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_sid_entre_contas_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=3, open=True, kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_SQL)
        # a migração de verdade: é ela que define a regra que este teste exercita
        c.execute((BASE / "159_mensagens_sid_por_conversa.sql").read_text(encoding="utf-8"))
        c.commit()
    yield p
    p.close()


def _msgs(c, conta_id):
    return c.execute(
        """select m.direcao, m.texto, m.provider_sid from mensagens m
             join conversas cv on cv.id = m.conversa_id
            where cv.conta_id=%s order by m.id""", (conta_id,)).fetchall()


# ------------------------------------------------- o caso que aconteceu de verdade

def test_mensagem_entre_duas_contas_do_zaq_aparece_nas_duas(pool):
    """O caminho exato do chamado: o celular do dono está pareado na conta A, e ele
    manda pro número da conta B. A saída da A e a entrada da B carregam o MESMO sid."""
    with pool.connection() as c:
        # A registra o eco do que saiu do celular dela
        pp._wa_saida_conversa(c, CONTA_A, NUM_B, "Oi", SID)
        # B recebe a mesma mensagem, com o mesmo id
        pp._wa_inbound_conversa(c, CONTA_B, NUM_A, "Oi", SID, "Dono", False)
        c.commit()
        assert _msgs(c, CONTA_A) == [("out", "Oi", SID)]
        assert _msgs(c, CONTA_B) == [("in", "Oi", SID)]     # antes: vazio


def test_ordem_inversa_da_o_mesmo_resultado(pool):
    """Quem chega primeiro depende de rede — as duas ordens têm que terminar igual."""
    with pool.connection() as c:
        pp._wa_inbound_conversa(c, CONTA_B, NUM_A, "Oi", SID, "Dono", False)
        pp._wa_saida_conversa(c, CONTA_A, NUM_B, "Oi", SID)
        c.commit()
        assert len(_msgs(c, CONTA_A)) == 1
        assert len(_msgs(c, CONTA_B)) == 1


def test_historico_de_outra_conta_tambem_entra(pool):
    """A importação do pareamento traz mensagens antigas com os mesmos ids."""
    with pool.connection() as c:
        pp._wa_saida_conversa(c, CONTA_A, NUM_B, "Oi", SID)
        pp._wa_historico_conversa(c, CONTA_B, NUM_A, "Oi", SID, None)
        c.commit()
        assert len(_msgs(c, CONTA_B)) == 1


# ------------------------------------------- e o dedup que o índice existe pra dar

def test_o_eco_da_propria_mensagem_continua_sem_duplicar(pool):
    """A razão de ser do `do nothing`: o Zaq envia e grava; o Baileys ecoa a MESMA
    mensagem logo depois, na MESMA conversa. Isso não pode virar duas linhas."""
    with pool.connection() as c:
        pp._wa_saida_conversa(c, CONTA_A, NUM_B, "Oi", SID)
        pp._wa_saida_conversa(c, CONTA_A, NUM_B, "Oi", SID)
        c.commit()
        assert _msgs(c, CONTA_A) == [("out", "Oi", SID)]


def test_entrada_repetida_na_mesma_conta_tambem_nao_duplica(pool):
    """Reentrega do webhook (o provedor remanda quando não recebe 200 a tempo)."""
    with pool.connection() as c:
        pp._wa_inbound_conversa(c, CONTA_B, NUM_A, "Oi", SID, "Dono", False)
        pp._wa_inbound_conversa(c, CONTA_B, NUM_A, "Oi", SID, "Dono", False)
        c.commit()
        assert _msgs(c, CONTA_B) == [("in", "Oi", SID)]


def test_indice_novo_e_o_que_manda(pool):
    """A trava mudou de lugar, não sumiu: o par (conversa, sid) segue único, e o sid
    sozinho não é mais. Se alguém recriar o índice global, os testes acima quebram —
    este aqui explica por quê."""
    with pool.connection() as c:
        idx = c.execute(
            "select indexdef from pg_indexes where tablename='mensagens' "
            "and indexname='idx_mensagens_sid_conversa'").fetchone()
        assert idx and "conversa_id" in idx[0] and "provider_sid" in idx[0]
        antigo = c.execute(
            "select 1 from pg_indexes where indexname='idx_mensagens_provider_sid'").fetchone()
        assert antigo is None
