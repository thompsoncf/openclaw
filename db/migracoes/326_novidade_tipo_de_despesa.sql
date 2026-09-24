-- 326_novidade_tipo_de_despesa.sql
-- Fixa, eventual e investimento viram o TIPO da despesa, separado do centro de
-- custo — e a aba Empresa ganha o quadro "Despesas por tipo".
--
-- O QUE MUDOU:
--   * Campo novo "Tipo de despesa" (Fixa · Eventual · Investimento) na conta a
--     pagar (três botões) e em cada despesa da empresa no Financeiro. A memória
--     do fornecedor lembra o tipo, e a baixa leva o tipo pro caixa.
--   * Aba Empresa, quadro "Despesas por tipo": os dois últimos meses, quanto foi
--     fixo, eventual e investimento, quanto do valor já tem tipo, e a lista do
--     que falta classificar — um toque e grava, com sugestão quando o mesmo
--     gasto já teve tipo antes.
--   * Financeiro, card Despesas: "Empresa por tipo" no lugar do "por centro" da
--     entrega 4.
--   * WhatsApp: "foi investimento" passa a ser o tipo, e não o centro.
--   * As despesas que estavam com os centros DESPESA FIXA, DESPESA EVENTUAL ou
--     INVESTIMENTO ganharam o tipo correspondente (51 na Prime). O centro delas
--     ficou como estava; nenhum centro foi criado, mudado ou apagado.
--
-- POR QUE. Correção do dono em 24/09/2026: "fixa, eventual, investimento não é
-- centro de custo, tem que ser separado para ter maior clareza no relatório do
-- gestor". Mockup aprovado no mesmo dia (docs/mockups/financeiro_tipo_de_despesa.html).
--
-- PRA QUEM: dono e gestor — donos do Financeiro e da aba Empresa.
--
-- O PORTÃO: `todos` — tipo de despesa não é assunto de nicho.
--
-- CONTAS ALCANÇADAS com despesa de empresa (leitura em produção, 24/09/2026):
--   todas as contas com o módulo Empresa; na Prime (34), 51 despesas já
--   entram com tipo.
--
-- Aditivo e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('tipo-de-despesa', 'novidade', 'todos', '{dono,gestor}',
 'Fixa, eventual ou investimento: o tipo da despesa agora é separado',
 'Cada despesa da empresa diz o tipo — fixa, eventual ou investimento — separado do centro de custo, e a aba Empresa mostra quanto foi de cada um no mês.',
 '/painel/empresa#despesas-por-tipo',
 $txt$Fixa, eventual e investimento não são centro de custo — e agora têm campo próprio.

Toda despesa responde três perguntas, cada uma no seu lugar:
• Plano de contas: o que é o gasto (ex.: Diaristas).
• Centro de custo: de qual área (ex.: Buffet).
• Tipo de despesa: fixa, eventual ou investimento.

ONDE ESCOLHER

Na conta a pagar, três botões: Fixa, Eventual, Investimento. No Financeiro, cada despesa da empresa tem o tipo ao lado do centro. E pelo WhatsApp, é só dizer "foi investimento" ou "é conta fixa". O Zaq lembra o tipo do fornecedor da última vez.

O QUADRO DO GESTOR

Na aba Empresa, "Despesas por tipo" mostra os dois últimos meses: quanto foi fixo, eventual e investimento, e quanto ainda está sem tipo. Embaixo, a lista do que falta classificar — um toque em cada linha e pronto. Quando o mesmo gasto já teve tipo antes, ele aparece sugerido.

O QUE JÁ ESTAVA CLASSIFICADO

As despesas que estavam nos centros "DESPESA FIXA", "DESPESA EVENTUAL" e "INVESTIMENTO" já entraram com o tipo certo. O centro delas continua como estava — nada foi apagado. Se quiser, você pode desativar esses três centros na lista de centros de custo e passar a usar os das áreas.$txt$,
 timestamptz '2026-09-24 21:00:00+00')
on conflict (chave) do nothing;

-- OS DOIS AVISOS QUE FICARAM ERRADOS. O da entrega 4 (321) dizia que o card
-- mostra a despesa "por centro de custo (… despesa fixa, eventual,
-- investimento…)", e o da entrega 3 (319), que "foi investimento" vira um dos
-- centros. Os dois já estão publicados — no painel e no site — e ensinam
-- justamente a mistura que esta correção desfaz. Só o trecho errado é trocado
-- (replace de texto exato: rodar de novo não muda nada, e texto já editado à mão
-- não é tocado).
update public.novidades
   set resumo = replace(resumo,
         'No Financeiro, a despesa da empresa aparece por centro de custo.',
         'No Financeiro, a despesa da empresa aparece por tipo: fixa, eventual e investimento.'),
       corpo = replace(corpo,
         'O card Despesas passa a mostrar a despesa da empresa por centro de custo (os centros que você já tem: despesa fixa, eventual, investimento…). O que foi lançado sem centro aparece em "Sem centro", em âmbar — é o que falta classificar.',
         'O card Despesas passa a mostrar a despesa da empresa por tipo: fixa, eventual e investimento. O que ainda não tem tipo aparece em "Sem tipo", em âmbar — é o que falta classificar.')
 where chave = 'planejamento-da-semana';

update public.novidades
   set corpo = replace(corpo,
         'E quando você disser "foi investimento" ou "foi despesa fixa", o Zaq já sabe qual dos seus centros é.',
         'E quando você disser "foi investimento" ou "é conta fixa", o Zaq marca o tipo da despesa — que é separado do centro de custo.')
 where chave = 'comprovante-quita-conta';

-- rollback:
--   delete from public.novidades where chave = 'tipo-de-despesa';
--   (os dois textos corrigidos acima não voltam: voltar seria republicar o erro)
