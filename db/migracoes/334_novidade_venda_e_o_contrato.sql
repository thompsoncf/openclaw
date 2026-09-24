-- 334_novidade_venda_e_o_contrato.sql
-- O cockpit, o Raio-X e o relatório de Contratos passaram a contar a venda pelo
-- CONTRATO ASSINADO. Mockup aprovado pelo dono em 24/09/2026
-- (docs/mockups/prime_cockpit_contratos.html).
--
-- O QUE MUDOU:
--   * na conta que já assinou contrato, o "fechado no período" do cockpit são os
--     contratos assinados no período, pela data e pelo valor da assinatura. Antes
--     eram os leads arrastados pro fechamento: na Prime, setembro dizia 8 negócios
--     e foram 10 contratos;
--   * o contrato feito direto pelo orçamento, SEM lead, passa a contar — no funil
--     (com a nota "inclui N contratos feitos sem lead"), no Placar e no Raio-X.
--     Na Prime eram 2 (Josinalva e Viviane), e o Raio-X dizia 8 onde eram 10;
--   * o vendedor do contrato é quem fez o orçamento; na falta, o vendedor do lead.
--     É o nome que o relatório de Contratos já mostrava;
--   * o relatório de Contratos abre com o período pela ASSINATURA, com a opção de
--     trocar pra criação.
--
-- PRA QUEM: dono e gestor (cockpit, Raio-X, relatório) e vendedor — o número de
-- contratos do Raio-X dele pode subir. Público `servico`: é onde há contrato e
-- equipe de venda.
--
-- Aditivo e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('venda-e-o-contrato-assinado', 'novidade', 'servico', '{dono,gestor,vendedor}',
 'A venda agora é o contrato assinado',
 'Cockpit, Raio-X e relatório de Contratos passaram a contar a venda pelo contrato assinado, incluindo os feitos sem lead.',
 '/cockpit/equipe',
 $txt$O cockpit, o Raio-X e o relatório de Contratos agora contam a mesma coisa: contrato assinado.

COMO ERA

O cockpit contava lead arrastado pro fechamento, na data em que alguém arrastou. O contrato feito direto pelo orçamento, sem lead no funil, não aparecia no cockpit nem no Raio-X. E o relatório de Contratos filtrava pela data em que o contrato foi criado, então um contrato criado num mês e assinado no outro caía no mês errado.

COMO FICOU

"Contratos assinados no período" é a soma dos contratos assinados, pela data e pelo valor da assinatura. O funil soma os contratos sem lead na etapa de fechamento e avisa quantos são.

O contrato conta pra quem fez o orçamento. Se o orçamento não diz quem fez, conta pro vendedor do lead. É o mesmo nome que já aparecia no relatório de Contratos.

NO RELATÓRIO

Contratos abre com o período pela Assinatura. Ao lado do período, "Criação" volta a mostrar pela data em que o contrato foi criado.

Vendedor: no seu Raio-X, os contratos que você fechou direto pelo orçamento agora também entram.$txt$,
 timestamptz '2026-09-24 04:30:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'venda-e-o-contrato-assinado';
