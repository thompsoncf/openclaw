-- 203_aditivo_modelo.sql
-- O texto do termo aditivo passa a ser do DONO, como o do contrato já é.
--
-- POR QUE, EM UMA FRASE DO PRÓPRIO DONO (05/09/2026)
--   "não é interessante também deixar o aditivo igual o contrato, podendo
--    alterar alguma coisa nas cláusulas? veja lá na parte de serviços em
--    contrato e veja como está, e a gente replica."
--
-- A incoerência que o pedido dele expôs: o contrato é escrito por ele, no card
-- de Serviços, desde a 160. O aditivo — que altera esse mesmo contrato, no mesmo
-- negócio, para o mesmo cliente — saía com texto escrito dentro do
-- `finance/aditivo.py`. Dois documentos, duas vozes.
--
-- CINCO TEXTOS FIXOS, NÃO LISTA LIVRE, e essa é a diferença em relação ao
-- contrato. Lá o dono acrescenta e remove cláusulas à vontade porque o documento
-- é dele do começo ao fim. Aqui cada texto CASA COM UM BLOCO do formulário
-- (data, horário, convidados, serviços, valor): uma cláusula a mais não teria
-- bloco que a preenchesse, e uma a menos deixaria um bloco marcado sem texto
-- para sair. Por isso o jsonb é um objeto com chaves conhecidas, e não um array.
--
-- Os números continuam vindo de CAMPO, nunca digitados dentro do texto — é a
-- mesma regra que fez o `finance/contrato` existir (o contrato dizia R$ 600 na
-- hora extra enquanto o catálogo dizia R$ 620, medido na Prime em 16/08/2026).
-- Lá era pra não divergir do catálogo; aqui é pra não divergir da agenda.
--
-- Quem nunca abrir este card não vê diferença: sem linha aqui, vale o
-- `finance.aditivo.MODELO_PADRAO`, que é palavra por palavra o texto que já está
-- no ar — tirado dos quatro aditivos reais que o dono mandou em 04/09.
--
-- Aditiva e idempotente.
create table if not exists public.aditivo_modelo (
    conta_id       bigint primary key references public.contas(id) on delete cascade,
    -- {data, horario, convidados, servicos, valor, disposicoes, fecho}; cada um
    -- dos cinco primeiros é {titulo, corpo} — e `convidados` leva também
    -- `titulo_reduz`, porque os aditivos reais escrevem "ACRÉSCIMO DE
    -- CONVIDADOS" quando sobe e o oposto quando desce. Documento que anuncia
    -- acréscimo e reduz confunde quem lê depois.
    textos         jsonb       not null default '{}'::jsonb,
    atualizado_em  timestamptz not null default now(),
    atualizado_por text        not null default ''
);

-- rollback:
--   drop table if exists public.aditivo_modelo;
