-- 030_email_unico.sql
-- Remove duplicados de email e cria trava (unique index).
-- Usa o selo para limpeza intencional (set local app.permitir_limpeza).

-- Remove duplicatas (mantém a conta com MENOR id, apaga as mais novas)
begin;
set local app.permitir_limpeza = 'SIM';
delete from contas a using contas b
  where a.email is not null
    and lower(a.email) = lower(b.email)
    and a.id > b.id;
commit;

-- Trava: email único (case-insensitive), só vale pra emails não-nulos
create unique index if not exists idx_contas_email_unico
  on contas (lower(email)) where email is not null;
