-- 434_novidade_festa_aconteceu.sql
-- O aviso da revisão do funil, parte 2 (as telas: docs/mockups/revisao_parte2_telas.html,
-- aprovado em 27/09/2026), seguindo a seção 5 do CLAUDE.md.
--
-- PÚBLICO `eventos` (§6): a pergunta "a festa aconteceu?", a Lista de espera e as
-- colunas novas do Raio-X só existem pra quem vende festa. PRA QUEM: dono, gestor e
-- vendedor — o vendedor é quem responde a pergunta e arrasta o card. QUEM RECEBE: toda
-- conta que vende festa (na Prime, 34, tudo já vale no deploy).
--
-- Aditiva e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('festa-aconteceu', 'novidade', 'eventos', '{dono,gestor,vendedor}',
 'A festa aconteceu? O pós-festa espera a confirmação',
 'Quem vende festa confirma, com um toque, que a festa aconteceu antes de o agradecimento sair pro cliente; arrastar o card pra Lista de espera põe na fila da data; e o Raio-X passa a contar os leads de todas as colunas do funil.',
 '/painel/prospeccao',
 $txt$O que muda:

- A FESTA ACONTECEU? No dia seguinte à festa, às 9h, o vendedor do card recebe a pergunta no WhatsApp dos avisos e no app (o dono, se o card é da IA). Às 18h ela se repete. O card mostra o selo com três botões: Aconteceu, Remarcou e Cancelou. Só depois do "Aconteceu" o card vai pro Pós-festa e o agradecimento com o pedido de avaliação sai. Vale pro Fechado e pra Data segurada.
- LISTA DE ESPERA: arrastar o card pra coluna faz o mesmo que o botão "esperar". Se a data está livre, o card não se move e o quadro diz por quê.
- RAIO-X: os leads em Qualificado, Visita feita e Data segurada passam a contar como em aberto (antes ficavam de fora). A Lista de espera continua fora: ela espera a data abrir, não o vendedor.
- HISTÓRICO DO AVISO (Follow-up): cada linha diz o tipo do aviso (cobrança, fecho do dia, visita, festa, teste), e dá pra filtrar por tipo.$txt$,
 timestamptz '2026-09-28 12:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'festa-aconteceu';
