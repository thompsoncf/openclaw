-- 310_novidade_servicos_funil_recorrente.sql
-- A tela de Serviços do recorrente passou a ter a forma da tela da Prime.
--
-- O QUE MUDOU NA TELA:
--   * O funil vem PRIMEIRO, com as três abas (Precisa de mim / Com o cliente /
--     Fechadas), busca por nome ou nº e filtro por vendedor.
--   * O editor abre em "+ Nova proposta" ou ao clicar numa linha, e "← Funil"
--     volta. Antes ele ficava sempre aberto e o funil era o rodapé.
--   * Cliente: busca na Base, como na Prime, com o "cadastrar um cliente novo".
--   * Meus serviços: a lista mostra só o que está NESTA proposta. O resto do
--     catálogo fica atrás da busca ou do "ver os N serviços em ordem alfabética".
--     O interruptor por linha e os botões "Marcar todos" / "Limpar seleção" saíram.
--   * A linha tem uma caixa por coluna (setup, mensal, desconto) com o rótulo em
--     cima do número e ponto de milhar. O custo entra como coluna própria no Modo
--     margem.
--   * No celular, a barra fixa com o total do 1º ano e os botões Salvar e Gerar.
--
-- POR QUE. Pedido do dono em 23/09/2026, olhando a tela da ZAQ (conta 3): "deixa
-- o mesmo modelo que já tem na Prime eventos do layout da página, as ordens,
-- botões e tudo". A grade antiga tinha 7 colunas pra 6 caixas — a coluna Custo
-- fica escondida fora do Modo margem, mas o título "Custo/Margem" continuava na
-- tela, então tudo escorregava uma casa: "MENSAL 1200" aparecia como "MENSAL 12(",
-- o desconto invadia o ✎ e o 🗑, e o nome quebrava no meio da palavra.
--
-- É 'mudanca' e não 'novidade': botões saíram ("Marcar todos", o interruptor) e
-- o editor não abre mais sozinho — quem procurar do jeito antigo não vai achar.
--
-- PRA QUEM: dono, gestor e vendedor. É a tela de trabalho de quem vende.
--
-- O PORTÃO: `recorrente` — a tela da Prime (eventos) não mudou.
--
-- CONTAS ALCANÇADAS (criadas antes de 23/09/2026, leitura em produção):
--   3 ZAQ - SISTEMAS IAs · 16 SUPER FIT · 21 MGB SOLUTIONS · 23 RAMO CAPITAL ·
--   30 PC CONTABILIDADE · 33 PX2 EMPRRENDIMENTOS ·
--   37 LIBERAL NETO CORRETAGEM DE SEGUROS · 39 ESPACO PELLE CLINICA DERMATOLOGICA
--   Só a ZAQ tem orçamento nessa tela hoje (15); as outras sete têm zero.
--
-- Aditivo e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('servicos-funil-recorrente', 'mudanca', 'recorrente', '{dono,gestor,vendedor}',
 'A tela de Serviços ficou organizada',
 'A tela de propostas de serviço foi reorganizada: o funil vem primeiro, com abas e busca, e cada proposta abre num editor que mostra só os serviços dela, com setup, mensalidade e desconto em colunas alinhadas.',
 '/painel/servicos',
 $txt$A tela de Serviços mudou de ordem e ficou mais limpa.

O QUE ESTAVA ERRADO

A lista de serviços mostrava o catálogo inteiro, com um interruptor em cada linha, e as colunas estavam desalinhadas: o valor da mensalidade aparecia cortado ("MENSAL 12(" no lugar de 1.200), o desconto passava por cima dos botões de editar e excluir, e o nome do serviço quebrava no meio da palavra.

COMO FICOU

• O funil vem primeiro. As propostas ficam separadas em três abas — Precisa de mim, Com o cliente e Fechadas — com busca por nome ou número.
• Para montar uma proposta, clique em "+ Nova proposta". Para mexer numa que já existe, clique nela. O botão "← Funil" volta para a lista.
• O cliente pode ser buscado direto da sua Base de prospecção, sem digitar de novo.
• Em "Meus serviços" aparecem só os serviços desta proposta. Para acrescentar, use a busca logo acima ou "ver os serviços em ordem alfabética". Para tirar, o 🗑 da linha.
• Cada serviço tem as caixas de setup, mensalidade e desconto alinhadas, com o valor inteiro à vista. O custo aparece quando você liga o Modo margem.
• No celular, o total do 1º ano fica fixo no rodapé, com os botões Salvar e Gerar.

O QUE SAIU

Os botões "Marcar todos" e "Limpar seleção", e o interruptor em cada linha. No lugar deles: a busca para adicionar e o 🗑 para tirar.

O QUE NÃO MUDOU

Os valores, o desconto por item e no total, o pagamento anual, os parâmetros e o escopo pela IA calculam exatamente como antes. As propostas que já existem abrem com os mesmos serviços e os mesmos números.$txt$,
 timestamptz '2026-09-23 18:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'servicos-funil-recorrente';
