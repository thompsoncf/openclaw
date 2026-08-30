-- 191_clientes_fusao.sql
-- Trilha de auditoria das fusões de cadastro duplicado.
--
-- POR QUE. A migração 190 fechou a torneira: salvar de novo agora atualiza em
-- vez de cunhar um registro. Mas a poça continua — na Prime Eventos são 5
-- cadastros a mais, de 4 pessoas (Ana Clara em três, Victoria com um "s" a
-- mais, Gilvan e Ronaldo cada um com um segundo cadastro vazio marcado como
-- fornecedor). Juntar dois cadastros é a operação mais perigosa que existe
-- nesta base: mexe em dado de cliente, que pela regra 0 do CLAUDE.md não pode
-- se perder.
--
-- ENTÃO A FUSÃO NÃO APAGA NADA. O cadastro absorvido é ARQUIVADO (ativo=false),
-- como já faz o botão "Arquivar" da ficha. Os títulos, lançamentos e orçamentos
-- que apontavam pra ele passam a apontar pro que ficou. E esta tabela guarda,
-- em `movido`, exatamente quais linhas mudaram de dono e quais campos vazios
-- foram preenchidos — que é o que permite DESFAZER. Sem esse registro, a fusão
-- seria irreversível, e uma operação irreversível sobre cadastro de cliente não
-- devia existir.
--
-- `desfeita_em` marca a fusão revertida; a linha permanece (o histórico do que
-- se fez também é informação do cliente).
create table if not exists clientes_fusao (
    id            bigserial primary key,
    dono_id       bigint not null references contas(id),
    vencedor_id   bigint not null references clientes(id),
    perdedor_id   bigint not null references clientes(id),
    motivo        text,
    movido        jsonb   not null default '{}'::jsonb,
    feita_por     bigint,
    criado_em     timestamptz not null default now(),
    desfeita_em   timestamptz,
    desfeita_por  bigint
);

create index if not exists ix_clientes_fusao_dono
    on clientes_fusao (dono_id, criado_em desc);

-- Um cadastro só pode ter sido absorvido UMA vez enquanto a fusão estiver de
-- pé. Sem isso, dois cliques seguidos no mesmo par moveriam as referências duas
-- vezes e o desfazer não saberia pra onde voltar.
create unique index if not exists ux_clientes_fusao_perdedor_ativa
    on clientes_fusao (perdedor_id) where desfeita_em is null;
