-- 211_novidade_vendedor_no_funil.sql
-- Aviso da coluna "vendido por" na tela Serviços → Funil de orçamentos. O dono
-- reclamou em 05/09/2026: dono e gestor veem o funil inteiro sem ter como saber
-- de qual vendedor é cada proposta, sem abrir uma por uma.
--
-- QUEM RECEBE: publico='servico' — a tela é gate por `vende_servico`
-- (finance.novidades._n.vende_servico), então só quem vende serviço a alcança.
-- pra_quem só {dono,gestor}: o vendedor já só vê a própria carteira no funil, e
-- pra ele a linha sempre diria o próprio nome — não é rotina nova nenhuma.
--
-- Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values

('vendedor-no-funil', 'novidade', 'servico', '{dono,gestor}',
 'O funil de orçamentos agora mostra quem vendeu cada proposta',
 'Cada linha do funil de orçamentos passa a dizer quem fez a venda, sem precisar abrir a proposta.',
 '/painel/servicos',
 $txt$Antes, a única forma de saber quem fez uma proposta era abrir uma por uma. Agora a linha do funil diz: "vendido por Ana Souza", no mesmo lugar onde já aparece "aprovada por" — mesma voz, informação a mais.

A linha também ficou em duas: quem é o cliente, o número e quem vendeu numa; o valor e o que já aconteceu (aprovada, sinal recebido, data reservada) na outra. Estava virando um parágrafo só, cada vez mais difícil de escanear.

Proposta que nasceu de um vendedor mostra o nome dele; a que o próprio dono montou mostra o nome da empresa — a mesma regra que Relatórios → Vendas já usa pra essa coluna.$txt$,
 timestamptz '2026-09-05 22:00:00+00')

on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'vendedor-no-funil';
