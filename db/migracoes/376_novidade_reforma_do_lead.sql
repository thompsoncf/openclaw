-- 376_novidade_reforma_do_lead.sql
-- O aviso da reforma ligada ao lead, seguindo a seção 5 do CLAUDE.md: PR que muda
-- tela leva o aviso, no mesmo PR. Precisa da 375 e da 350 (o portão `construcao`).
-- Desenho aprovado pelo dono em 25/09/2026: docs/mockups/nicho_construcao.html,
-- seções 08 e 09.
--
-- QUEM RECEBE, conferido na produção em 26/09/2026 (contas × nichos, só leitura):
--   conta 33 · Pablo Thyago G. Dias / PX2 Empreendimentos · Lago da Pedra-MA
--
-- PRA QUEM: dono e gestor (o botão é de quem gerencia).
--
-- Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values

('obras-reforma-do-lead', 'novidade', 'construcao', '{dono,gestor}',
 'Do lead ao orçamento da reforma, e o card anda sozinho',
 'Na ficha do lead, "Abrir a reforma" cria a obra com o orçamento. Mandou o orçamento, o card vai pra Proposta; o cliente aceitou, vai pra Fechado.',
 '/painel/prospeccao',
 $txt$O cliente pediu orçamento de reforma e está no funil. Agora o caminho até a obra é um botão.

NA FICHA DO LEAD

"Abrir a reforma" cria a obra de reforma com o nome do cliente e leva direto pro orçamento. Se ela já existe, o botão vira "Ver a reforma".

O CARD ANDA SOZINHO

Mandou o link do orçamento: o card vai pra Proposta. O cliente aceitou: vai pra Fechado. No Reforma Casa Brasil o aceite leva pra Crédito em análise — o cliente ainda espera a Caixa —, e quando a primeira parcela entra (o "Recebi" na ficha da obra), vai pra Fechado. Recusou: o card fica onde está, e quem decide se foi perda é você.

A COBRANÇA VAI PRO NÚMERO CERTO

Com o lead ligado, o "Cobrar no WhatsApp" da parcela já abre a conversa com o cliente, sem precisar procurar o contato.$txt$,
 timestamptz '2026-09-26 22:00:00+00')

on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'obras-reforma-do-lead';
