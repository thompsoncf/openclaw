-- 314_novidade_contas_atrasadas.sql
-- A aba Empresa ganhou a pílula "⚠️ Atrasadas" nos títulos.
--
-- O QUE MUDOU NA TELA:
--   * Empresa → "Títulos a pagar e receber": ao lado de "Esperando liberação" e
--     "A receber" aparece agora, em primeiro lugar e em coral, a pílula
--     "⚠️ Atrasadas", com quantas contas A PAGAR já venceram.
--   * Clicando nela, a lista mostra só essas contas — de qualquer bloco. O
--     cabeçalho de cada bloco troca o total pelo total atrasado, pra não anunciar
--     um número que não bate com o que está na tela.
--   * As outras pílulas continuam exatamente como eram, e "Tudo" continua
--     somando tudo.
--
-- POR QUE. Pedido do dono em 23/09/2026: "dentro da aba empresas tem 3 card lá,
-- só falta esse". Medido na Prime (conta 34) no mesmo dia: das 13 contas a pagar
-- em aberto, 7 já tinham vencido — R$ 15.671,54, uma delas havia 18 dias — e
-- nenhuma se destacava, porque estavam espalhadas pelos blocos com a data em
-- âmbar no meio da meta-linha.
--
-- (De quebra o número explicou por que ele via TRÊS pílulas e não quatro: a
-- "✅ Liberadas — pode pagar" está zerada, e pílula sem conta não é desenhada.)
--
-- PRA QUEM: dono e gestor. O vendedor não abre o financeiro da empresa.
--
-- O PORTÃO: `todos` — contas a pagar não é assunto de nicho nenhum; quem tem o
-- módulo Empresa tem a tela, e quem não tem não vê aba nenhuma.
--
-- CONTAS ALCANÇADAS que já usam títulos (leitura em produção, 23/09/2026):
--   3 ZAQ - SISTEMAS IAs · 9 Ceaseiro · 16 SUPER FIT · 30 PC CONTABILIDADE ·
--   34 PRIME EVENTOS
--
-- Aditivo e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('contas-pagar-atrasadas', 'novidade', 'todos', '{dono,gestor}',
 'As contas atrasadas em um toque',
 'A aba Empresa ganhou um filtro que mostra, de uma vez, todas as contas a pagar que já venceram — com o total do que está em atraso.',
 '/painel/empresa#titulos',
 $txt$As contas vencidas pararam de se esconder no meio da lista.

O QUE FAZER

Abra Empresa e vá até "Títulos a pagar e receber". Na fileira de filtros, a primeira agora é "⚠️ Atrasadas", em vermelho, com o número de contas a pagar que já passaram do vencimento.

Clique nela e a lista mostra só essas contas, com o total em atraso. Clique em qualquer outro filtro pra voltar ao de sempre.

POR QUE ELA É DIFERENTE DAS OUTRAS

"Liberadas", "Esperando liberação" e "A receber" dizem em que gaveta a conta está — cada conta fica em uma só. "Atrasada" atravessa as três: uma conta vencida pode estar liberada ou esperando. Por isso ela não é uma gaveta nova; é uma lente por cima das que já existem, e nenhuma conta aparece duas vezes.

Ela também fica vermelha mesmo sem você clicar. As outras só ganham cor quando selecionadas, mas ter dinheiro vencido não depende de ninguém clicar pra ser verdade.

O QUE NÃO ENTRA

Só contas A PAGAR. Uma conta a receber vencida não é conta que você esqueceu de pagar — é cliente que te deve, e isso continua no card "Carteira de clientes", logo abaixo na mesma tela.$txt$,
 timestamptz '2026-09-23 23:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'contas-pagar-atrasadas';
