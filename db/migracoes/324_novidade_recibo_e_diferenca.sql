-- 324_novidade_recibo_e_diferenca.sql
-- O recibo nasce de um botão depois da baixa, e a baixa de conta a receber
-- pergunta quanto entrou.
--
-- O QUE MUDOU:
--   * Recibo (pedido 1): ao dar baixa numa conta a RECEBER, aparece "📄 gerar
--     recibo". O recibo tem número por empresa e por ano (0001/2026), link
--     próprio pra abrir e imprimir, e "mandar pro cliente" pelo WhatsApp da
--     empresa quando a conta tem conversa no Zaq. As contas já recebidas ganham
--     o mesmo botão na lista dos baixados. Texto aprovado pelo dono em
--     23/09/2026.
--   * Valor diferente (pedido 9): a baixa de conta a receber pergunta o "valor
--     recebido". Entrou a menos: "continua devendo" (nasce uma conta com o que
--     falta, no mesmo vencimento) ou "foi desconto". Entrou a mais: "abate da
--     parcela" que o gestor escolher, ou "foi multa e juros". Nada é decidido
--     sozinho.
--   * Crédito que já tinha entrado: parcela baixada por mais do que o
--     orçamento combinava mostra a diferença na lista dos baixados, com o
--     mesmo "abater".
--
-- POR QUE. Entrega 5 do plano aprovado pelo dono em 23/09/2026. Regra da
-- diferença dada por ele no mesmo dia: "continua devendo quando o valor não
-- fechar, e quando passar fica de crédito e [abate de outra] parcela — sempre
-- pergunta pro gestor". O caso que motivou: orçamento nº 23 da Prime, R$ 75,00
-- a mais no sinal, que não abateram nada.
--
-- PRA QUEM: dono e gestor — são eles que dão baixa na aba Empresa.
--
-- O PORTÃO: `todos` — recibo e conta a receber não são assunto de nicho; o
-- texto do recibo é que segue o que a conta vende (evento com data,
-- mensalidade com o mês).
--
-- CONTAS ALCANÇADAS que têm conta a receber (leitura em produção, 23/09/2026):
--   3 ZAQ - SISTEMAS IAs · 9 Ceaseiro · 34 PRIME EVENTOS
--
-- Aditivo e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('recibo-e-valor-diferente', 'novidade', 'todos', '{dono,gestor}',
 'Recibo com um botão, e a baixa que pergunta quanto entrou',
 'Deu baixa numa conta a receber? Gere o recibo numerado e mande pro cliente. E se entrou um valor diferente da parcela, o Zaq pergunta o que fazer com a diferença.',
 '/painel/empresa#titulos',
 $txt$Duas mudanças na baixa das contas a receber, na aba Empresa.

O RECIBO

Depois de confirmar a baixa, aparece o botão "📄 gerar recibo". O recibo sai numerado (0001/2026, 0002/2026…), com o nome e o CPF do cliente (quando estão no cadastro), o valor por extenso, a que parcela se refere e quanto ainda falta.

Você pode abrir e imprimir, copiar o link, ou mandar direto pro WhatsApp do cliente — pelo número da empresa, quando a conta tem conversa no Zaq.

Esqueceu de gerar na hora? As contas já recebidas têm o mesmo botão na lista "Já baixados". Gerar de novo mantém o número: se você completou o CPF depois, o recibo sai atualizado.

QUANDO ENTRA UM VALOR DIFERENTE

A baixa agora pergunta o "valor recebido". Se for igual à parcela, nada muda. Se for diferente, o Zaq pergunta — e só faz o que você escolher:

• Entrou a menos: "continua devendo" cria uma conta com o que falta, no mesmo vencimento. Ou "foi desconto".
• Entrou a mais: o crédito abate da parcela que você escolher (se passar dela, abate das seguintes). Ou "foi multa e juros".

Toda parcela mexida mostra o motivo na linha, e o valor combinado de antes fica guardado.

Se alguma parcela já foi baixada por mais do que o combinado no orçamento, ela aparece na lista dos baixados com a opção de abater a diferença.$txt$,
 timestamptz '2026-09-24 11:15:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'recibo-e-valor-diferente';
