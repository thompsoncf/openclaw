-- 109_nicho_construcao.sql
-- Nicho 'construcao' (construção civil / obras): serviço que fatura por
-- MEDIÇÃO/ETAPA da obra. Casa o slug com finance/nichos.py (persona + rótulo
-- "Medições") e com o ramo Construcao de finance/cnpj_info.py.
-- contas.nicho_id é FK -> nichos(id), então precisa existir na tabela.
-- Idempotente e aditivo.

insert into nichos (nome, slug, tipo)
select 'Construção civil / Obras', 'construcao', 'servico'
where not exists (select 1 from nichos n where n.slug = 'construcao');

-- rollback:
--   delete from nichos where slug = 'construcao';
