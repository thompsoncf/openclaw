-- 368_novidade_cobrar_parcela_com_pix.sql
-- O aviso da cobrança da parcela da reforma com o Pix da própria empresa, seguindo
-- a seção 5 do CLAUDE.md: PR que muda tela leva o aviso, no mesmo PR. Precisa da
-- 367 (a chave Pix) e da 350 (o portão `construcao`). Decisões do dono em
-- 26/09/2026: Pix direto na conta da empresa; a mensagem sai do WhatsApp dela.
--
-- QUEM RECEBE, conferido na produção em 26/09/2026 (contas × nichos, só leitura):
--   conta 33 · Pablo Thyago G. Dias / PX2 Empreendimentos · Lago da Pedra-MA
--
-- PRA QUEM: dono e gestor.
--
-- Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values

('obras-cobrar-parcela-com-pix', 'novidade', 'construcao', '{dono,gestor}',
 'Cobrar a parcela da reforma com o Pix pronto',
 'Terminou a etapa, a parcela ganha um botão de cobrar: a mensagem vai do seu WhatsApp com o Pix copia e cola da empresa, e o cliente também vê o QR no link do orçamento.',
 '/painel/obras',
 $txt$A etapa da reforma ficou pronta e a parcela dela já pode ser cobrada. Agora a cobrança sai pronta.

A CHAVE PIX DA EMPRESA

Na ficha de uma reforma, em Obras, o dono cadastra a chave Pix da empresa (CNPJ, e-mail, celular ou aleatória). O dinheiro cai direto na conta da empresa — o Zaq não passa no meio. Só o dono troca a chave.

COBRAR NO WHATSAPP

Cada parcela liberada ganha o botão "Cobrar no WhatsApp": abre o seu WhatsApp com a mensagem pronta — a etapa que ficou pronta, o valor e o Pix copia e cola. O cliente recebe de um número que ele conhece. No link do orçamento, ele também vê o QR code pra pagar.

Pelo WhatsApp do Zaq também dá: "monta a cobrança da reforma da Dona Márcia".

QUANDO O CLIENTE PAGAR

O Pix cai na conta da empresa, então quem sabe que pagou é você: toque em "Recebi" na parcela, ou diga no WhatsApp "a Dona Márcia pagou a parcela". A parcela vira receita da obra e sai da lista de cobrar.$txt$,
 timestamptz '2026-09-26 15:00:00+00')

on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'obras-cobrar-parcela-com-pix';
