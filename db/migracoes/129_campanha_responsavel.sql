-- 129_campanha_responsavel.sql
-- Responsável da campanha: o membro da equipe vinculado a ela (espelha
-- prospeccao.vendedor_id nos leads). Nulo = sem responsável (só dono/gestor veem).
-- Um vendedor passa a ver e gerenciar SÓ as campanhas em que é o responsável
-- (ver web/painel_prospeccao._pode_campanha e o filtro de _campanhas_dados).

alter table public.campanhas add column if not exists responsavel_id bigint;
create index if not exists idx_campanhas_responsavel
  on public.campanhas (conta_id, responsavel_id);
