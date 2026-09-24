-- 337_novidade_plano_fardamentos.sql
-- O aviso da 336: a conta 4.1.06 Fardamentos entrou no plano de contas, em
-- Despesas com Pessoal. Pedido da Prime (24/09/2026), mas o plano é global e a
-- conta aparece ligada pra todas as empresas — por isso o público é `todos`, e o
-- texto diz como desligar pra quem não usa.
--
-- PRA QUEM: dono e gestor, que são quem lança despesa e cuida do plano.
-- Aditivo e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('plano-fardamentos', 'novidade', 'todos', '{dono,gestor}',
 'Nova conta no plano: Fardamentos',
 'O plano de contas ganhou Fardamentos, em Despesas com Pessoal, pra lançar farda e uniforme da equipe.',
 '/painel/empresa#plano-contas',
 $txt$O plano de contas ganhou a conta 4.1.06 Fardamentos, dentro de Despesas com Pessoal.

É pra farda e uniforme da equipe — garçom, recepção, cozinha, vendedor. Fica junto de salários e benefícios porque é gasto com quem trabalha, e aparece assim no relatório de resultado.

Ela já vem ligada e aparece na lista quando você lança uma despesa. Se a sua empresa não usa, dá pra desligar em Empresa, no plano de contas.$txt$,
 timestamptz '2026-09-24 18:15:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'plano-fardamentos';
