-- 185_reset_senha_para_membro.sql
-- O "Esqueci minha senha" que não servia pra vendedor.
--
-- O QUE ACONTECEU
-- A tela `/esqueci-senha` só consultava `contas`. Quem tem login de MEMBRO — o
-- vendedor, o gestor, o financeiro — digitava o e-mail, via "enviamos o link" e
-- ficava esperando um link que nunca foi criado. A consulta não achava a linha, o
-- `if row:` não entrava, e a tela respondia "enviado" do mesmo jeito.
--
-- Caso real: `docemellvendas@gmail.com` (Maze, vendedora da conta 35, Doce Mell)
-- ficou de fora do app sem nenhum caminho de volta. Medido em 22/08/2026: são
-- 5 membros com e-mail cadastrado no mesmo beco.
--
-- A mensagem genérica ("se esse e-mail tem conta, enviamos") CONTINUA — ela é a
-- proteção contra descobrir quem é cliente pelo formulário, e sempre esteve certa.
-- O defeito nunca foi a frase; era não existir token nenhum pra metade das pessoas.
--
-- O QUE MUDA AQUI
-- O token passa a poder apontar pra uma conta OU pra um membro. Exatamente um dos
-- dois, garantido por check — um token que não sabe quem ele reseta é pior do que
-- token nenhum, porque `redefinir-senha` escreveria em lugar errado ou em nenhum.
--
-- QUEM MANDA NA IDENTIDADE, quando o e-mail é os dois
-- Se existe conta com aquele e-mail, o reset é da CONTA, nunca do membro. É a
-- mesma regra de `contas.equipe.autenticar` ("a conta própria manda") e de
-- `finance.cockpit.definir_senha`, que se recusa a criar uma segunda senha na
-- linha de membro. Duas senhas pra mesma pessoa foi o bug em que alguém trocou a
-- senha duas vezes e o app continuou recusando — não se repete por aqui.
--
-- Aditivo e idempotente. Nenhuma linha existente muda: todas têm conta_id
-- preenchido e membro_id nulo, então num_nonnulls dá 1 e o check já passa.

-- ─────────────────────────────────── 1. o alvo do token deixa de ser só conta
alter table public.tokens_reset_senha alter column conta_id drop not null;

alter table public.tokens_reset_senha
  add column if not exists membro_id bigint references membros(id) on delete cascade;

-- ─────────────────────────────────── 2. exatamente um alvo, nunca zero nem dois
alter table public.tokens_reset_senha
  drop constraint if exists tokens_reset_senha_alvo_check;
alter table public.tokens_reset_senha
  add constraint tokens_reset_senha_alvo_check
  check (num_nonnulls(conta_id, membro_id) = 1);

create index if not exists ix_token_reset_membro
  on public.tokens_reset_senha (membro_id);

-- rollback:
--   drop index if exists ix_token_reset_membro;
--   alter table public.tokens_reset_senha drop constraint if exists tokens_reset_senha_alvo_check;
--   delete from public.tokens_reset_senha where conta_id is null;   -- órfãos de membro
--   alter table public.tokens_reset_senha drop column if exists membro_id;
--   alter table public.tokens_reset_senha alter column conta_id set not null;
