-- 320_saldo_bancario_informado.sql
-- O SALDO DO BANCO, informado pelo dono — etapa A do pedido 3 (Open Finance).
--
-- POR QUE. Pedido do dono em 23/09/2026: "open finance — saldo da conta para
-- planejamento de contas a pagar". Open Finance de verdade é um contrato
-- (agregador autorizado, mensalidade, consentimento renovado a cada 12 meses), e
-- a decisão de custo é dele. A etapa A entrega o planejamento SEM fornecedor: ele
-- digita o saldo de cada banco (Sicoob e Banco do Nordeste, resposta dele no
-- mesmo dia) e a aba Empresa responde "tenho X, devo Y até o fim da semana,
-- sobra/falta Z".
--
-- HISTÓRICO, NUNCA SOBRESCRITA. Cada informe é uma linha nova; o saldo atual de
-- um banco é a linha mais recente dele. Saldo é dado do cliente (regra 0), e a
-- sequência dos informes é o que um dia permite conferir o livro-caixa contra o
-- banco. Tirar um banco da conta também é uma linha — `arquivado` —, e não um
-- delete: um nome digitado errado sai da soma sem apagar nada.
--
-- `origem` fica pra etapa B: quando um agregador existir, o saldo dele entra na
-- MESMA tabela com outra origem, e a tela não muda.
--
-- `valor_centavos` aceita negativo: cheque especial é saldo de verdade, e
-- escondê-lo faria a faixa prometer dinheiro que não existe.
--
-- Aditiva e idempotente.

create table if not exists public.saldo_bancario_informado (
    id              bigserial primary key,
    conta_id        bigint not null references public.contas(id) on delete restrict,
    banco           text   not null check (length(btrim(banco)) between 1 and 60),
    valor_centavos  bigint not null,
    arquivado       boolean not null default false,
    origem          text   not null default 'manual',
    informado_em    timestamptz not null default now(),
    informado_por   bigint references public.membros(id) on delete set null
);

create index if not exists idx_saldo_informado_conta
    on public.saldo_bancario_informado (conta_id, lower(btrim(banco)), informado_em desc);

comment on table public.saldo_bancario_informado is
  'Saldo de cada banco, informado pelo dono (etapa A do Open Finance). Histórico: '
  'cada informe é uma linha; o atual é a mais recente por banco. Ver a migração 320.';

-- rollback:
--   drop table if exists public.saldo_bancario_informado;
