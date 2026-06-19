-- 027_sem_cascade_conta.sql
-- BLINDAGEM TOTAL: nada apaga conta de usuario por cascata.
-- Pedido do dono: NENHUM codigo pode derrubar conta/dados em cascata. Apagar conta
-- so' manualmente, com autorizacao explicita (selo da trava 026).
--
-- O que faz:
-- 1. Troca todos os "on delete CASCADE" que apontam pra contas por "on delete RESTRICT".
--    RESTRICT = o banco RECUSA apagar uma conta que ainda tem membros/lancamentos/etc.
--    (Pra apagar de verdade, tem que apagar os filhos antes, de proposito.)
-- 2. Reforca a trava: bloqueia TRUNCATE em contas/lancamentos/membros tambem.

-- ---------- 1. trocar CASCADE por RESTRICT nas FKs que apontam pra contas ----------
-- membros
alter table membros drop constraint if exists membros_conta_id_fkey;
alter table membros add constraint membros_conta_id_fkey
  foreign key (conta_id) references contas(id) on delete restrict;

-- conta_modulos
alter table conta_modulos drop constraint if exists conta_modulos_conta_id_fkey;
alter table conta_modulos add constraint conta_modulos_conta_id_fkey
  foreign key (conta_id) references contas(id) on delete restrict;

-- lancamentos
alter table lancamentos drop constraint if exists lancamentos_conta_id_fkey;
alter table lancamentos add constraint lancamentos_conta_id_fkey
  foreign key (conta_id) references contas(id) on delete restrict;

-- uso_diario
alter table uso_diario drop constraint if exists uso_diario_conta_id_fkey;
alter table uso_diario add constraint uso_diario_conta_id_fkey
  foreign key (conta_id) references contas(id) on delete restrict;

-- eventos_conta
alter table eventos_conta drop constraint if exists eventos_conta_conta_id_fkey;
alter table eventos_conta add constraint eventos_conta_conta_id_fkey
  foreign key (conta_id) references contas(id) on delete restrict;

-- qr_leituras
alter table qr_leituras drop constraint if exists qr_leituras_conta_id_fkey;
alter table qr_leituras add constraint qr_leituras_conta_id_fkey
  foreign key (conta_id) references contas(id) on delete restrict;

-- ---------- 2. trava contra TRUNCATE (alem do delete que a 026 ja' cobre) ----------
-- TRUNCATE nao dispara trigger de DELETE. Precisa de trigger BEFORE TRUNCATE.
create or replace function trava_truncate() returns trigger as $$
begin
  if current_setting('app.permitir_limpeza', true) is distinct from 'SIM' then
    raise exception
      'BLOQUEADO: TRUNCATE em % exige autorizacao explicita (set local '
      'app.permitir_limpeza = ''SIM''). Protecao contra apagar dados.', TG_TABLE_NAME;
  end if;
  return null;
end;
$$ language plpgsql;

drop trigger if exists tg_trava_truncate_contas on contas;
create trigger tg_trava_truncate_contas
  before truncate on contas
  for each statement execute function trava_truncate();

drop trigger if exists tg_trava_truncate_lancamentos on lancamentos;
create trigger tg_trava_truncate_lancamentos
  before truncate on lancamentos
  for each statement execute function trava_truncate();

drop trigger if exists tg_trava_truncate_membros on membros;
create trigger tg_trava_truncate_membros
  before truncate on membros
  for each statement execute function trava_truncate();
