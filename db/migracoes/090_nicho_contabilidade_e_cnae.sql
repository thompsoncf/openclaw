-- 090_nicho_contabilidade_e_cnae.sql
-- Molda o sistema ao CNAE da empresa:
--
-- 1) nicho 'contabilidade' (serviço) na tabela `nichos`, casando o slug com
--    finance/nichos.py. Sem isso, contas.nicho_id (FK) não pode apontar pra ele.
--
-- 2) contas.cnae: guarda a atividade econômica CRUA (descrição do CNAE da
--    Receita), que antes era calculada no cadastro e DESCARTADA. Ter o CNAE
--    permite moldar a persona/relatórios ao ramo real, não só ao nicho derivado.
--
-- Ambos aditivos e idempotentes: zero impacto no que já está no ar.

insert into nichos (nome, slug, tipo)
select 'Contabilidade / Escritório contábil', 'contabilidade', 'servico'
where not exists (select 1 from nichos n where n.slug = 'contabilidade');

alter table contas add column if not exists cnae text;

-- rollback:
--   alter table contas drop column if exists cnae;
--   delete from nichos where slug = 'contabilidade';
