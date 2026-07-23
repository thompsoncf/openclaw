-- 091_nichos_servico_profissional.sql
-- Novos ramos de SERVIÇO que faturam por honorário/mensalidade, moldados no mesmo
-- framework da contabilidade (persona por nicho + carteira de clientes):
--   advocacia, arquitetura, agencia (marketing), tecnologia (software/TI).
-- 'consultoria' já foi semeada (062) — aqui só re-rotula pra "assessoria
-- empresarial" (separada de TI). Casa os slugs com finance/nichos.py.
-- contas.nicho_id é FK -> nichos(id), então precisam existir na tabela.
-- Idempotente e aditivo.

insert into nichos (nome, slug, tipo)
select v.nome, v.slug, 'servico'
from (values
    ('Advocacia / Escritório de advocacia',     'advocacia'),
    ('Arquitetura / Engenharia',                'arquitetura'),
    ('Agência de marketing / publicidade',      'agencia'),
    ('Tecnologia / Software',                   'tecnologia')
) as v(nome, slug)
where not exists (select 1 from nichos n where n.slug = v.slug);

-- consultoria já existe: atualiza só o rótulo (separa de "tecnologia")
update nichos set nome = 'Consultoria / Assessoria empresarial'
 where slug = 'consultoria';

-- rollback:
--   delete from nichos where slug in ('advocacia','arquitetura','agencia','tecnologia');
--   update nichos set nome='Consultoria / Tecnologia' where slug='consultoria';
