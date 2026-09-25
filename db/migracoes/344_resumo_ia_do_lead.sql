-- 344_resumo_ia_do_lead.sql
-- O resumo da conversa e a sugestão da IA, guardados por lead.
--
-- O PEDIDO (dono, 25/09/2026, mockup docs/mockups/funil_resumo_ia.html aprovado
-- "assim"): um item no ⋯ do card do funil e um ✨ no app do vendedor que leem a
-- conversa do lead e devolvem o que o cliente quer, em que pé está, o que pode
-- travar, o próximo passo e uma mensagem pronta pro vendedor revisar.
--
-- POR QUE GUARDAR. A IA só roda no clique, e o resumo vale até chegar mensagem
-- nova: abrir de novo não gasta. A chave de "ainda vale" é `ultima_msg_id` — o
-- maior `mensagens.id` das conversas do lead no momento da leitura. Chegou
-- mensagem com id maior, a tela avisa "chegou mensagem nova · Atualizar".
--
-- O QUE NÃO ENTRA: texto de mensagem. `resumo` guarda só o que a IA escreveu
-- (e que a tela mostra); a conversa continua morando em `mensagens`.
--
-- `voto` e `usado_em` medem se ajuda: 👍/👎 e "Usar na conversa".
--
-- FKs: `contas` com restrict (027: nada que aponta pra contas tem cascade);
-- `prospeccao` com CASCADE — excluir o lead leva o resumo junto, e sem isso a
-- exclusão de lead daria erro. Membro sem FK, como a 301.
--
-- Aditiva e idempotente.

create table if not exists public.lead_resumo_ia (
  id             bigserial primary key,
  conta_id       bigint not null references public.contas(id) on delete restrict,
  prospeccao_id  bigint not null references public.prospeccao(id) on delete cascade,
  membro_id      bigint,
  ultima_msg_id  bigint not null default 0,
  -- muda quando o TEXTO de uma mensagem muda sem id novo (a transcrição do áudio
  -- chega segundos depois, na mesma linha) ou quando mensagem some (retenção)
  assinatura     text not null default '',
  n_lidas        integer not null default 0,
  n_total        integer not null default 0,
  resumo         jsonb not null,
  modelo         text,
  voto           smallint check (voto in (-1, 1)),
  votado_em      timestamptz,
  usado_em       timestamptz,
  criado_em      timestamptz not null default now()
);

-- o último resumo de um lead (a leitura da janela)
create index if not exists idx_lead_resumo_ia_lead
  on public.lead_resumo_ia (conta_id, prospeccao_id, id desc);
-- o teto técnico por conta por dia
create index if not exists idx_lead_resumo_ia_dia
  on public.lead_resumo_ia (conta_id, criado_em);

-- O TETO TÉCNICO por conta por dia, contado por TENTATIVA (a chamada que falha
-- também custa) e num insert-or-update só — dois cliques juntos não passam os
-- dois. Não é cota: é trava contra laço.
create table if not exists public.lead_resumo_ia_uso (
  conta_id    bigint not null references public.contas(id) on delete restrict,
  dia         date not null,
  tentativas  integer not null default 0,
  primary key (conta_id, dia)
);

comment on table public.lead_resumo_ia is
  'Resumo da conversa e sugestão da IA por lead, guardado até chegar mensagem nova. Ver a migração 344.';

comment on table public.lead_resumo_ia_uso is
  'Tentativas de resumo por IA por conta e dia (teto contra laço). Ver a migração 344.';

-- rollback:
--   drop table if exists public.lead_resumo_ia_uso;
--   drop table if exists public.lead_resumo_ia;
