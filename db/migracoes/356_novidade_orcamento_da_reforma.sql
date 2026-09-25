-- 356_novidade_orcamento_da_reforma.sql
-- O aviso do orçamento da reforma, seguindo a seção 5 do CLAUDE.md: PR que muda
-- tela leva o aviso, no mesmo PR. Precisa da 355 (o orçamento) e da 350 (o
-- portão `construcao`). Desenho aprovado pelo dono em 25/09/2026:
-- docs/mockups/nicho_construcao.html, seção 09.
--
-- PORTÃO `construcao`: a ficha da reforma só abre pro perfil `obras`.
--
-- QUEM RECEBE, conferido na produção em 25/09/2026 (contas × nichos, só leitura):
--   conta 33 · Pablo Thyago G. Dias / PX2 Empreendimentos · Lago da Pedra-MA
--
-- PRA QUEM: dono e gestor.
--
-- Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values

('obras-orcamento-da-reforma', 'novidade', 'construcao', '{dono,gestor}',
 'Orçamento de reforma com aceite do cliente',
 'A reforma ganhou orçamento com mão de obra, material e equipamento separados, link pro cliente aceitar, aditivo pra serviço extra e cobrança por etapa.',
 '/painel/obras',
 $txt$Numa reforma, a briga mais comum é a do serviço extra cobrado no meio da obra. A outra é o cliente que some depois da etapa feita. O orçamento da reforma resolve as duas pelo papel.

O ORÇAMENTO

Na ficha de uma obra do tipo reforma, em Obras: cada serviço com quantidade, unidade (m², diária, empreitada) e valor, separado em mão de obra, material e equipamento — é o que o Código de Defesa do Consumidor pede no orçamento. Diga se o material está incluso ou se o cliente compra, o prazo, o escopo e a garantia.

O LINK PRO CLIENTE

"Gerar o link" cria uma página com o orçamento, as condições e um botão de aceitar. Você manda no WhatsApp; o cliente lê, escreve o nome e aceita. O aceite fica registrado com nome, data e o endereço de internet de onde foi feito. O link vale 10 dias.

COMO PAGA

Entrada mais parcelas ligadas às etapas da obra, à vista, ou o modelo do Reforma Casa Brasil — o crédito da Caixa em que o cliente recebe 90% na contratação e 10% depois das fotos da obra pronta, com 55 dias de prazo. Aceito o orçamento, cada parcela vira uma conta a receber na obra.

SERVIÇO EXTRA É ADITIVO

Depois do aceite, o orçamento não muda mais. Serviço novo entra como aditivo: outro link, outro aceite, outras parcelas.

A ETAPA FEITA LIBERA A PARCELA

Quando você diz no WhatsApp "terminou o reboco da reforma", o assistente marca a etapa e avisa que a parcela daquela etapa já pode ser cobrada.$txt$,
 timestamptz '2026-09-26 00:30:00+00')

on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'obras-orcamento-da-reforma';
