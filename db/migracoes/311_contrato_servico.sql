-- 311_contrato_servico.sql
-- Contrato de PRESTAÇÃO DE SERVIÇOS pro nicho recorrente — e o pagamento anual
-- que o orçamento nunca gravou.
--
-- 1. `contrato_modelo.pedir_assinatura`
--
--    No nicho de eventos o contrato existe por NICHO: toda conta de eventos tem
--    contrato de locação, e é a assinatura que abre o financeiro. No recorrente o
--    contrato passou a existir em 23/09/2026, pedido do dono pra ZAQ (conta 3):
--    "copie o mesmo modelo que já roda na Prime ... com orçamento e contrato sem o
--    aditivo". Mas são OITO contas recorrentes, e ligar pelo nicho faria o contrato
--    aparecer de uma vez em clínica, seguros, construção e suplementos, com um texto
--    que ninguém dessas contas escreveu.
--
--    Então no recorrente é uma CHAVE POR CONTA, e ela nasce desligada — inclusive
--    na ZAQ. Quem liga é o dono, no card do contrato, depois de preencher os
--    "números da casa" (reajuste, aviso prévio, implantação...). Ligada com número
--    em branco, o contrato sairia pro cliente com `{regra.aviso_previo_dias}` escrito
--    no meio da cláusula — por isso a tela recusa ligar enquanto faltar número.
--
--    No nicho de eventos a coluna é ignorada: lá o contrato continua sendo do
--    nicho, como sempre foi.
--
-- 2. `orcamentos.pagamento_anual`
--
--    O botão "Pagamento anual (-15%)" do recorrente só existia na TELA: o
--    orçamento gravava a mensalidade já com o desconto e esquecia que era anual.
--    Reabrir a proposta trazia o botão desligado e as linhas com a mensalidade
--    cheia — e o próximo Salvar mudava o preço que o cliente tinha recebido. O
--    contrato precisa do dado: o anual é o ano inteiro À VISTA, com 15% de desconto
--    ("desconto só pagando à vista o ano todo", dono, 23/09/2026).
--
-- 3. `orcamentos.setup_liquido_centavos` e `mensal_liquido_centavos`
--
--    As duas pontas do dinheiro DEPOIS de todos os descontos (da linha, do anual e
--    do total), gravadas ao salvar. `setup_centavos`/`mensal_centavos` são o BRUTO
--    — e era do bruto que `fechar_orcamento` abria os títulos do recorrente: com
--    desconto, o cliente seria cobrado a mais do que aprovou. O contrato de
--    serviço precisa dizer o número que o financeiro vai cobrar, e os dois passam
--    a ler daqui. Nulo = orçamento salvo antes desta migração: segue o bruto, como
--    sempre seguiu, até ser salvo de novo.
--
-- 4. `orcamentos.dia_vencimento`
--
--    "Cliente escolhe a melhor data" (dono, 23/09/2026). No contrato de serviço
--    mensal o cliente escolhe o dia do mês na hora de assinar; ele fica gravado
--    aqui, entra no texto congelado do contrato e é o dia do título recorrente.
--    Nulo = não escolheu (anual à vista, ou orçamento de antes): vence como
--    sempre venceu, um mês depois do fechamento.
--
-- Aditivo e idempotente. Os defaults são `false`/nulo = o comportamento de hoje:
-- nenhuma conta muda de fluxo por causa desta migração, e nenhum orçamento já
-- salvo muda de valor.

alter table public.contrato_modelo
  add column if not exists pedir_assinatura boolean not null default false;

comment on column public.contrato_modelo.pedir_assinatura is
  'Só no nicho recorrente: true = a proposta aprovada vira contrato de prestação '
  'de serviço, e o financeiro abre na assinatura. No nicho de eventos o contrato '
  'é do nicho e esta coluna é ignorada.';

alter table public.orcamentos
  add column if not exists pagamento_anual boolean not null default false;

comment on column public.orcamentos.pagamento_anual is
  'Recorrente: o cliente escolheu o pagamento anual À VISTA (o ano inteiro, com 15% '
  'de desconto). `mensal_centavos` já vem com o desconto aplicado.';

alter table public.orcamentos
  add column if not exists setup_liquido_centavos bigint;
alter table public.orcamentos
  add column if not exists mensal_liquido_centavos bigint;

comment on column public.orcamentos.setup_liquido_centavos is
  'Recorrente: implantação depois de todos os descontos. Nulo = salvo antes da 311.';
comment on column public.orcamentos.mensal_liquido_centavos is
  'Recorrente: mensalidade depois de todos os descontos (linha, anual, total). '
  'É o valor do título recorrente. Nulo = salvo antes da 311.';

alter table public.orcamentos
  add column if not exists dia_vencimento smallint
    check (dia_vencimento is null or dia_vencimento between 1 and 28);

comment on column public.orcamentos.dia_vencimento is
  'Recorrente mensal: dia do mês escolhido pelo cliente ao assinar o contrato de '
  'serviço (1–28). Nulo = vence um mês depois do fechamento, como sempre.';

-- rollback:
--   alter table public.contrato_modelo drop column if exists pedir_assinatura;
--   alter table public.orcamentos drop column if exists pagamento_anual;
--   alter table public.orcamentos drop column if exists setup_liquido_centavos;
--   alter table public.orcamentos drop column if exists mensal_liquido_centavos;
--   alter table public.orcamentos drop column if exists dia_vencimento;
