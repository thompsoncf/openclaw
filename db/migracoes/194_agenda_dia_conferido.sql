-- 194_agenda_dia_conferido.sql
-- "Está certo" — o jeito de encerrar a pergunta do choque de data.
--
-- POR QUE. `choques_de_data` marca em vermelho todo dia futuro com mais de um
-- compromisso, e isso é certo: no nicho de eventos duas festas no mesmo salão
-- não precisam se sobrepor por hora nenhuma pra serem um problema. O que faltava
-- era a outra metade — dizer que aquele dia foi olhado e está certo.
--
-- Medido na Prime (conta 34) em 02/09/2026: dos 10 dias com mais de um
-- compromisso, 9 já passaram e o painel nem mostra. O único futuro é 12/09, com
-- uma VISITA às 10:00 e um ANIVERSÁRIO às 19:00 — a rotina da casa, não conflito.
-- Ou seja: o alerta estava errado em 1 de 1, e não havia botão nenhum pra dizer
-- isso. O card promete "some daqui sozinho quando você resolver" e não cumpre,
-- porque não há o que resolver. Lista que nunca esvazia é lista que ninguém abre.
--
-- O QUE ESTA TABELA GUARDA, E POR QUE NÃO É SÓ UMA DATA. `eventos` é o conjunto
-- de ids que estavam no dia na hora do "está certo". A resposta valeu PRA
-- AQUELES compromissos, não pro dia inteiro até o fim dos tempos: se cair uma
-- festa nova em cima, o conjunto atual deixa de caber no conferido e o alerta
-- volta sozinho. Guardar só o dia calaria pra sempre a data mais perigosa da
-- agenda — justamente a que já tem gente marcada.
--
-- Tirar um compromisso do dia NÃO faz o alerta voltar (o conjunto continua
-- cabendo), e é de propósito: menos compromisso no dia nunca é notícia pior.
--
-- Isto não apaga nem altera evento nenhum: é uma marca no DIA, ao lado da agenda.
-- Aditiva e idempotente.
create table if not exists public.agenda_dia_conferido (
    conta_id    bigint      not null references public.contas(id) on delete cascade,
    dia         date        not null,
    eventos     bigint[]    not null default '{}',
    marcado_em  timestamptz not null default now(),
    marcado_por text        not null default '',
    primary key (conta_id, dia)
);
