-- 444_novidade_contrato_de_proposta_anterior.sql
-- O aviso da nota por vendedor no Raio-X (pedido do dono em 27/09/2026), seguindo a
-- seção 5 do CLAUDE.md.
--
-- O QUE MUDOU NA TELA: embaixo da tabela "Da visita ao contrato", uma linha por
-- vendedor cujo contrato do período veio de proposta aceita antes dele — o Thiago
-- tinha 4 contratos e 3 propostas assinadas em setembro, e a tabela não dizia por quê.
--
-- PÚBLICO `servico` (quem vende serviço tem a tabela, festa e recorrente). PRA QUEM:
-- dono e gestor — o Raio-X do dono. SEM RESUMO de propósito: é uma explicação de
-- tela, não vai pro site.
--
-- Aditiva e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, link, corpo, publicado_em) values
('raio-x-contrato-de-proposta-anterior', 'mudanca', 'servico', '{dono,gestor}',
 'Raio-X: por que os contratos de alguém passam das propostas',
 '/painel/raio-x',
 $txt$Na tabela "Da visita ao contrato", cada coluna conta pelo dia em que aquilo aconteceu: a proposta pelo dia em que o cliente aceitou, o contrato pelo dia em que assinou. Por isso o contrato de uma proposta aceita no mês anterior entra só nos contratos deste mês — e os contratos de um vendedor podem passar das propostas dele.

Agora, embaixo da tabela, cada vendedor nessa situação ganha uma linha dizendo quantos contratos vieram de proposta aceita antes do período.$txt$,
 timestamptz '2026-09-28 12:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'raio-x-contrato-de-proposta-anterior';
