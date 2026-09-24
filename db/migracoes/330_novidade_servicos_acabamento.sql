-- 330_novidade_servicos_acabamento.sql
-- A montagem da proposta ficou alinhada: pílulas inteiras, caixas mais baixas e a
-- escolha do serviço mais clara.
--
-- O QUE MUDOU NA TELA (os dois nichos; a estrutura é a mesma de antes):
--   * "Cobrar | Incluso" inteiro — o texto saía cortado pela metade.
--   * As caixas de Qtd, Vr. unit., Desconto e Subtotal com o rótulo em cima e a
--     caixa com a metade da altura; uma linha fina entre um serviço e outro.
--   * O nome do serviço sem o extra-negrito; nome longo corta com "…".
--   * EVENTO: a linha cobrada ganha uma faixa verde; no incluso, os números ficam
--     em cinza e o subtotal mostra "Incluso" com o valor de tabela riscado.
--   * A lista "ver todos": interruptor de selecionar no lugar (virava uma bola
--     deformada), ✎ e ⊘ alinhados, valor em R$ com centavos e o selo "na proposta".
--   * A busca "pra adicionar" mostra ícone, descrição, "+ adicionar" — e também o
--     que já está na proposta, marcado "✓ na proposta" (antes ele sumia da busca).
--
-- POR QUE. O dono, olhando a proposta nº 39 da Prime: "bora ajeitar essas
-- pílulas, melhorar o layout e ajustar os campos e linhas entre serviços". O
-- defeito de fundo era o `button{min-height:48px}` global do painel esticando os
-- botões pequenos. Mockup aprovado: docs/mockups/prime_servicos_linhas.html (v2,
-- depois de "não gostei, tá mexendo muito na estrutura" na v1).
--
-- É 'novidade': nada saiu nem mudou de lugar.
--
-- PRA QUEM: dono, gestor e vendedor. O PORTÃO: `servico`.
--
-- CONTAS ALCANÇADAS (as mesmas da 313 e da 327):
--   3 ZAQ - SISTEMAS IAs · 16 SUPER FIT · 21 MGB SOLUTIONS · 23 RAMO CAPITAL ·
--   30 PC CONTABILIDADE · 33 PX2 EMPRRENDIMENTOS · 34 PRIME EVENTOS ·
--   35 DOCE MELL · 37 LIBERAL NETO CORRETAGEM DE SEGUROS ·
--   39 ESPACO PELLE CLINICA DERMATOLOGICA
--
-- Aditivo e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('servicos-acabamento-linhas', 'novidade', 'servico', '{dono,gestor,vendedor}',
 'Montagem da proposta mais alinhada',
 'A montagem da proposta ficou mais limpa: botões inteiros, campos alinhados, uma divisão clara entre os serviços e a busca mostrando o que já está na proposta.',
 '/painel/servicos',
 $txt$A tela onde você monta a proposta continua igual no lugar das coisas — só ficou mais fácil de ler.

O QUE MUDOU

• O "Cobrar | Incluso" aparece inteiro (o texto saía cortado).
• As caixas de quantidade, valor, desconto e subtotal ficaram alinhadas, com o nome do campo em cima, e há uma linha clara entre um serviço e outro.
• Quem vende evento: o serviço cobrado ganha uma faixa verde à esquerda; os inclusos ficam em cinza, com o valor de tabela riscado embaixo do "Incluso".

NA HORA DE ESCOLHER O SERVIÇO

• Na lista "ver todos", o botão de selecionar voltou ao lugar, os botões de editar e inativar ficaram alinhados, o valor aparece em reais e o que já está na proposta tem o selo "na proposta".
• A busca mostra o ícone e a descrição de cada serviço, com "+ adicionar" — e o que já está na proposta aparece marcado, em vez de sumir.

As contas, a ordem dos serviços e a folha que o cliente recebe não mudaram.$txt$,
 timestamptz '2026-09-24 18:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'servicos-acabamento-linhas';
