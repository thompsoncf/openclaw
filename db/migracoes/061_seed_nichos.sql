-- 061_seed_nichos.sql
-- Semeia os 7 nichos do modulo Vendas na tabela `nichos`, casando os SLUGS com o
-- finance/nichos.py. Como contas.nicho_id e' FK -> nichos(id), os nichos precisam
-- existir na tabela pra poder gravar o nicho da conta.
-- 'hortifruti' ja existe (migracao 031); os outros 6 entram aqui. Idempotente.

insert into nichos (nome, slug, tipo)
select v.nome, v.slug, 'produto'
from (values
    ('Vestuário / Acessórios',   'vestuario'),
    ('Minimercado / Mercearia',  'minimercado'),
    ('Alimentação / Lanche',     'alimentacao'),
    ('Farmácia / Saúde',         'farmacia'),
    ('Beleza / Cosméticos',      'beleza'),
    ('Outro / Genérico',         'generico')
) as v(nome, slug)
where not exists (select 1 from nichos n where n.slug = v.slug);

-- rollback (remove os 6 semeados aqui; NAO remove hortifruti):
--   delete from nichos where slug in
--     ('vestuario','minimercado','alimentacao','farmacia','beleza','generico');
