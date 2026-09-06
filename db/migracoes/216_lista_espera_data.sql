-- 216_lista_espera_data.sql
-- A lista de espera por data (docs/mockups/lista_de_espera_por_data.html,
-- aprovado em 06/09/2026, medido na Prime Eventos):
--
-- Em 06/09 a Prime tinha 25 leads em jogo pedindo datas que ela JÁ vendeu — 18
-- datas, e nenhum dos 25 tinha recebido proposta. E 3 leads esperavam por
-- 16/01, um sábado que abriu por cancelamento em 02/09 sem ninguém avisar.
-- Faltavam duas coisas no banco:
--
--   contas.festas_por_dia   quantas festas a empresa faz no MESMO dia. É o
--                           número que decide quando a data está "tomada".
--                           NULO = a conta não usa lista de espera (é o padrão,
--                           e é o que mantém a Doce Mell de fora nesta rodada,
--                           por decisão do dono). A Prime nasce com 1: em 29
--                           dias de agenda nunca teve duas festas no mesmo dia.
--
--   lista_espera_data       quem espera por qual data. Uma linha por (lead,
--                           data): o mesmo cliente pode esperar por duas datas,
--                           e a mesma data tem vários esperando. `saiu_em`
--                           preenchido é histórico — a linha NUNCA é apagada,
--                           porque "quantos desistiram desta data" é a pergunta
--                           que decide a tabela de sábado.
--
-- Aditiva e idempotente. Só leitura de agenda: nada aqui muda festa, sessão ou
-- canal (regra 0 e 1 do CLAUDE.md).

alter table public.contas add column if not exists festas_por_dia int;

alter table public.contas drop constraint if exists contas_festas_por_dia_check;
alter table public.contas add constraint contas_festas_por_dia_check
    check (festas_por_dia is null or festas_por_dia between 1 and 20);

create table if not exists public.lista_espera_data (
    id            bigserial primary key,
    conta_id      bigint not null references public.contas(id) on delete cascade,
    prospeccao_id bigint not null references public.prospeccao(id) on delete cascade,
    -- a data que o cliente PEDIU, do lead (prospeccao.evento_em). Guardada aqui
    -- porque o lead pode mudar de data: a linha antiga vira histórico ("pediu
    -- 10/10, aceitou 07/11"), e não some junto.
    data          date   not null,
    entrou_em     timestamptz not null default now(),
    -- por que saiu: 'fechou' (virou ganho), 'mudou_data', 'desistiu' (perdido ou
    -- o vendedor tirou), 'atendido' (a data abriu e o cliente ficou com ela).
    saiu_em       timestamptz,
    saiu_motivo   text,
    -- quando o vendedor foi avisado de que esta data abriu. Nulo = ainda não.
    -- É a trava do aviso: com dois workers no ticker, quem consegue gravar avisa.
    avisado_em    timestamptz,
    unique (prospeccao_id, data)
);

alter table public.lista_espera_data drop constraint if exists lista_espera_saiu_motivo_check;
alter table public.lista_espera_data add constraint lista_espera_saiu_motivo_check
    check (saiu_motivo is null or saiu_motivo in ('fechou', 'mudou_data', 'desistiu', 'atendido'));

-- quem espera por uma data, na conta: é a consulta da tela e a do aviso
create index if not exists idx_lista_espera_conta_data
    on public.lista_espera_data (conta_id, data) where saiu_em is null;

-- rollback:
--   drop table if exists public.lista_espera_data;
--   alter table public.contas drop column if exists festas_por_dia;
