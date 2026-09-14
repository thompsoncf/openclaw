-- 259_agente_marca_visita.sql
-- A IA passa a poder combinar a visita ao espaço dentro da conversa.
--
-- POR QUE UMA CHAVE DE TRÊS ESTADOS, e não um boolean. Decisão do dono em
-- 14/09/2026, ao ver o mockup: "é bom colocar no sistema as 2 opções". Cada
-- empresa escolhe até onde deixa a IA ir, e muda de ideia sem ninguém tocar em
-- código. É o mesmo formato da régua do funil (finance/funil_regua.MODOS).
--
--   off     a IA nem oferece visita — responde e passa pro time, como hoje
--   propoe  combina dia e hora com o cliente e deixa PRONTA pra um toque; nada
--           entra na agenda sem uma pessoa confirmar
--   marca   marca direto, manda a confirmação com endereço e o .ics, e avisa
--
-- NASCE 'off' EM TODA CONTA, e isso é deliberado. Já existe um `pode_agendar`
-- boolean (migração 080) que está `true` nas duas contas com agente e que o
-- motor NUNCA leu — chave ligada que não liga nada. Semear `propoe` a partir
-- dele ligaria a função em quem nunca pediu, e a §0 do CLAUDE.md é clara: o que
-- é do cliente não se muda por conta própria. `pode_agendar` fica onde está,
-- intocado, pra não quebrar quem o lê; quem manda agora é `agendar_modo`.

alter table public.agente_config
  add column if not exists agendar_modo text not null default 'off';

do $$
begin
  if not exists (select 1 from pg_constraint where conname = 'agente_config_agendar_modo_ck') then
    alter table public.agente_config
      add constraint agente_config_agendar_modo_ck
      check (agendar_modo in ('off','propoe','marca'));
  end if;
end $$;

-- QUEM MARCOU A VISITA. Sem isto não dá pra responder, daqui a um mês, "das
-- visitas que a IA marcou, quantas o cliente compareceu?" — e virar a chave pra
-- 'marca' seria voto de fé sem placar. Nulo = marcada antes desta migração,
-- que é diferente de 'vendedor' (marcada à mão DEPOIS, sabendo-se disso).
alter table public.eventos_agenda
  add column if not exists marcado_por text;

-- A VISITA PROPOSTA, esperando o vendedor. Mora fora de `eventos_agenda` de
-- propósito: proposta não é compromisso, e escrever na agenda antes de um
-- humano confirmar é exatamente o que o modo 'propoe' existe pra evitar. Quem
-- confirma vira evento pelo mesmo caminho do Cockpit (`cockpit.agendar_visita`),
-- e o id do evento volta pra cá.
create table if not exists public.agente_visitas (
  id            bigserial primary key,
  conta_id      bigint not null references public.contas(id) on delete cascade,
  prospeccao_id bigint not null references public.prospeccao(id) on delete cascade,
  conversa_id   bigint,
  inicio        timestamptz not null,
  dur_min       int not null default 60,
  estado        text not null default 'proposta'
                check (estado in ('proposta','confirmada','descartada','expirada')),
  membro_id     bigint,          -- o vendedor a quem a proposta foi entregue
  evento_id     bigint,          -- preenchido ao confirmar
  criado_em     timestamptz not null default now(),
  decidido_em   timestamptz,
  decidido_por  bigint
);

-- UMA proposta viva por lead: a IA que voltar a combinar horário atualiza a que
-- existe em vez de encher a fila do vendedor com três cartões do mesmo cliente.
create unique index if not exists agente_visitas_uma_viva
  on public.agente_visitas (conta_id, prospeccao_id)
  where estado = 'proposta';

create index if not exists agente_visitas_por_membro
  on public.agente_visitas (conta_id, membro_id) where estado = 'proposta';

-- rollback:
--   drop table if exists public.agente_visitas;
--   alter table public.eventos_agenda drop column if exists marcado_por;
--   alter table public.agente_config drop constraint if exists agente_config_agendar_modo_ck;
--   alter table public.agente_config drop column if exists agendar_modo;
