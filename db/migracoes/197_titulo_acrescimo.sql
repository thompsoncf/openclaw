-- 197_titulo_acrescimo.sql
-- O que o ATRASO custou: multa e juros do boleto pago depois do vencimento.
-- (E o contrário: o desconto de quem pagou adiantado.)
--
-- POR QUE. O "dar baixa" não perguntava nada — nem valor, nem data do pagamento.
-- Lançava o valor de face na data de hoje e fechava. Aconteceu com o dono em
-- 04/09/2026: o IPTU 2026 3/6 venceu 31/08 e foi baixado em 04/09, quatro dias
-- depois, com lançamento de R$ 1.160,85 — exatamente a face. Se o boleto veio
-- corrigido, a diferença não existe em lugar nenhum: nem no caixa, nem no DRE.
--
-- E havia um segundo buraco, maior: `pagamento_serve_pro_titulo` exigia valor
-- IDÊNTICO pra ligar um pagamento do extrato a uma conta ("O valor do pagamento
-- não bate com o da conta"). Então o débito de R$ 2.258,67 que vai cair no OFX
-- nunca fecharia a ZARB de R$ 2.200,00, vencida há 20 dias. Sobravam dois
-- caminhos ruins: dar baixa (lançando a despesa uma SEGUNDA vez, em cima da que
-- veio do extrato) ou editar o valor da conta (apagando quanto se devia).
--
-- UM CAMPO SÓ, COM SINAL, e não `multa_centavos` + `juros_centavos`. O boleto
-- atualizado imprime a soma, e quem digita copia o que está no papel — dois
-- campos seriam duas chances de errar a divisão de um número que ninguém confere
-- separado. Positivo é multa+juros; NEGATIVO é desconto de quem pagou antes do
-- vencimento, que é caso real de boleto com desconto por antecipação. A
-- sugestão da tela usa a regra da própria casa (multa 2% + juros de mora 1% ao
-- mês, cláusula 3.4 do contrato da Prime, em finance/contrato.py), mas é só
-- sugestão: o campo é editável e o boleto é quem manda.
--
-- O SEGUNDO LANÇAMENTO. `lancamento_id` é a baixa do principal; este aqui é o
-- acréscimo, que vai pra conta própria do plano (6.1.01 Juros e Multas, ou
-- 1.2.02 Receitas Financeiras, conforme o lado e o sinal) — as duas já existem
-- desde a migração 132 e estavam vazias por falta de porta. Sem separar, o juros
-- viraria custo de fornecedor no DRE e a pergunta "quanto paguei de juros esse
-- ano?" continuaria sem resposta.
--
-- Fica NULO na conciliação, e isso é de propósito: ali o dinheiro já entrou no
-- caixa como UMA linha do extrato, com o valor cheio que o banco debitou.
-- Quebrá-la em duas seria reescrever dado vindo do banco. O título guarda o
-- acréscimo pro registro; o lançamento continua sendo o que o banco disse.
--
-- Aditiva e idempotente. Título antigo fica com acréscimo zero, que é o que ele
-- sempre foi.

alter table public.titulos
    add column if not exists acrescimo_centavos int not null default 0,
    add column if not exists lancamento_acrescimo_id bigint
        references public.lancamentos(id) on delete set null;

comment on column public.titulos.acrescimo_centavos is
  'O que o atraso custou: POSITIVO = multa + juros; NEGATIVO = desconto por '
  'antecipação. Zero em conta paga no prazo — ver a migração 197.';
comment on column public.titulos.lancamento_acrescimo_id is
  'O lançamento do acréscimo, separado do principal pra que o DRE não conte '
  'juros como custo de fornecedor. Nulo na conciliação, onde o extrato já '
  'trouxe o valor cheio numa linha só.';
