-- 365_obras_lembrete_segunda.sql
-- A trava do lembrete de segunda das obras (finance/obras_lembrete.py): uma linha
-- por pessoa e semana ISO. É gravada ANTES de mandar — com 2 workers no web, só um
-- consegue inserir, e o envio que falha não se repete a cada 2 minutos.
--
-- `canal` diz por onde saiu (whatsapp, whatsapp_template, telegram, email) ou
-- 'nenhum' — é a primeira coisa a olhar quando o dono disser que não recebeu.
--
-- Tabela própria, e não `lembretes_enviados`: aquela tem um check de `tipo` que a
-- agenda e a corretora já redefinem, e mais um redefinidor é mais um jeito de a
-- ordem das migrações quebrar o deploy.
--
-- Aditiva e idempotente.

create table if not exists public.obras_lembretes (
  conta_id    bigint not null references public.contas(id) on delete cascade,
  membro_id   bigint not null,
  semana      text   not null,                 -- 'AAAA-Www' (ISO)
  canal       text,
  itens       int,
  enviado_em  timestamptz not null default now(),
  primary key (conta_id, membro_id, semana)
);

-- rollback:
--   drop table if exists public.obras_lembretes;
