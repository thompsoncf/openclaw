-- 026_trava_protecao.sql
-- TRAVA DE PROTEÇÃO contra perda de dados (contas e lançamentos).
-- Criada após o incidente de perda total. Impede que o sistema (ou um comando
-- acidental) apague contas, ou apague lançamentos EM MASSA, sem autorização.
--
-- PERMITE (uso legítimo, sem precisar de nada):
--   - Cliente apagar 1 lançamento dele pelo perfil (delete de 1 linha)
-- PERMITE (com selo explícito do admin):
--   - Limpeza intencional de contas/lançamentos em massa
-- BLOQUEIA (acidente):
--   - Qualquer DELETE em contas (sem selo)
--   - DELETE de 2+ lançamentos de uma vez (sem selo)
--
-- COMO O ADMIN AUTORIZA UMA LIMPEZA (no SQL Editor, na MESMA transação):
--   begin;
--   set local app.permitir_limpeza = 'SIM';
--   delete from contas where id = 99;      -- agora passa
--   commit;
-- Fora dessa transação, a trava volta a valer sozinha.

-- ---------- TRAVA 1: apagar CONTA ----------
create or replace function trava_apagar_conta() returns trigger as $$
begin
  if current_setting('app.permitir_limpeza', true) is distinct from 'SIM' then
    raise exception
      'BLOQUEADO: apagar conta exige autorizacao explicita. Se for intencional, '
      'rode na mesma transacao: set local app.permitir_limpeza = ''SIM'';';
  end if;
  return old;
end;
$$ language plpgsql;

drop trigger if exists tg_trava_conta on contas;
create trigger tg_trava_conta
  before delete on contas
  for each row execute function trava_apagar_conta();

-- ---------- TRAVA 2: apagar LANÇAMENTO em massa ----------
-- Conta quantas linhas o delete afetou (tabela de transição, Postgres 10+).
-- 1 linha (cliente apagando o próprio) passa; 2+ sem selo é bloqueado.
create or replace function trava_lancamento_massa() returns trigger as $$
declare
  n bigint;
begin
  if current_setting('app.permitir_limpeza', true) is distinct from 'SIM' then
    select count(*) into n from deletadas;
    if n > 1 then
      raise exception
        'BLOQUEADO: apagar % lancamentos de uma vez exige autorizacao. '
        'Cliente apaga 1 por vez. Pra limpeza em massa, rode na mesma transacao: '
        'set local app.permitir_limpeza = ''SIM'';', n;
    end if;
  end if;
  return null;
end;
$$ language plpgsql;

drop trigger if exists tg_trava_lancamento_massa on lancamentos;
create trigger tg_trava_lancamento_massa
  after delete on lancamentos
  referencing old table as deletadas
  for each statement execute function trava_lancamento_massa();
