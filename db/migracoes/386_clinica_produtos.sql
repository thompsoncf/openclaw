-- 386_clinica_produtos.sql
-- Fase 7c da clínica: PRODUTO JUNTO DO ATENDIMENTO (docs/mockups/clinica_planos_pacotes_assinatura.html,
-- seção 06, "o dermocosmético que já está na prateleira"). finance/clinica_produtos.py,
-- tela /painel/clinica/produtos.
--
-- O produto e o estoque continuam os do Zaq (catalogo_produtos, estoque_mov — migração 032),
-- e a venda continua a do balcão (finance/pdv.py: baixa o estoque, lança a receita em
-- Vendas). O que a clínica ganha:
--   * DURAÇÃO do produto (ex.: protetor de 60 dias): na data prevista, o agente pergunta
--     se acabou e oferece reposição — sem dizer o nome do produto, no horário de
--     atendimento e contando no "1 mensagem automática por paciente por dia".
--   * LOTE COM VALIDADE: a entrada pela tela da clínica guarda a validade; o que vence
--     em até 60 dias vira sugestão de venda antes de virar perda.
--   * A VENDA LIGADA AO PACIENTE e ao atendimento (e o desconto do assinante, fase 7b).
--
-- Aditiva e idempotente.

alter table public.catalogo_produtos add column if not exists recompra_dias smallint
  check (recompra_dias is null or recompra_dias between 1 and 730);

create table if not exists public.clinica_produto_lotes (
  id bigserial primary key,
  conta_id bigint not null references public.contas(id),
  produto_id bigint not null references public.catalogo_produtos(id),
  quantidade numeric(12,3) not null check (quantidade > 0),
  validade date not null,
  criado_por bigint,
  criado_em timestamptz not null default now());
create index if not exists clinica_produto_lotes_conta on public.clinica_produto_lotes (conta_id, produto_id);

create table if not exists public.clinica_produto_vendas (
  id bigserial primary key,
  conta_id bigint not null references public.contas(id),
  produto_id bigint not null references public.catalogo_produtos(id),
  evento_id bigint,                        -- o atendimento em que vendeu (se foi no fim de um)
  prospeccao_id bigint references public.prospeccao(id) on delete set null,
  paciente_nome text not null,
  paciente_fone text not null default '',
  quantidade numeric(12,3) not null check (quantidade > 0),
  valor_centavos integer not null,         -- já com o desconto
  desconto_centavos integer not null default 0,
  lancamento_id bigint,                    -- a receita (ou o título, no fiado) do balcão
  recompra_em date,                        -- a data prevista da reposição (duração × quantidade)
  recompra_estado text not null default 'aguardando'
    check (recompra_estado in ('aguardando','lembrado','comprou','dispensado','sem')),
  criado_por bigint,
  criado_em timestamptz not null default now());
create index if not exists clinica_produto_vendas_conta on public.clinica_produto_vendas (conta_id, criado_em);
create index if not exists clinica_produto_vendas_recompra
  on public.clinica_produto_vendas (conta_id, recompra_em) where recompra_estado = 'aguardando';

-- o lembrete da recompra entra na mesma trava de "1 automática por dia" (clinica_lembretes, 381)
alter table public.clinica_lembretes drop constraint if exists clinica_lembretes_tipo_check;
alter table public.clinica_lembretes add constraint clinica_lembretes_tipo_check
  check (tipo in ('sessao','retorno','validade','recompra'));

alter table public.clinica_agenda_config
  add column if not exists produto_recompra text not null default 'ligado';
do $$ begin
  if not exists (select 1 from pg_constraint where conname = 'clinica_agenda_config_produto_recompra_check') then
    alter table public.clinica_agenda_config add constraint clinica_agenda_config_produto_recompra_check
      check (produto_recompra in ('ligado','off'));
  end if;
end $$;

-- rollback:
--   alter table public.clinica_agenda_config drop constraint if exists clinica_agenda_config_produto_recompra_check,
--     drop column if exists produto_recompra;
--   alter table public.clinica_lembretes drop constraint if exists clinica_lembretes_tipo_check;
--   alter table public.clinica_lembretes add constraint clinica_lembretes_tipo_check
--     check (tipo in ('sessao','retorno','validade'));   -- só depois de apagar os 'recompra'
--   drop table if exists public.clinica_produto_vendas; drop table if exists public.clinica_produto_lotes;
--   alter table public.catalogo_produtos drop column if exists recompra_dias;
