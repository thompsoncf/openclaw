-- 308_nicho_clinica.sql
-- Nicho 'clinica': MISTO — atende (consulta, procedimento, pacote de sessões) e
-- vende produto de beleza (dermocosmético, protetor), junto com o atendimento ou
-- separado. Casa o slug com finance/nichos.py e com o ramo Clinica de
-- finance/cnpj_info.py. contas.nicho_id é FK -> nichos, então precisa existir
-- na tabela.
--
-- POR QUE AGORA: a conta 39 (Espaço Pelle Clínica Dermatológica, Pedreiras-MA,
-- trial desde 21/09/2026) está com nicho_id nulo, e nenhum dos 24 nichos
-- descreve uma clínica — 'salao' é beleza sem ato de saúde, 'farmacia' é balcão
-- de remédio sem atendimento.
--
-- O VÍNCULO DA CONTA 39 vai aqui, e não na mão, porque a FK só aceita o slug
-- depois deste insert, e o insert só roda no pre-deploy. Só grava se o nicho
-- ainda estiver vazio: se ela escolher outro na tela Empresa antes do deploy, a
-- escolha dela vale. vende_produto/vende_servico da conta estão nulos, então ela
-- herda o par do nicho (os dois ligados) sem precisar gravar nada.
--
-- MODO DO ORÇAMENTO: nada a fazer — fora de NICHOS_EVENTO é 'recorrente'.
-- PERFIL DO RAIO-X: misto (vende_servico) cai em 'recorrente', com funil.
--
-- 'tipo' na tabela é legado (a verdade é o par de flags no código), e vai
-- 'produto' como em 'eventos' e 'suplementos', que também são mistos.
--
-- Aditiva e idempotente.

insert into nichos (nome, slug, tipo)
select 'Clínica / Saúde', 'clinica', 'produto'
where not exists (select 1 from nichos n where n.slug = 'clinica');

update contas
   set nicho_id = (select id from nichos where slug = 'clinica')
 where id = 39
   and nicho_id is null;

-- rollback:
--   update contas set nicho_id = null
--    where id = 39 and nicho_id = (select id from nichos where slug = 'clinica');
--   delete from nichos where slug = 'clinica';
