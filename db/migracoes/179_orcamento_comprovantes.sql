-- O COMPROVANTE DE PAGAMENTO, amarrado à PARCELA.
--
-- Um orçamento não tem um pagamento: tem o sinal e mais N parcelas. Guardar o
-- arquivo no orçamento deixaria "qual dos sete?" sem resposta — então a chave é
-- (orçamento, parcela), o mesmo `parcela_idx` que `fechar_orcamento` numera e que
-- `titulos` já usa pra saber qual título recebe baixa. O sinal é o índice 0
-- quando existe (ver vendas.indice_do_sinal).
--
-- GUARDA O CAMINHO, NÃO A URL. O arquivo vive num bucket PRIVADO: comprovante
-- bancário tem nome do cliente, banco, valor e às vezes CPF, e o bucket público
-- que serve foto de produto entregaria isso a qualquer um com o link — que se
-- encaminha, se cola em grupo, se indexa. Quem entrega o arquivo é uma rota
-- nossa, que confere sessão e conta antes.
--
-- UM POR PARCELA, SUBSTITUÍVEL. Anexou o errado, anexa de novo e o anterior sai.
-- Histórico de comprovante trocado é arquivo morto: ninguém vai auditar a versão
-- que estava errada.
create table if not exists public.orcamento_comprovantes (
    id            bigserial primary key,
    conta_id      bigint      not null,
    orcamento_id  bigint      not null,
    parcela_idx   int         not null,
    caminho       text        not null,          -- objeto no bucket privado
    nome          text        not null default '',   -- como o arquivo se chamava
    tipo          text        not null default '',   -- content-type
    bytes         bigint      not null default 0,
    por           text        not null default '',   -- membro_id, ou 'dono'
    criado_em     timestamptz not null default now()
);

create unique index if not exists uq_orc_comprovante
    on public.orcamento_comprovantes (orcamento_id, parcela_idx);
create index if not exists idx_orc_comprovante_conta
    on public.orcamento_comprovantes (conta_id, orcamento_id);
