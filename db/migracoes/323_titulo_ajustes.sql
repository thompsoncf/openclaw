-- 323_titulo_ajustes.sql
-- O HISTÓRICO de quando o valor recebido não fecha com a parcela — pedido 9 do
-- dono (23/09/2026: "ajuste da parcela do valor do sinal for outro valor que não
-- o mesmo que foi gerado").
--
-- A REGRA, nas palavras dele, no mesmo dia:
--   * entrou A MENOS -> "continua devendo": nasce uma conta nova só com o que
--     falta, com o mesmo vencimento;
--   * entrou A MAIS  -> "fica de crédito e abate de outra parcela";
--   * nos dois casos "sempre pergunta pro gestor" — o sistema nunca decide.
--
-- POR QUE UMA TABELA. Os dois caminhos MUDAM valor de conta: a parcela paga
-- passa a valer o que entrou, e a parcela que recebe o crédito diminui. Sem
-- registro, "por que a parcela de outubro é R$ 1.067 e não R$ 1.142?" não teria
-- resposta — e o valor combinado de antes estaria perdido (regra 0). Cada linha
-- guarda o antes e o depois de UMA conta mexida, e de qual recebimento veio.
--
-- O caso que motivou: orçamento nº 23 da Prime, sinal combinado de R$ 2.340,00,
-- conta editada à mão pra R$ 2.415,00 e baixada em 23/09 — R$ 75,00 a mais que
-- não abateram nada.
--
-- Aditiva e idempotente.

create table if not exists public.titulo_ajustes (
    id               bigserial primary key,
    conta_id         bigint not null references public.contas(id) on delete restrict,
    titulo_id        bigint references public.titulos(id) on delete set null,
    origem_titulo_id bigint references public.titulos(id) on delete set null,
    tipo             text   not null check (tipo in ('recebido', 'restante',
                                                     'abatimento', 'quitada_credito')),
    valor_antes      bigint not null,
    valor_depois     bigint not null,
    criado_em        timestamptz not null default now(),
    criado_por       bigint references public.membros(id) on delete set null
);

create index if not exists idx_titulo_ajustes_conta
    on public.titulo_ajustes (conta_id, titulo_id);
create index if not exists idx_titulo_ajustes_origem
    on public.titulo_ajustes (origem_titulo_id);

comment on table public.titulo_ajustes is
  'Quando o recebido não fecha com a parcela: o antes e o depois de cada conta '
  'mexida (a paga, a do restante, a que recebeu o crédito). Ver a migração 323.';

-- rollback:
--   drop table if exists public.titulo_ajustes;
