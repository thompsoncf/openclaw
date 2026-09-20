-- 302_tempo_tela.sql
-- QUANTO DEMORA, DO PONTO DE VISTA DO CELULAR DO VENDEDOR.
--
-- POR QUÊ. O servidor já se cronometra desde 19/09/2026 (db/medicao.py + o
-- middleware `_mede_cockpit`): tempo, consultas e conexões por tela. Mas isso vai
-- pro LOG e morre lá — não dá pra comparar semana passada com hoje, nem separar
-- "o servidor demorou" de "a rede do vendedor estava ruim". E o log não sabe o
-- que acontece DEPOIS da resposta: o aparelho ainda precisa desenhar a tela.
--
-- Esta tabela é uma linha por NAVEGAÇÃO, escrita pelo próprio aparelho quando a
-- tela termina de abrir. O tempo chega quebrado em pedaços que se somam, e é essa
-- quebra que diz em quem apostar:
--
--     conexao_ms  DNS + TCP + TLS            → a rede do vendedor
--     espera_ms   1º byte menos o servidor   → a viagem de ida e volta
--     servidor_ms o que o servidor gastou    → nosso código (banco_ms dentro dele)
--     render_ms   do fim do download à tela  → o aparelho desenhando
--
-- NÃO GUARDA DADO DE NINGUÉM: nem texto, nem lead, nem telefone. `tela` vem com
-- os números trocados por {id} — /cockpit/lead/812 e /cockpit/lead/90 são a mesma
-- tela, e é por tela que se compara.
--
-- Isto é telemetria, não histórico: pode ser limpa a qualquer momento sem perda.
create table if not exists public.tempo_tela (
    id          bigserial primary key,
    conta_id    bigint      not null references contas(id) on delete cascade,
    -- quem tocou: NULL quando é o dono/gestor, que não tem membro_id
    membro_id   bigint,
    tela        text        not null,
    servidor_ms integer     not null default 0,
    banco_ms    integer     not null default 0,
    consultas   integer     not null default 0,
    conexao_ms  integer     not null default 0,
    espera_ms   integer     not null default 0,
    render_ms   integer     not null default 0,
    total_ms    integer     not null default 0,
    -- '4g', '3g', 'wifi'… o que o navegador disser; '' quando ele não diz
    rede        text        not null default '',
    criado_em   timestamptz not null default now()
);

-- a tela de Velocidade sempre pergunta "desta conta, nos últimos N dias, por tela"
create index if not exists idx_tempo_tela_conta
    on public.tempo_tela (conta_id, criado_em desc);
