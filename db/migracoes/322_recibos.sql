-- 322_recibos.sql
-- O RECIBO de dinheiro que entrou — pedido 1 do dono (23/09/2026: "recibo do
-- valor do sinal de pagamentos e da parcela"; "quando der baixa abre um botão
-- pra nascer o recibo").
--
-- UM RECIBO POR CONTA A RECEBER. Gerar de novo o recibo da mesma parcela NÃO
-- cria outro número: atualiza o texto (quem completou o CPF no cadastro gera de
-- novo e o papel sai com ele) e mantém o número e o link. Dois recibos do mesmo
-- dinheiro seriam dois papéis dizendo que o cliente pagou duas vezes.
--
-- NÚMERO POR EMPRESA E POR ANO (0001/2026), dado sob trava em `finance/recibo.py`
-- — a unique abaixo é a última palavra se duas pessoas gerarem ao mesmo tempo.
--
-- O TEXTO FICA CONGELADO em `dados`: o recibo é um documento entregue ao
-- cliente, e ele não pode mudar sozinho porque alguém editou o orçamento depois.
-- Só o "gerar de novo" reescreve.
--
-- `titulo_id` vira nulo (e não some o recibo) se a conta for apagada: o papel já
-- está na mão do cliente, e o registro de que ele foi emitido é dado (regra 0).
--
-- Aditiva e idempotente.

create table if not exists public.recibos (
    id            bigserial primary key,
    conta_id      bigint not null references public.contas(id) on delete restrict,
    titulo_id     bigint references public.titulos(id) on delete set null,
    ano           int    not null,
    numero        int    not null check (numero > 0),
    token         text   not null unique,
    dados         jsonb  not null,
    emitido_em    timestamptz not null default now(),
    atualizado_em timestamptz not null default now(),
    emitido_por   bigint references public.membros(id) on delete set null,
    enviado_em    timestamptz,
    unique (conta_id, ano, numero)
);

create unique index if not exists uq_recibos_titulo
    on public.recibos (titulo_id) where titulo_id is not null;

comment on table public.recibos is
  'Recibo de conta a receber: um por título, número por empresa e ano, texto '
  'congelado em dados. Link público /recibo/<token>. Ver a migração 322.';

-- rollback:
--   drop table if exists public.recibos;
