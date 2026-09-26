-- 372_novidade_etapa_paga.sql
-- O aviso da mão de obra paga por etapa, seguindo a seção 5 do CLAUDE.md: PR que
-- muda tela leva o aviso, no mesmo PR. Precisa da 371 e da 350 (o portão
-- `construcao`). Desenho aprovado pelo dono em 25/09/2026:
-- docs/mockups/nicho_construcao.html, seção 07.
--
-- QUEM RECEBE, conferido na produção em 26/09/2026 (contas × nichos, só leitura):
--   conta 33 · Pablo Thyago G. Dias / PX2 Empreendimentos · Lago da Pedra-MA
--
-- PRA QUEM: dono e gestor.
--
-- Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values

('obras-etapa-paga', 'novidade', 'construcao', '{dono,gestor}',
 'O empreiteiro pago por etapa',
 'Diga que etapas cada pagamento de mão de obra fechou: a ficha mostra o que foi pago e não feito (adiantamento) e o que foi feito e ainda não pago.',
 '/painel/obras',
 $txt$Em empreitada, o prejuízo mora em dois lugares: pagar etapa que ainda não foi feita, e pagar a mesma etapa duas vezes.

PELO WHATSAPP

"Paguei 10 mil pro empreiteiro, fundação e estrutura da casa 2." O assistente lança a mão de obra na Casa 2 e marca as duas etapas como pagas, dividindo o valor pelo peso de cada uma. Se a etapa ainda não estiver pronta, ele pergunta se foi adiantamento; se já tinha pagamento, pergunta se é parcela combinada.

NA FICHA DA OBRA

Cada etapa mostra quanto já foi pago. O quadro "Mão de obra por etapa" junta os pagamentos e mostra o que foi pago e não feito, e o que foi feito e ainda não pago — que é o que o empreiteiro vai cobrar. Dá pra marcar pelo painel também, escolhendo o pagamento e as etapas.

Marcar etapa paga não diz que ela ficou pronta: pronta é quando você marca a etapa como concluída.$txt$,
 timestamptz '2026-09-26 20:00:00+00')

on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'obras-etapa-paga';
