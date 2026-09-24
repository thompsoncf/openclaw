-- 335_novidade_da_visita_ao_contrato.sql
-- "Da visita ao contrato" no Raio-X do dono e na Visão do Cockpit. Mockup aprovado
-- pelo dono em 24/09/2026 (docs/mockups/prime_visita_ao_contrato.html).
--
-- O QUE MUDOU:
--   * quatro degraus do mesmo período, cada um pela sua data: visitas realizadas,
--     quantas viraram orçamento, propostas assinadas pelo cliente e contratos
--     assinados — com o valor de cada um e a taxa da visita ao orçamento;
--   * os clientes de cada ponta: quem visitou e não tem orçamento, quem tem
--     orçamento e ainda não assinou (o "em jogo") e quem virou contrato;
--   * a tabela proposta aceita × contrato assinado, com a espera entre os dois;
--   * a tabela por vendedor com o total do time e o valor.
--
-- O vocabulário é do nicho (§6): visita/propostas assinadas pra quem vende
-- festa; reunião (cotação, avaliação)/propostas aceitas pra quem vende serviço.
-- Conta de produto não tem o bloco.
--
-- PRA QUEM: dono e gestor. O vendedor não vê a lista dos colegas (resposta do
-- dono, 24/09). Público `servico`.
--
-- Aditivo e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('da-visita-ao-contrato', 'novidade', 'servico', '{dono,gestor}',
 'Da visita ao contrato, com os clientes de cada etapa',
 'O Raio-X e o Cockpit mostram quantas visitas viram orçamento, proposta assinada e contrato, com o valor e os clientes de cada etapa.',
 '/painel/raio-x',
 $txt$O Raio-X e a Visão do Cockpit ganharam o caminho da visita ao contrato.

OS QUATRO DEGRAUS

Visitas realizadas, quantas viraram orçamento, propostas que o cliente assinou e contratos assinados — todos no período escolhido, com o valor de cada um. Entre a visita e o orçamento aparece a taxa: de cada 100 visitas, quantas viram orçamento.

OS CLIENTES DE CADA PONTA

Quem visitou e ainda não tem orçamento, quem tem orçamento e ainda não assinou (o valor em jogo) e quem virou contrato. No Cockpit, é um toque no bloco.

PROPOSTA × CONTRATO

A tabela mostra, contrato por contrato, quando a proposta foi aceita e quando o contrato foi assinado, e quantos dias ficaram no meio. Quando os contratos do mês passam das propostas do mês, a nota diz qual veio de proposta aceita antes.

POR VENDEDOR

A tabela por vendedor tem a linha de total do time, com o valor.$txt$,
 timestamptz '2026-09-24 11:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'da-visita-ao-contrato';
