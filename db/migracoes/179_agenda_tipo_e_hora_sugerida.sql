-- 179_agenda_tipo_e_hora_sugerida.sql
-- O que a agenda precisa saber sobre uma FESTA, e não só sobre um compromisso.
--
-- POR QUE
-- A Prime Eventos tem 31 compromissos vivos no sistema antigo e 4 linhas no Zaq.
-- Ao trazer os 31, três coisas dos dados dela não tinham onde morar:
--
--  1. QUE FESTA É. O tipo (Locação, Casamento, Formatura…) só existe hoje dentro
--     de `orcamentos.evento->>'tipo'` — e nenhuma das 31 tem orçamento: são datas
--     vendidas por telefone, anotadas na agenda. Sem coluna aqui, o tipo vira
--     texto no título e ninguém consegue contar "quantas locações tenho em
--     dezembro". Pior: a sugestão de horário por tipo (abaixo) fica impossível
--     dali pra frente, porque o sistema não sabe que aquilo é uma locação.
--
--  2. QUANTA GENTE. A Prime escreve "BUFFET 70", "BUFFET 60", "BUFFET 150" — o
--     número é a quantidade de convidados, que é o que dimensiona o buffet. Como
--     texto no título ele é ilegível pra máquina; como int ele é o dado que é.
--
--  3. SE A HORA É PALPITE. 8 das 31 têm data e não têm hora, porque nesse negócio
--     o que se vende é o DIA — o horário se acerta depois. Mas `inicio` é
--     timestamptz not null: alguma hora tem que ser gravada. Gravar uma hora
--     chutada SEM MARCAR é o defeito perigoso: daqui a três meses ninguém sabe
--     quais horários alguém escolheu e quais o sistema inventou, e um 19:00 num
--     casamento que era 16:00 é cliente esperando sozinho no salão.
--     `hora_sugerida` é essa marca. A tela mostra o horário sublinhado e ele entra
--     na lista de conferência até alguém confirmar.
--
-- POR QUE `hora_sugerida` E NÃO `hora_definida`
-- O default tem que ser o caso comum e inofensivo. Todo evento que já existe teve
-- hora escolhida por gente, então o default é `false` (não é sugestão) e nenhuma
-- linha antiga precisa de backfill. `hora_definida default true` diria a mesma
-- coisa, mas quem lê `if ev.hora_sugerida` entende na hora; `if not
-- ev.hora_definida` é uma negação a mais entre a pergunta e a resposta.
--
-- NICHO
-- As três colunas só são preenchidas e mostradas onde `vende_data` é verdade (o
-- nicho eventos). Nos demais nichos ficam null e a tela não muda — é o mesmo
-- desenho de `pre_reserva_ate` e `sinal_centavos` (160 e 163).
--
-- Aditivo e idempotente: só ADD COLUMN IF NOT EXISTS, nenhum drop, nenhum
-- backfill, nenhuma constraint recriada.

alter table public.eventos_agenda
    -- Que festa é. Texto livre de propósito, e não check: a lista canônica vive em
    -- finance/servicos_catalogo.TIPOS_EVENTO e muda com o negócio (Locação,
    -- Formatura e Buffet entraram junto com esta migração). Um check aqui viraria
    -- uma segunda lista pra manter em sincronia — e listas paralelas acertam no
    -- primeiro dia e divergem no terceiro.
    add column if not exists tipo_evento text,

    -- Quantidade de convidados. Existe igual em `orcamentos.evento->>'convidados'`,
    -- e a duplicação é deliberada pelo mesmo motivo de `sinal_centavos` (163): a
    -- agenda tem que funcionar em compromisso que nunca teve orçamento.
    add column if not exists convidados int,

    -- A hora deste evento foi CHUTADA pelo sistema e ninguém confirmou ainda.
    add column if not exists hora_sugerida boolean not null default false;

-- Índice só pro que a tela consulta: a lista de pendências ("horários a conferir")
-- pergunta por conta, e só interessam as sugeridas. Parcial, porque a esmagadora
-- maioria das linhas é false e não precisa ocupar índice.
create index if not exists idx_eventos_hora_sugerida
    on public.eventos_agenda (conta_id, inicio)
    where hora_sugerida;
