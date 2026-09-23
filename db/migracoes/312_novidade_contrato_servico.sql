-- 312_novidade_contrato_servico.sql
-- O recorrente ganhou contrato de prestação de serviços, com assinatura do cliente.
--
-- O QUE MUDOU NA TELA:
--   * Serviços → dentro da proposta, o card "Contrato de prestação de serviços",
--     só pro DONO: as cláusulas (um modelo inicial pronto pra ajustar), os
--     "números da casa" (fidelidade, vencimento, reajuste, aviso prévio, multa
--     rescisória, implantação, suporte) e a chave "Pedir assinatura de contrato".
--   * Com a chave ligada: a proposta aprovada vira contrato com link próprio, o
--     funil passa a mostrar "Mandar o contrato pra assinar", e a implantação e as
--     mensalidades entram no financeiro quando o cliente assina.
--   * O botão "Pagamento anual (-15%)" passou a ficar gravado na proposta.
--
-- A CHAVE NASCE DESLIGADA em todas as contas (migração 311). Quem não ligar não vê
-- nada mudar no funil — por isso é 'novidade', não 'mudanca'.
--
-- PRA QUEM: só o dono. É ele quem escreve o contrato e liga a chave; o vendedor
-- só vai ver diferença quando o dono ligar, e aí o funil mostra o botão sozinho.
--
-- O PORTÃO: `recorrente` — no nicho de eventos o contrato de locação já existia.
--
-- CONTAS ALCANÇADAS (as mesmas oito da 310, leitura em produção):
--   3 ZAQ - SISTEMAS IAs · 16 SUPER FIT · 21 MGB SOLUTIONS · 23 RAMO CAPITAL ·
--   30 PC CONTABILIDADE · 33 PX2 EMPRRENDIMENTOS ·
--   37 LIBERAL NETO CORRETAGEM DE SEGUROS · 39 ESPACO PELLE CLINICA DERMATOLOGICA
--
-- Aditivo e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('contrato-prestacao-servicos', 'novidade', 'recorrente', '{dono}',
 'Contrato de prestação de serviços, assinado pelo cliente',
 'Quem vende serviço com mensalidade agora pode transformar a proposta aprovada num contrato com link próprio: o cliente lê, assina pelo celular, e a implantação e as mensalidades entram no financeiro na hora da assinatura.',
 '/painel/servicos',
 $txt$A proposta aprovada agora pode virar um contrato de verdade, assinado pelo cliente.

COMO LIGAR

Em Serviços, abra uma proposta (ou clique em "+ Nova proposta") e desça até o card "Contrato de prestação de serviços". Ele já vem com um modelo inicial de cláusulas — objeto, implantação, mensalidade, reajuste, fidelidade, suporte, proteção de dados e cancelamento — que você pode ajustar à vontade.

Antes de ligar, preencha os "Números da casa": fidelidade, dia de vencimento, índice de reajuste, aviso prévio, multa rescisória, prazo de implantação, horário de suporte e como a implantação é paga. Esses números entram sozinhos no texto; enquanto algum estiver em branco, a chave "Pedir assinatura de contrato" não liga — pra nenhum contrato sair com um campo vazio.

O QUE MUDA DEPOIS DE LIGAR

• O cliente aprova a proposta e o contrato nasce na hora, com o nome, o documento, os serviços e os valores dela.
• No funil aparece "Mandar o contrato pra assinar". O cliente abre o link, lê e assina pelo celular — fica registrado nome, CPF, data, hora e IP.
• Quando ele assina, a implantação e a mensalidade entram no financeiro sozinhas. Antes da assinatura, nada é cobrado.

O QUE NÃO MUDA

Com a chave desligada, tudo continua como hoje: a proposta aprovada fecha pelo botão "Fechar negócio". E as propostas que já estavam aprovadas antes de você ligar continuam fechando pelo botão.

E MAIS: o "Pagamento anual (-15%)" passou a ficar gravado na proposta. Antes, ao reabrir, o botão voltava desligado.$txt$,
 timestamptz '2026-09-23 20:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'contrato-prestacao-servicos';
