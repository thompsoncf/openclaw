-- 147_orcamento_evento.sql
-- MODO EVENTO no orçamento. O módulo nasceu (045/068/074) pra venda consultiva
-- RECORRENTE: item = nome+setup+mensal, totais = investimento inicial +
-- mensalidade + 1º ano. Num orçamento de evento não existe mensalidade — existe
-- data do evento, número de convidados, horário de início/encerramento,
-- quantidade × valor unitário e parcelas com vencimento. A empresa do nicho
-- 'eventos' (migração 132) não tinha onde botar nada disso e continuava
-- emitindo orçamento em outro sistema.
--
-- Mesma tabela, uma coluna `modo`: a conta do nicho eventos passa a gravar
-- 'evento' e ganha o layout/《fechar contrato》 dela; todo o resto continua
-- 'recorrente', byte por byte igual ao que está no ar. Aditivo e idempotente.
--
-- Espelhado em runtime por web/painel_servicos._garantir_tabela (o deploy não
-- roda migração sozinho).

alter table public.orcamentos add column if not exists modo     text not null default 'recorrente';
alter table public.orcamentos add column if not exists evento   jsonb;   -- {data, convidados, inicio, fim, tipo, contratos[], local}
alter table public.orcamentos add column if not exists parcelas jsonb;   -- [{venc, valor_centavos, forma, obs}]
alter table public.orcamentos add column if not exists numero   int;     -- nº sequencial POR CONTA (o cliente entende "orçamento nº 60")
alter table public.orcamentos add column if not exists endereco text;    -- endereço do cliente (o CEP/cidade/UF já existiam em parte)
alter table public.orcamentos add column if not exists cep      text;
-- compromisso criado na agenda quando o cliente aprovou (reserva da data).
-- Sem FK de propósito: o espelho de runtime cria esta coluna em bancos que
-- ainda não têm eventos_agenda, e apagar um compromisso não pode derrubar o
-- orçamento. Guarda o id; quem lê trata "sumiu" como "não tem".
alter table public.orcamentos add column if not exists evento_agenda_id bigint;

alter table public.orcamentos drop constraint if exists orcamentos_modo_check;
alter table public.orcamentos add constraint orcamentos_modo_check
    check (modo in ('recorrente','evento'));

-- numeração sequencial por conta: dois orçamentos da mesma empresa nunca saem
-- com o mesmo número (o insert calcula max+1 e reexecuta se colidir).
create unique index if not exists uq_orcamentos_conta_numero
    on public.orcamentos (conta_id, numero) where numero is not null;

-- backfill: quem já existe ganha o número na ordem em que foi criado, pra o
-- próximo max+1 continuar a série em vez de começar do 1 e colidir.
with seq as (
    select id, row_number() over (partition by conta_id order by id) as n
      from public.orcamentos
     where conta_id is not null
)
update public.orcamentos o set numero = seq.n
  from seq
 where seq.id = o.id and o.numero is null;

-- rollback:
--   alter table public.orcamentos drop constraint if exists orcamentos_modo_check;
--   drop index if exists uq_orcamentos_conta_numero;
--   alter table public.orcamentos drop column if exists modo, drop column if exists evento,
--     drop column if exists parcelas, drop column if exists numero,
--     drop column if exists endereco, drop column if exists cep,
--     drop column if exists evento_agenda_id;
