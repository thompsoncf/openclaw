-- 314_novidade_proposta_recorrente.sql
-- A proposta de quem vende mensalidade ganhou cara de documento.
--
-- O QUE MUDOU NA TELA:
--   * A folha da proposta (o link que o cliente abre):
--     - no alto, o nome da EMPRESA com razão social, CNPJ, endereço e contato
--       (saía o nome de quem abriu a conta, sem dado nenhum);
--     - o contratante inteiro: CNPJ/CPF, contato, WhatsApp, e-mail, cidade;
--     - cada serviço com ícone, a lista do que inclui, o valor cheio riscado e o
--       valor com desconto por mês;
--     - a conta que fecha: mensalidade de tabela, desconto, "Investimento mensal";
--     - as duas formas de pagamento, mensal e anual à vista (−15%), e o CLIENTE
--       escolhe ao aprovar;
--     - as condições (sem fidelidade, aviso prévio, reajuste, suporte), tiradas
--       dos números da casa do contrato.
--   * No editor, o desconto em R$ de um serviço passou a ser POR MÊS ("R$/mês") e
--     a linha mostra quanto ela cobra por mês depois dele.
--   * Os serviços ganharam ícones próprios (anúncios, redes, vídeo, IA...),
--     escolhidos pelo nome, com seletor no catálogo.
--   * O contrato de prestação de serviços abre com o mesmo cabeçalho.
--
-- POR QUE. A proposta nº 10 da ZAQ (conta 3), pra HLED, em 23/09/2026: o dono deu
-- R$ 500 de desconto em cada serviço pra fechar R$ 3.000/mês, e a folha saiu com
-- "Mensalidade R$ 5.700" e "Total 1º ano R$ 65.699" — o desconto em R$ era lido
-- como fatia do ANO (R$ 41,67/mês). Mockup aprovado:
-- docs/mockups/zaq_proposta_recorrente.html.
--
-- É 'mudanca' e não 'novidade': o R$ do desconto mudou de significado.
--
-- PRA QUEM: dono, gestor e vendedor — é quem monta e manda proposta.
--
-- O PORTÃO: `recorrente` — a folha do evento (Prime) não mudou.
--
-- CONTAS ALCANÇADAS (as mesmas da 310, leitura em produção):
--   3 ZAQ - SISTEMAS IAs · 16 SUPER FIT · 21 MGB SOLUTIONS · 23 RAMO CAPITAL ·
--   30 PC CONTABILIDADE · 33 PX2 EMPRRENDIMENTOS ·
--   37 LIBERAL NETO CORRETAGEM DE SEGUROS · 39 ESPACO PELLE CLINICA DERMATOLOGICA
--
-- Aditivo e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('proposta-recorrente-documento', 'mudanca', 'recorrente', '{dono,gestor,vendedor}',
 'A proposta de serviço ficou com cara de documento',
 'A proposta que o cliente recebe agora traz os dados completos das duas empresas, cada serviço com ícone e a lista do que inclui, o desconto à vista, o investimento mensal em destaque e as duas formas de pagamento para o cliente escolher.',
 '/painel/servicos',
 $txt$A folha da proposta que o seu cliente abre foi redesenhada.

O QUE O CLIENTE VÊ AGORA

• No alto, o nome da sua empresa com razão social, CNPJ, endereço e contato.
• Os dados dele completos: CNPJ ou CPF, responsável, WhatsApp, e-mail e cidade.
• Cada serviço com ícone e a lista do que está incluso. Quando tem desconto, o valor cheio aparece riscado ao lado do valor por mês.
• A conta fechada: mensalidade de tabela, desconto e o "Investimento mensal" em destaque.
• As duas formas de pagamento lado a lado: mensal ou anual à vista, com 15% de desconto. O cliente escolhe na hora de aprovar.
• As condições (sem fidelidade, aviso prévio, reajuste e suporte), tiradas dos números do seu contrato.

MUDOU NO EDITOR

O desconto em R$ de um serviço agora é POR MÊS. R$ 500 numa mensalidade de R$ 1.500 deixa o serviço em R$ 1.000 por mês — e a linha mostra esse valor ao lado. Antes, o R$ era dividido pelo ano inteiro. O desconto em % continua igual.

As propostas que você já salvou não mudam de valor sozinhas: abra e salve de novo para valer a regra nova.

E MAIS

Os serviços ganharam ícones próprios (anúncios, redes sociais, vídeo, IA, automação...). Eles são escolhidos pelo nome, e no catálogo dá para trocar com um clique. O contrato de prestação de serviços também abre com o mesmo cabeçalho.$txt$,
 timestamptz '2026-09-23 23:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'proposta-recorrente-documento';
