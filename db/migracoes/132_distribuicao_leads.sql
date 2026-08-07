-- 132_distribuicao_leads.sql
-- Distribuição automática de leads por rodízio (fila) entre os vendedores.
-- Nível EMPRESA (uma fila por conta): todo lead novo que chega SEM dono (contato novo
-- no chip da empresa, resposta de campanha, tráfego pago) é atribuído ao PRÓXIMO
-- vendedor da fila. O agente IA segue dando o 1º toque; o vendedor é avisado, observa e
-- assume quando quiser. Ver finance/distribuicao.py.

create table if not exists public.distribuicao (
  conta_id  bigint primary key references public.contas(id) on delete cascade,
  ativo     boolean not null default false,
  ponteiro  int     not null default 0,      -- de quem é a próxima vez (índice na fila)
  avisar    boolean not null default true,   -- notifica o vendedor quando cai um lead
  atualizado_em timestamptz not null default now()
);

create table if not exists public.distribuicao_fila (
  conta_id   bigint not null references public.contas(id) on delete cascade,
  membro_id  bigint not null references public.membros(id) on delete cascade,
  ordem      int    not null default 0,
  primary key (conta_id, membro_id)
);
create index if not exists idx_distribuicao_fila_ordem
  on public.distribuicao_fila (conta_id, ordem);

-- número de WhatsApp do vendedor (o cadastro da equipe não tinha telefone) — pra onde
-- vai o aviso "caiu um lead pra você". O aviso por e-mail usa o e-mail que já existe.
alter table public.membros add column if not exists whatsapp text;
