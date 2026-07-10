-- 062_seed_nichos_servico.sql
-- Semeia os 4 nichos de SERVICO na tabela `nichos` (tipo='servico'), casando os
-- slugs com finance/nichos.py. Servico nao tem estoque: a aba Vendas se adapta.
-- Idempotente.

insert into nichos (nome, slug, tipo)
select v.nome, v.slug, 'servico'
from (values
    ('Consultoria / Tecnologia',   'consultoria'),
    ('Beleza / Salão (serviço)',   'salao'),
    ('Educação / Aulas',           'educacao'),
    ('Serviços gerais',            'servicos_gerais')
) as v(nome, slug)
where not exists (select 1 from nichos n where n.slug = v.slug);

-- rollback:
--   delete from nichos where slug in
--     ('consultoria','salao','educacao','servicos_gerais');
