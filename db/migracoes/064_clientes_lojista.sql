-- 064_clientes_lojista.sql
-- Base de clientes PROPRIA do lojista (o ativo de pos-venda/recompra).
-- IMPORTANTE: e' SEPARADA de contas ZAQ. Um cliente aqui NAO precisa ter conta
-- no ZAQ — e' a agenda do lojista, gente que comprou no balcao. Se por acaso o
-- cliente TAMBEM tiver conta ZAQ, da' pra vincular (conta_zaq_id), mas e' opcional.
-- Idempotente.

create table if not exists clientes (
    id            bigserial primary key,
    dono_id       bigint not null references contas(id),  -- o lojista dono da base
    nome          text not null,
    telefone      text,                                   -- WhatsApp/telefone (pos-venda)
    email         text,
    aniversario   date,                                   -- pra mensagem/desconto
    conta_zaq_id  bigint references contas(id),           -- opcional: se tb e' cliente ZAQ
    obs           text,                                   -- anotacao livre do lojista
    ativo         boolean not null default true,          -- soft delete
    criado_em     timestamptz not null default now(),
    atualizado_em timestamptz not null default now()
);

create index if not exists ix_clientes_dono on clientes (dono_id) where ativo;
create index if not exists ix_clientes_dono_nome on clientes (dono_id, nome);
create index if not exists ix_clientes_dono_tel  on clientes (dono_id, telefone)
    where telefone is not null;

-- rollback:
--   drop table if exists clientes;
