-- 318_novidade_classificacao_na_conta.sql
-- A conta a pagar e a receber ganha categoria, plano de contas e centro de custo.
--
-- O QUE MUDOU NA TELA:
--   * Empresa → "Títulos a pagar e receber" → o formulário de nova conta ganhou
--     uma linha com Categoria, Plano de contas e Centro de custo. Os três são
--     opcionais.
--   * Ao digitar o fornecedor, os campos vazios se preenchem com o que ESTE
--     fornecedor teve da última vez, pro MESMO tipo de conta — em verde, com a
--     frase "Preenchido como da última vez: …". O que a pessoa já escolheu não é
--     trocado.
--   * O "editar ✎" de cada conta ganhou os mesmos três campos — é por ali que as
--     contas que já existiam recebem a classificação antes de serem pagas.
--   * A linha da conta mostra a classificação quando ela existe
--     ("5.1.10 Serviços Terceirizados · DESPESA FIXA").
--   * Na baixa, os três vão junto pro lançamento do caixa; a próxima ocorrência
--     da conta que repete já nasce com eles.
--
-- POR QUE. Pedido do dono em 23/09/2026: "no lançamento do contas a pagar já
-- colocar o centro de custo e plano de contas e categoria". Medido na Prime no
-- mesmo dia: das 20 despesas de setembro que nasceram de conta a pagar, 20 tinham
-- plano (classificadas à mão depois, no Financeiro) e 1 tinha centro.
--
-- Nenhum centro de custo foi criado nem alterado (regra do dono, mesmo dia): a
-- tela oferece os que a conta já tem, com os nomes e a ordem dela.
--
-- PRA QUEM: dono e gestor. O vendedor não abre o financeiro da empresa.
--
-- O PORTÃO: `todos` — conta a pagar não é assunto de nicho; quem tem o módulo
-- Empresa tem a tela.
--
-- CONTAS ALCANÇADAS que já usam títulos (leitura em produção, 23/09/2026):
--   3 ZAQ - SISTEMAS IAs · 9 Ceaseiro · 16 SUPER FIT · 30 PC CONTABILIDADE ·
--   34 PRIME EVENTOS
--
-- Aditivo e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('contas-classificacao-na-conta', 'novidade', 'todos', '{dono,gestor}',
 'Centro de custo e plano de contas já na conta a pagar',
 'Ao lançar uma conta a pagar ou a receber, dá pra escolher categoria, plano de contas e centro de custo — e eles vão sozinhos pro caixa quando a conta é paga.',
 '/painel/empresa#titulos',
 $txt$A conta já nasce classificada, e a classificação vai junto quando ela é paga.

ONDE

Em Empresa, "Títulos a pagar e receber". O formulário de nova conta tem agora uma linha com Categoria, Plano de contas e Centro de custo. Os três são opcionais: se não quiser usar, deixe como está.

O "editar ✎" de cada conta tem os mesmos três campos. É por ali que as contas que já estavam cadastradas ganham centro e plano antes de serem pagas.

A MEMÓRIA DO FORNECEDOR

Quando você digita o fornecedor, o Zaq olha como a última conta DESTE fornecedor, do MESMO tipo, foi classificada, e preenche os campos que estiverem vazios — em verde, com a frase "Preenchido como da última vez". Confira e siga.

"Do mesmo tipo" importa: se o mesmo fornecedor tem quinzena e diária de evento, a quinzena lembra a quinzena, não a diária. E o que você já escolheu nunca é trocado pela lembrança.

NA BAIXA

Quando você der baixa, os três vão pro lançamento do caixa — você não precisa classificar de novo no Financeiro. Se a conta repete, a do mês seguinte já nasce classificada.

O QUE NÃO MUDOU

Os seus centros de custo continuam exatamente como estão: nenhum foi criado, renomeado nem alterado.$txt$,
 timestamptz '2026-09-24 11:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'contas-classificacao-na-conta';
