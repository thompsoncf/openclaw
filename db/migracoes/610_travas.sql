-- 610_travas.sql
-- A TRAVA QUE O POOLER NÃO QUEBRA (db/trava.py).
--
-- O caso (03/10/2026, Prime, conta 34): o resgate da IA decidiu NÃO chamar o lead #881
-- ("o cliente já fechou") e avisou o supervisor; 3 segundos depois saiu uma retomada pro
-- mesmo cliente. Eram os dois workers do uvicorn rodando o ciclo ao mesmo tempo. A trava
-- do resgate era `pg_try_advisory_lock` — trava de SESSÃO — e o banco é acessado pelo
-- pooler do Supabase, que entrega cada comando a uma conexão de servidor qualquer: a trava
-- era pega numa, o `pg_advisory_unlock` caía em outra ("you don't own a lock of type
-- ExclusiveLock" no log do Postgres, em todo ciclo), a trava ficava presa na primeira, e
-- qualquer worker que caísse nela "pegava" de novo.
--
-- Aqui a trava é uma LINHA: quem consegue gravar o nome leva, com prazo de validade (se o
-- processo morre no meio, o prazo vence e o próximo ciclo pega). Vale com qualquer pooler.
--
-- Aditiva e idempotente; nenhuma tabela existente muda.

create table if not exists public.travas (
  nome      text primary key,
  dono      text not null,          -- máquina:pid:sorteio de quem pegou
  expira_em timestamptz not null,
  pegou_em  timestamptz not null default now()
);
