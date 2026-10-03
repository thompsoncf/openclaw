-- 680_aparencia_temas.sql
-- Temas do Zaq, fase 1 (docs/mockups/zaq_temas.html, aprovado pelo dono em 03/10/2026).
-- contas/aparencia.py e web/tema.py.
--
-- contas.tema          o tema da empresa: escuro, claro, misto ou auto. Vazio = escuro.
-- contas.temas_piloto  o portão da fase 1: só conta marcada vê a tela Aparência e recebe
--                      tema diferente do escuro. Liga no Espaço Pelle (conta 39, nicho
--                      clínica), a piloto escolhida pelo dono.
-- membros.tema         o tema da pessoa. Vazio = segue o da empresa.
--
-- Aditiva e idempotente; nenhuma linha existente muda além da marca de piloto.

alter table public.contas
  add column if not exists tema text,
  add column if not exists temas_piloto boolean not null default false;

alter table public.membros
  add column if not exists tema text;

do $$ begin
  if not exists (select 1 from pg_constraint where conname = 'contas_tema_check') then
    alter table public.contas add constraint contas_tema_check
      check (tema is null or tema in ('escuro','claro','misto','auto'));
  end if;
  if not exists (select 1 from pg_constraint where conname = 'membros_tema_check') then
    alter table public.membros add constraint membros_tema_check
      check (tema is null or tema in ('escuro','claro','misto','auto'));
  end if;
end $$;

-- A piloto. Conferindo o nicho junto do id: num banco em que a conta 39 seja outra
-- empresa (teste, cópia), ninguém vira piloto sem querer. Dentro do `if` porque há
-- banco de teste sem `contas.nicho_id` (test_blindagem_migracoes pula a 031).
do $$ begin
  if exists (select 1 from information_schema.columns
              where table_schema = 'public' and table_name = 'contas' and column_name = 'nicho_id')
     and to_regclass('public.nichos') is not null then
    execute $q$update public.contas set temas_piloto = true
                where id = 39
                  and nicho_id = (select id from public.nichos where slug = 'clinica')$q$;
  end if;
end $$;

-- rollback:
--   alter table public.contas drop constraint if exists contas_tema_check,
--     drop column if exists tema, drop column if exists temas_piloto;
--   alter table public.membros drop constraint if exists membros_tema_check,
--     drop column if exists tema;
