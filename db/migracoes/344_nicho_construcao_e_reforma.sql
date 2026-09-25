-- 344_nicho_construcao_e_reforma.sql
-- O nicho 'construcao' passa a se chamar "Construção e reforma", o nome que o
-- dono deu ao ramo em 25/09/2026, e o que a primeira conta dele faz: a PX2
-- Empreendimentos (conta 33, Lago da Pedra-MA) constrói casa popular pra vender
-- pelo Minha Casa Minha Vida e faz reforma. Desenho aprovado no mesmo dia em
-- docs/mockups/nicho_construcao.html.
--
-- O SLUG NÃO MUDA. contas.nicho_id aponta pro id, e o código inteiro
-- (finance/nichos.py, cnpj_info, raio_x_perfil, novidades) casa pelo slug. Só o
-- nome muda — o que o admin lê na tabela — pra acompanhar o label do código, que
-- é o que o painel mostra.
--
-- A conta 33 já está no nicho (nicho_id 21, conferido na produção em 25/09, só
-- leitura). Nenhum vínculo a fazer.
--
-- Aditiva e idempotente: cria o nicho se a base não tiver rodado a 109, e acerta
-- o nome se ele ainda for o antigo.

insert into nichos (nome, slug, tipo)
select 'Construção e reforma', 'construcao', 'servico'
where not exists (select 1 from nichos n where n.slug = 'construcao');

update nichos set nome = 'Construção e reforma'
 where slug = 'construcao' and nome is distinct from 'Construção e reforma';

-- rollback:
--   update nichos set nome = 'Construção civil / Obras' where slug = 'construcao';
