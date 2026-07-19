-- 080_comunicacao_omnichannel.sql
-- Fundação do inbox omnichannel: conversas + mensagens (canal-agnóstico) e a
-- config/treino do Agente IA. Idempotente. Os canais (WhatsApp/Messenger/Instagram)
-- acendem depois; aqui só a estrutura + backfill do que já foi enviado.

create table if not exists public.conversas (
  id                     bigserial primary key,
  conta_id               bigint not null references public.contas(id) on delete cascade,
  prospeccao_id          bigint references public.prospeccao(id) on delete set null,
  canal                  text not null check (canal in ('email','whatsapp','messenger','instagram')),
  contato_ref            text,
  status                 text not null default 'aberta' check (status in ('aberta','pendente','resolvida')),
  agente_ativo           boolean not null default false,
  responsavel_membro_id  bigint references public.membros(id),
  janela_expira_em       timestamptz,
  ultima_msg_em          timestamptz not null default now(),
  criado_em              timestamptz not null default now()
);
create index if not exists idx_conversas_conta on public.conversas (conta_id, ultima_msg_em desc);
create unique index if not exists idx_conversas_lead_canal
  on public.conversas (conta_id, prospeccao_id, canal) where prospeccao_id is not null;

create table if not exists public.mensagens (
  id           bigserial primary key,
  conversa_id  bigint not null references public.conversas(id) on delete cascade,
  canal        text not null,
  direcao      text not null check (direcao in ('in','out')),
  autor        text not null default 'humano' check (autor in ('humano','bot','lead')),
  membro_id    bigint references public.membros(id),
  texto        text not null default '',
  meta         jsonb,
  provider_sid text,
  criado_em    timestamptz not null default now()
);
create index if not exists idx_mensagens_conversa on public.mensagens (conversa_id, criado_em);

create table if not exists public.agente_config (
  conta_id           bigint primary key references public.contas(id) on delete cascade,
  ativo              boolean not null default false,
  limiar_confianca   int not null default 80,
  horario            text not null default 'comercial',
  tom                text not null default 'informal',
  max_trocas         int not null default 4,
  escalar_para       text not null default 'dono_lead',
  pode_responder     boolean not null default true,
  pode_qualificar    boolean not null default true,
  pode_agendar       boolean not null default true,
  pode_orcamento     boolean not null default true,
  orcamento_proativo boolean not null default false,
  atualizado_em      timestamptz not null default now()
);

create table if not exists public.agente_conhecimento (
  id        bigserial primary key,
  conta_id  bigint not null references public.contas(id) on delete cascade,
  tipo      text not null default 'faq' check (tipo in ('instrucoes','faq')),
  pergunta  text,
  resposta  text not null default '',
  ordem     int not null default 0,
  criado_em timestamptz not null default now()
);
create index if not exists idx_conhecimento_conta on public.agente_conhecimento (conta_id, ordem, id);

-- backfill: transforma o histórico de e-mail/WhatsApp já registrado em conversas+mensagens
insert into public.conversas (conta_id, prospeccao_id, canal, status, ultima_msg_em, criado_em)
select p.conta_id, a.prospeccao_id, a.tipo, 'aberta', max(a.criado_em), min(a.criado_em)
  from public.prospeccao_atividades a
  join public.prospeccao p on p.id = a.prospeccao_id
 where a.tipo in ('email','whatsapp')
   and not exists (select 1 from public.conversas c
                    where c.prospeccao_id = a.prospeccao_id and c.canal = a.tipo)
 group by p.conta_id, a.prospeccao_id, a.tipo;

insert into public.mensagens (conversa_id, canal, direcao, autor, membro_id, texto, criado_em)
select c.id, a.tipo, 'out', 'humano', a.membro_id, a.descricao, a.criado_em
  from public.prospeccao_atividades a
  join public.conversas c on c.prospeccao_id = a.prospeccao_id and c.canal = a.tipo
 where a.tipo in ('email','whatsapp')
   and not exists (select 1 from public.mensagens m
                    where m.conversa_id = c.id and m.criado_em = a.criado_em and m.texto = a.descricao);
