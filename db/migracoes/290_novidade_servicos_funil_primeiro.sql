-- 290_novidade_servicos_funil_primeiro.sql
-- A aba de Serviços do nicho de eventos: o funil na frente, e a palavra "incluso".
--
-- O QUE MUDOU NA TELA:
--   * O FUNIL virou a primeira coisa da aba, com três abas (Precisa de mim, Com
--     o cliente, Fechadas), busca e filtro por vendedor. O editor de orçamento
--     abre em "Nova proposta" ou ao clicar numa linha.
--   * A DATA DA FESTA abre cada linha, no lugar da inicial do nome. Proposta sem
--     data aparece em coral.
--   * COBRAR × INCLUSO por linha, no lugar de escrever 100 no desconto. O resumo
--     separa o que é cobrado do que vem junto, e a folha do cliente imprime
--     "Incluso" com o valor de tabela.
--   * No CELULAR, uma barra fixa com o total e o botão de gerar.
--   * Cliente antes de Evento; contrato e aditivo foram pro fim da página.
--   * Dois avisos com o conserto ao lado: parcela sem vencimento e serviço sem
--     categoria.
--
-- POR QUE EXISTE. Lendo a aba com os dados da Prime (conta 34) em 18/09/2026:
--   * 27 propostas numa lista só, sem filtro e sem busca, QUINZE delas rascunho
--     e a mais velha de 19/08 — e o funil era o RODAPÉ de uma página de editor;
--   * 122 das 217 linhas de orçamento tinham "100% de desconto", e exatamente
--     UMA tinha desconto percentual de verdade. Não era desconto: era o vendedor
--     dizendo "vem junto no pacote" com a única ferramenta que a tela oferecia.
--     Por isso o resumo anunciava "Economia de R$ 14.850" numa proposta onde
--     ninguém descontou nada;
--   * no celular, abaixo de 820px, o total caía depois dos serviços e do plano
--     de pagamento: montava-se o orçamento inteiro sem ver o valor.
--
-- NENHUM VALOR MUDA. Cem por cento de desconto já zerava a linha e continua
-- zerando; o que muda é a tela parar de chamar aquilo de desconto.
--
-- PRA QUEM: dono, gestor e vendedor. É o vendedor que vive nesta tela — o funil
-- é a fila de trabalho dele e o "incluso" é o que ele faz em toda proposta.
--
-- O PORTÃO: `eventos`. A ZAQ e as demais contas de serviço recorrente não foram
-- tocadas: a ordem nova da página vale só no nicho de eventos (seção 6).
--
-- CONTAS ALCANÇADAS: 34 (MANOEL SOARES) e 35 (Louana vanessa cardoso Santos
-- costa) — as duas de nicho `eventos` em 19/09/2026.
--
-- Aditivo e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('servicos-funil-primeiro', 'novidade', 'eventos', '{dono,gestor,vendedor}',
 'A aba de Serviços abre no funil, e agora existe a palavra "incluso"',
 'O funil de propostas passou a ser a primeira coisa da tela, dividido em Precisa de mim, Com o cliente e Fechadas, com busca e filtro por vendedor — e o que vem junto no pacote deixou de ser cadastrado como "100% de desconto" para virar um botão "Incluso", que sai assim na folha do cliente.',
 '/painel/servicos',
 $txt$A aba de Serviços era uma página de editor com o funil no rodapé. Agora é o contrário.

O FUNIL NA FRENTE, EM TRÊS ABAS

Ao abrir, a primeira coisa é a lista de propostas, dividida em:

• Precisa de mim — tem botão verde ou aviso vermelho: é trabalho seu hoje.
• Com o cliente — está na mão dele, esperando resposta.
• Fechadas — negócio fechado e nada pendente.

Cada aba já vem com o número do lado, então dá pra saber quanto falta antes de clicar. Tem busca por nome, número ou data, e quem é dono ou gestor pode filtrar por vendedor.

O editor de orçamento abre quando você aperta "Nova proposta" ou clica numa linha. Pra voltar, o botão "← Funil" no topo dele.

A DATA ABRE A LINHA

No lugar da inicial do nome, cada linha começa com o dia e o mês da festa. É o que você procura quando bate o olho na lista. Proposta sem data marcada aparece em vermelho — é o tipo de coisa que passava despercebida no meio das outras.

"INCLUSO" DEIXOU DE SER 100% DE DESCONTO

Em cada serviço da proposta agora tem um par: Cobrar ou Incluso.

Quando você marca Incluso, o item continua aparecendo pro cliente — com o valor de tabela ao lado — e não entra no total. Na folha que ele recebe sai a palavra "Incluso", em vez de um valor riscado e "R$ 0,00".

Antes, a única forma de dizer isso era escrever 100 no campo de desconto. Funcionava pra conta, mas a tela então somava aquilo como desconto e anunciava uma "economia" que ninguém tinha dado. O resumo agora mostra as duas coisas separadas: quanto é cobrado, e quanto vem junto no pacote.

Suas propostas antigas já entendem sozinhas: onde estava 100% de desconto, aparece "Incluso". Nenhum valor mudou.

NO CELULAR, O TOTAL NÃO SOME MAIS

Enquanto você monta o orçamento no telefone, uma barra fixa embaixo mostra o total e os botões de salvar e gerar. Antes o total ficava no fim da página, depois dos serviços e do plano de pagamento.

DOIS AVISOS QUE DIZEM ONDE ARRUMAR

• Parcela sem data de vencimento: título sem data nasce sem cobrança e não entra no fluxo de caixa. O aviso tem o botão que abre o gerador de parcelas.
• Serviço sem categoria: a folha do cliente só mostra o subtotal por categoria quando todos os itens têm uma. O aviso leva direto pro catálogo.

E MAIS DUAS MUDANÇAS DE ORDEM

Cliente agora vem antes de Evento — na conversa a gente pergunta quem é antes de quando é. E os cartões de Contrato e Termo aditivo foram pro fim da página: são configuração, escrevem-se uma vez, e estavam na frente do trabalho de todo dia.$txt$,
 timestamptz '2026-09-19 03:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'servicos-funil-primeiro';
