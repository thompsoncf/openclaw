-- 454_novidade_plano_despesas_diversas.sql
-- O aviso da 453: a conta 5.1.13 Despesas Diversas entrou no plano de contas, em
-- Despesas Operacionais. Plano global, ligada pra todas as empresas: público
-- `todos`, igual ao aviso da 352. PRA QUEM: dono e gestor (quem lança despesa e
-- cuida do plano); o vendedor não vê plano de contas.
-- Aditivo e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('plano-despesas-diversas', 'novidade', 'todos', '{dono,gestor}',
 'Nova conta no plano: Despesas Diversas',
 'O plano de contas ganhou Despesas Diversas, em Despesas Operacionais, pra lançar o gasto pequeno que não cabe em nenhuma outra conta.',
 '/painel/empresa#plano-contas',
 $txt$O plano de contas ganhou a conta 5.1.13 Despesas Diversas, dentro de Despesas Operacionais.

É pra o gasto avulso que não tem conta própria. Antes de usar, veja se ele não cabe em uma mais específica (Aluguel, Marketing, Manutenção, Materiais e Utensílios e as outras): quanto mais coisa cai em Diversas, menos o seu resultado mostra de onde o dinheiro saiu.

O que é consumido no que você vende continua em Custos, nunca aqui.

Ela já vem ligada e aparece na lista quando você lança uma despesa. Se a sua empresa não usa, dá pra desligar em Empresa, no plano de contas. Lançamento antigo não muda de lugar sozinho — se quiser reclassificar algum, é na tela do lançamento.$txt$,
 timestamptz '2026-09-30 20:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'plano-despesas-diversas';
