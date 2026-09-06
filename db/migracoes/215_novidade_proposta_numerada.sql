-- 215_novidade_proposta_numerada.sql
-- Toda proposta nasce numerada, por qualquer porta (regra §5 do CLAUDE.md).
--
-- O QUE MUDOU NA TELA
-- Quatro portas criam orçamento — painel, app do vendedor, prospecção e agente —
-- e só o PAINEL numerava. As outras três inseriam sem `numero`, e a proposta só
-- ganhava o dela se alguém depois a abrisse e salvasse no painel. Quem criava e
-- mandava tudo pelo celular ficava com uma proposta sem número pra sempre, no
-- card do funil e na lista de Serviços.
--
-- O PORTÃO: `servico`. Orçamento é do módulo Serviços — conta que não vende
-- serviço não tem essa tela, e avisar ali prometeria número numa proposta que ela
-- nunca faz. Não é por nicho de eventos: a numeração vale igual pra quem vende
-- por mensalidade (§6 — o vocabulário é o mesmo nos dois, "proposta nº N").
--
-- PRA QUEM: dono, gestor e VENDEDOR. É o vendedor quem vive a mudança — ele cria
-- a proposta no app e é no card dele que o número aparecia vazio.
--
-- QUEM RECEBE, conferido na produção em 06/09/2026:
--   conta 34 · MANOEL SOARES (Prime Eventos) · eventos     · equipe de 5
--   conta  3 · Thompson Cavalcante (ZAQ)     · consultoria · equipe de 5
-- (as duas contas com orçamento; as demais não têm o módulo Serviços ativo)
--
-- A MEDIÇÃO QUE MOTIVOU: existia UMA proposta criada pelo app em toda a produção
-- (`canal='cockpit'`), e era exatamente a única sem número — 1 de 35. Um caso só
-- porque o construtor do app é novo; o comunicado de 03/09 manda os vendedores
-- trabalharem por lá, então a exceção viraria a regra.
--
-- Aditivo e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('proposta-numerada-em-qualquer-porta', 'novidade', 'servico', '{dono,gestor,vendedor}',
 'Toda proposta nasce com número, inclusive as do app',
 'A proposta criada no app do vendedor, na prospecção ou pelo agente passou a receber o número na hora — antes só ganhava número quem fosse aberto e salvo no painel.',
 '/painel/servicos',
 $txt$A proposta feita no app do vendedor nascia sem número. Ela funcionava — link, envio, aprovação, contrato —, mas aparecia sem o "nº" no card do funil e na lista de Serviços, e só ganhava o dela se alguém depois a abrisse e salvasse no computador.

Agora o número sai junto com a proposta, em qualquer lugar onde ela nasça: painel, app, prospecção ou agente.

A NUMERAÇÃO É UMA SÓ POR EMPRESA. O app não tem série própria: se a última proposta da casa foi a nº 20, a próxima é a 21, tenha sido feita no celular ou no computador. Dois vendedores salvando no mesmo instante não tiram o mesmo número.

As propostas antigas que ficaram sem número seguem como estão até alguém abrir e salvar — o que já acontecia antes. O que muda é daqui pra frente.$txt$,
 timestamptz '2026-09-06 18:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'proposta-numerada-em-qualquer-porta';
