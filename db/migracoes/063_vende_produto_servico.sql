-- 063_vende_produto_servico.sql
-- (1) Semeia os 4 nichos MISTOS (vendem produto E servico).
-- (2) Adiciona na conta a DECISAO FINAL do que ela vende: vende_produto e
--     vende_servico. Inicializadas pelo padrao do nicho no cadastro, mas o
--     cliente pode destravar a outra aba (parte A+). A aba Produtos/Servicos
--     aparece conforme ESTAS colunas (nao o nicho direto), o que permite o override.
-- Idempotente.

-- (1) nichos mistos
insert into nichos (nome, slug, tipo)
select v.nome, v.slug, 'produto'   -- 'tipo' na tabela e' legado; a verdade e' o par de flags no codigo
from (values
    ('Oficina / Auto',                       'oficina'),
    ('Petshop',                              'petshop'),
    ('Salão completo (produto + serviço)',   'salao_completo'),
    ('Assistência técnica',                  'assistencia')
) as v(nome, slug)
where not exists (select 1 from nichos n where n.slug = v.slug);

-- (2) decisao da conta (null = ainda nao definido; o cadastro preenche do nicho)
alter table contas add column if not exists vende_produto boolean;
alter table contas add column if not exists vende_servico boolean;

-- rollback:
--   alter table contas drop column if exists vende_produto, drop column if exists vende_servico;
--   delete from nichos where slug in ('oficina','petshop','salao_completo','assistencia');
