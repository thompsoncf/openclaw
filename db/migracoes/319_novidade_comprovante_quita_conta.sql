-- 319_novidade_comprovante_quita_conta.sql
-- O comprovante que o dono manda pelo WhatsApp passa a perguntar se quita uma
-- conta a pagar — e, com o "sim", fecha a conta.
--
-- O QUE MUDOU:
--   * Ao registrar um comprovante de EMPRESA, o Zaq procura uma conta em aberto
--     que ele possa quitar, com a mesma régua da tela da Empresa (valor, data,
--     multa e juros que o atraso explica, período e nome no texto). Achando,
--     pergunta na mesma resposta: "Tem uma conta aberta que bate: Águas de
--     Teresina, R$ 86,22, venceu 21/09 — centro DESPESA FIXA. Esse pagamento
--     quita ela?"
--   * Com o "sim", a conta fecha LIGANDO o pagamento que já está no caixa —
--     nenhum dinheiro novo é lançado. Com duas contas possíveis, ele pergunta
--     qual. Sem resposta, nada acontece.
--   * O agente passa a conhecer os centros de custo da empresa: "foi
--     investimento" vira o centro INVESTIMENTO.
--
-- POR QUE. Entrega 3 do plano aprovado pelo dono em 23/09/2026. Medido na Prime
-- (conta 34) no mesmo dia: 85 dos 165 lançamentos nasceram de comprovante, e 9
-- das 13 contas a pagar abertas tinham o pagamento igual já no caixa. Às 20:57
-- entrou o comprovante da água (R$ 86,22), e a conta da água de R$ 86,22,
-- vencida em 21/09, continuou aberta.
--
-- PRA QUEM: dono — só o dono tem as ferramentas da empresa no WhatsApp, e é
-- dele a decisão (resposta do dono em 23/09/2026: a pergunta vai pra ele, nunca
-- pra quem só mandou o arquivo).
--
-- O PORTÃO: `todos` — conta a pagar não é assunto de nicho.
--
-- CONTAS ALCANÇADAS que já usam títulos (leitura em produção, 23/09/2026):
--   3 ZAQ - SISTEMAS IAs · 9 Ceaseiro · 16 SUPER FIT · 30 PC CONTABILIDADE ·
--   34 PRIME EVENTOS
--
-- Aditivo e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('comprovante-quita-conta', 'novidade', 'todos', '{dono}',
 'O comprovante já fecha a conta a pagar',
 'Mandou o comprovante pelo WhatsApp? O Zaq procura a conta em aberto que ele paga e pergunta se pode fechar — um "sim" e a conta sai da lista.',
 '/painel/empresa#titulos',
 $txt$Mande o comprovante como você já manda. A novidade vem na resposta.

O QUE ACONTECE

O Zaq registra o pagamento no caixa, como sempre. Agora, ele também procura uma conta a pagar em aberto que esse pagamento possa quitar. Achando, ele pergunta na mesma mensagem: "Tem uma conta aberta que bate: Águas de Teresina, R$ 86,22, venceu 21/09. Esse pagamento quita ela?"

Responda "sim" e a conta fecha. O pagamento que já estava no caixa é ligado a ela — nenhum dinheiro é lançado duas vezes.

Se forem duas contas possíveis (o mesmo fornecedor, o mesmo valor), ele mostra as duas e pergunta qual. Ele nunca escolhe sozinho.

Se você disser "não", ou não responder, nada acontece: a conta continua aberta na tela da Empresa.

POR QUE ELE ÀS VEZES NÃO PERGUNTA

Ele só oferece a conta quando o comprovante combina de verdade: mesmo valor (ou o valor com a multa e os juros do atraso), data perto do vencimento, e nada no texto que contradiga — a quinzena de agosto não fecha a de setembro, e o Pix de uma pessoa não fecha a conta de outra.

CENTRO DE CUSTO

Se a conta tem centro de custo (ou o fornecedor teve da última vez), ele aparece na pergunta, e o "sim" confirma os dois. E quando você disser "foi investimento" ou "foi despesa fixa", o Zaq já sabe qual dos seus centros é.$txt$,
 timestamptz '2026-09-24 11:05:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'comprovante-quita-conta';
