-- 448_novidade_baixa_parcial_pagar.sql
-- O aviso da baixa parcial em Contas a pagar, seguindo a seção 5 do CLAUDE.md.
-- Pedido do dono em 29/09/2026 ("nos contas a pagar eu fiz um ajuste para
-- recebimento parcial de um lançamento — verifica como tá hoje e se é
-- possível fazer"), mockup aprovado em
-- docs/mockups/contas_pagar_baixa_parcial.html com as respostas: "data em
-- branco mesmo, e não cria abater no fornecedor".
--
-- O QUE MUDOU NA TELA. O painel de "dar baixa" de um título a pagar trocou o
-- campo solto "Multa e juros" por "Valor pago" — o mesmo desenho que Contas a
-- receber já tinha (finance/recebido_diferente.py, pedido 9 de 23/09/2026).
-- Pagar menos que o título abre um painel: "ainda falta pagar" (nasce uma
-- conta nova, com a DATA que você escolhe — em branco de propósito, porque só
-- você sabe quando combinou o resto com o fornecedor) ou "foi desconto
-- combinado". Pagar mais oferece só "foi multa e juros do atraso" — sem
-- "abater na próxima conta do fornecedor", que não foi pedido.
--
-- O PORTÃO: `empresa` (mesmo de 338_novidade_empresa_reorganizada.sql) — é
-- quem tem o módulo PJ que vê /painel/empresa#titulos.
--
-- PRA QUEM: dono e gestor. O vendedor não dá baixa em título (nem em pagar,
-- nem em receber pela aba Empresa) — mesma régua da 338.
--
-- QUEM RECEBE, conferido na produção em 29/09/2026 (só leitura, módulo PJ
-- ativo — mesma consulta da 338, com uma conta nova desde então):
--   3 Thompson · 7 João Pedro · 9 Zé do Arroz · 16 Danilo · 21 Maylson ·
--   23 Rawilson · 26 Katheley · 30 Paulo · 31 Juliana · 33 Pablo · 34 Manoel
--   (Prime) · 35 Louana · 37 Liberal · 39 Espaço Pelle Clínica Dermatológica ·
--   40 M.R. Rocha Assessoria de Imprensa
--
-- Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('empresa-baixa-parcial-pagar', 'novidade', 'empresa', '{dono,gestor}',
 'Dá pra pagar uma conta em partes',
 'A baixa de um título a pagar ganhou "Valor pago": se você pagou menos que o total, o que falta vira uma conta nova, com a data que você escolher.',
 '/painel/empresa',
 $txt$Antes, dar baixa numa conta a pagar só aceitava o valor cheio — o único ajuste era "Multa e juros", em cima do total. Não tinha onde registrar um pagamento parcial.

AGORA, o campo virou "Valor pago". Se você pagou menos que o título, um painel abre com duas opções:

- "ainda falta pagar" — nasce uma conta nova com o que sobrou. A data de vencimento dela vem em branco: é você quem escreve, do jeito que combinou com o fornecedor (não existe regra que adivinhe isso).
- "foi desconto combinado com o fornecedor" — funciona como já funcionava, sem criar conta nova.

Pagando mais que o título, a opção é "foi multa e juros do atraso" — igual a antes.

O que não mudou: dar baixa continua sendo de dono e gestor, e o valor lançado no caixa é sempre o que de fato saiu.$txt$,
 timestamptz '2026-09-29 21:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'empresa-baixa-parcial-pagar';
