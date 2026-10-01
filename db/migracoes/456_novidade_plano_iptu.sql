-- 456_novidade_plano_iptu.sql
-- O aviso da 455: a conta 5.1.14 IPTU entrou no plano de contas, em Despesas
-- Operacionais. Plano global, ligada pra todas as empresas: público `todos`,
-- igual ao aviso da 454. PRA QUEM: dono e gestor (quem lança despesa e cuida do
-- plano); o vendedor não vê plano de contas.
-- Aditivo e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('plano-iptu', 'novidade', 'todos', '{dono,gestor}',
 'Nova conta no plano: IPTU',
 'O plano de contas ganhou uma conta própria pra IPTU, em Despesas Operacionais.',
 '/painel/empresa#plano-contas',
 $txt$O plano de contas ganhou a conta 5.1.14 IPTU, dentro de Despesas Operacionais.

É pra o imposto predial do imóvel da empresa — separado de Aluguel e Condomínio porque IPTU tem guia e vencimento próprios.

Ela já vem ligada e aparece na lista quando você lança uma despesa. Se a sua empresa não paga IPTU (não tem imóvel próprio nem alugado com IPTU por conta própria), dá pra desligar em Empresa, no plano de contas. Lançamento antigo não muda de lugar sozinho — se quiser reclassificar algum, é na tela do lançamento.$txt$,
 timestamptz '2026-10-01 12:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'plano-iptu';
