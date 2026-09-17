-- 274_resumo_semanal.sql
-- O resumo semanal por e-mail: o interruptor, os e-mails de gestor e quando vai.
--
-- O PEDIDO (dono, 17/09/2026): "vamos criar o email pro gestor, semana de
-- acompanhamento da sua empresa". E, sobre onde configurar: "coloca lá na
-- engrenagem da prospecção, em Agentes IA, a automação pra ativação do botão pra
-- esse relatório, e os e-mails pra cadastrar um ou mais gestores".
--
-- Mockup aprovado: docs/mockups/email_semanal_do_gestor.html
--
-- NASCE DESLIGADO. Um e-mail semanal que começa ligado sozinho é spam com o nosso
-- nome no remetente — e a conta que não quiser nunca pediu nada. O dono liga.
--
-- OS E-MAILS DE GESTOR SÃO TEXTO, e não uma lista de membros, porque quem
-- acompanha a operação nem sempre tem login: a Prime não tem NINGUÉM com papel de
-- gestor no sistema (só o dono e três vendedores), e o sócio ou o contador que
-- recebe o resumo não precisa de usuário pra isso.
--
-- E O E-MAIL CADASTRADO AQUI NÃO É ACESSO. Ele recebe o resumo e nada mais: não
-- entra no painel, não vê lead, não vê conversa. É lista de envio, e está escrito
-- na própria tela pra não haver dúvida.
--
-- `resumo_semanal_vendedor`: cada vendedor recebe a parte DELE — só a carteira
-- dele, sem comparação com colega. Decisão do dono na mesma conversa. Fica
-- separado do interruptor principal porque são dois públicos diferentes, e desligar
-- um não tem por que desligar o outro.
--
-- Aditivo e idempotente. Colunas em `contas` pelo mesmo caminho de
-- `avisos_na_fila` (migração 266) — é configuração de conta, não entidade nova.

alter table public.contas
  add column if not exists resumo_semanal boolean not null default false,
  add column if not exists resumo_semanal_emails text not null default '',
  add column if not exists resumo_semanal_vendedor boolean not null default true,
  add column if not exists resumo_semanal_dia text not null default 'segunda';

-- O DIA é 'segunda' ou 'sexta' (escolha do dono: segunda, 9h). O check existe pra
-- o cron não precisar adivinhar o que fazer com um valor que ele não conhece.
do $$
begin
  if not exists (select 1 from pg_constraint where conname = 'contas_resumo_dia_check') then
    alter table public.contas
      add constraint contas_resumo_dia_check check (resumo_semanal_dia in ('segunda','sexta'));
  end if;
end $$;

-- O REGISTRO DE ENVIO. Sem ele, o cron que rodasse duas vezes na segunda (deploy no
-- meio, retry do Render) mandaria o mesmo resumo duas vezes — e a chave primária é
-- o que impede isso, não um `if` no código.
--
-- Guarda também o que NÃO foi enviado e por quê (`motivo`): "semana sem movimento"
-- é resposta legítima e é o que explica a caixa de entrada vazia numa segunda.
create table if not exists public.resumo_semanal_envio (
  conta_id    bigint not null,
  semana      text   not null,          -- '2026-W38', hora de Brasília
  destino     text   not null,
  tipo        text   not null,          -- dono | gestor | vendedor
  enviado_em  timestamptz not null default now(),
  ok          boolean not null default true,
  motivo      text,
  primary key (conta_id, semana, destino)
);

create index if not exists idx_resumo_envio_conta
    on public.resumo_semanal_envio (conta_id, enviado_em desc);

-- rollback:
--   drop table if exists public.resumo_semanal_envio;
--   alter table public.contas drop column if exists resumo_semanal,
--     drop column if exists resumo_semanal_emails,
--     drop column if exists resumo_semanal_vendedor,
--     drop column if exists resumo_semanal_dia;
