-- 388_regra_por_chip.sql
-- A REGRA POR NÚMERO (26/09/2026, mockup docs/mockups/vendedor_ia_chip.html).
--
-- Numa empresa de dois chips, todo lead novo caía no mesmo rodízio, venha por qual
-- número vier. O dono da Prime (conta 34) pôs um tráfego pago novo apontando só pro
-- chip CP Thiago (conta 36) e quer que TODO contato novo desse número vá para um
-- vendedor só — o "zaq teste", atendido pela IA — pra medir a IA contra a equipe.
--
-- Uma linha por chip: quem recebe o lead novo que entra por ele, se a IA atende as
-- conversas desse dono, em que horário, como ela se apresenta, e quem é avisado
-- quando ela precisa de gente (agenda → quem cuida do espaço; desconto e sinal → o
-- dono). Sem linha, o chip segue no rodízio de sempre.
--
-- NÃO TOCA EM `canais_config` nem em nada da conexão (CLAUDE.md §1): o chip é só a
-- chave de leitura. `chip_id` é o id da conta-chip (a mesma da `contas`), e o chip
-- principal é a própria empresa — igual ao `_resolver_chip` do webhook.
--
-- Aditiva e idempotente.

create table if not exists public.chip_regra (
  id                      bigserial primary key,
  conta_id                bigint not null references public.contas(id) on delete restrict,
  chip_id                 bigint not null,
  ativa                   boolean not null default true,
  -- quem recebe o lead NOVO que entra por este número (em vez do rodízio)
  membro_id               bigint references public.membros(id) on delete set null,
  -- a IA atende as conversas que caem com esse dono
  ia_ligada               boolean not null default false,
  ia_horario              text not null default '24h',
  -- dias da semana no padrão do Python (0 = segunda … 6 = domingo)
  ia_dias                 smallint[] not null default '{0,1,2,3,4,5}',
  ia_hora_ini             smallint not null default 8,
  ia_hora_fim             smallint not null default 22,
  ia_fora_texto           text,
  -- "Sou a assistente Zaq, da Prime Eventos"
  ia_apresentacao         text,
  -- quem a IA avisa: a agenda (visita, choque de horário, pedido de pessoa) e o dono
  -- (desconto, negociação, sinal)
  aviso_agenda_membro_id  bigint references public.membros(id) on delete set null,
  aviso_dono_membro_id    bigint references public.membros(id) on delete set null,
  -- só contato novo DEPOIS disto entra na regra; o que já existia fica com quem tem
  vale_desde              timestamptz not null default now(),
  criado_em               timestamptz not null default now(),
  atualizado_em           timestamptz not null default now(),
  constraint chip_regra_horario_check check (ia_horario in ('24h', 'proprio')),
  constraint chip_regra_horas_check check (ia_hora_ini between 0 and 23
                                           and ia_hora_fim between 1 and 24
                                           and ia_hora_ini < ia_hora_fim),
  constraint chip_regra_um_por_chip unique (conta_id, chip_id)
);

-- O pedido de ajuda da IA, pra não virar "vou passar pra equipe" sem ninguém saber
-- (o que aconteceu nos testes de 14/09). Uma linha por aviso: quem, por quê, e se
-- saiu. É também o que o painel do desafio conta ("por que a IA avisou gente").
create table if not exists public.ia_avisos (
  id              bigserial primary key,
  conta_id        bigint not null references public.contas(id) on delete restrict,
  conversa_id     bigint,
  prospeccao_id   bigint,
  membro_id       bigint,
  motivo          text not null,
  resumo          text,
  criado_em       timestamptz not null default now()
);
create index if not exists ia_avisos_conta_idx on public.ia_avisos (conta_id, criado_em desc);

-- Os leads que a REGRA deu ao dono dela, um por linha. É o que diz "esta conversa é
-- da IA": o dono da regra pode ter leads antigos no mesmo chip (ou ganhar um que o
-- gestor moveu pra ele), e esses seguem o atendimento de sempre. Tabela à parte, e
-- não coluna em `conversas`, pra não pedir trava em tabela viva.
create table if not exists public.chip_regra_leads (
  prospeccao_id   bigint primary key,
  conta_id        bigint not null references public.contas(id) on delete restrict,
  chip_id         bigint not null,
  membro_id       bigint,
  criado_em       timestamptz not null default now()
);

-- rollback:
--   drop table if exists public.chip_regra_leads;
--   drop table if exists public.ia_avisos;
--   drop table if exists public.chip_regra;
