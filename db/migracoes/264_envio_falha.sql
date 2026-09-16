-- 264_envio_falha.sql
-- O que o vendedor escreveu e NÃO saiu.
--
-- 15/09/2026, 19:33. O Thiago respondeu a Sheila pelo app. A mensagem não chegou
-- nela, e a tela ficou em "enviando…" pra sempre. Fui procurar o que houve e não
-- havia NADA:
--
--   * nenhuma linha em `mensagens` (só se grava DEPOIS do envio dar certo);
--   * nenhuma linha em `wa_qr_log` (o serviço Node loga `enviar: tentativa` antes
--     de qualquer coisa, então a requisição nem chegou nele);
--   * nenhuma linha em lugar nenhum.
--
-- O texto que ele escreveu existia só como pixel na tela dele. Isso é a seção 0
-- do CLAUDE.md — "nem informação ... pode ser perdido" — e era o produto inteiro
-- que estava assim: TODA falha de envio humano some sem deixar rastro.
--
-- E some duas vezes: some pro cliente, que não recebe, e some pro diagnóstico.
-- Passei uma manhã sem conseguir dizer por que uma mensagem não saiu, porque a
-- única prova de que alguém tentou era um print de celular.
--
-- Daqui em diante toda tentativa que falha vira uma linha aqui, com o TEXTO
-- inteiro. Serve pra três coisas, nesta ordem:
--   1. não perder o que a pessoa escreveu — dá pra devolver pra ela;
--   2. responder "o que aconteceu" com uma consulta, não com arqueologia;
--   3. medir: se as falhas se concentram num erro, num chip ou numa hora, isso
--      aparece.
--
-- Guarda o texto de propósito. É conteúdo de cliente, e a alternativa (guardar só
-- o erro) é justamente o que não resolveu o caso da Sheila.
--
-- Aditivo e idempotente.

create table if not exists public.envio_falha (
  id             bigserial primary key,
  conta_id       bigint not null,
  membro_id      bigint,
  prospeccao_id  bigint,
  conversa_id    bigint,
  canal          text not null default 'whatsapp',
  provedor       text,
  numero         text,
  texto          text,
  erro           text,
  detalhe        text,
  criado_em      timestamptz not null default now()
);

-- "o que falhou nesta conta, mais recente primeiro" é a única leitura que existe
-- hoje, e é a da investigação.
create index if not exists idx_envio_falha_conta
    on public.envio_falha (conta_id, criado_em desc);

-- ...e "esta conversa tem algo pendente?", pra tela poder devolver o texto ao
-- vendedor sem varrer a conta inteira.
create index if not exists idx_envio_falha_conversa
    on public.envio_falha (conversa_id, criado_em desc)
 where conversa_id is not null;

-- rollback:
--   drop table if exists public.envio_falha;
