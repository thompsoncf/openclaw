-- 307_apolice_lida_descartada.sql
-- DÁ PRA TIRAR UM DOCUMENTO DA FILA DE CONFERÊNCIA SEM CADASTRAR.
--
-- O PEDIDO (dono, 21/09/2026): "quando abri e clico na apólice para aprovar não
-- consigo sair caso não guarde, e como tem apólice repetida lá tenho que ver uma
-- forma de resolver isso".
--
-- O QUE ESTAVA ERRADO. A fila só esvaziava de um jeito: CADASTRANDO. Um documento
-- que não deveria ser cadastrado — a mesma apólice lida três vezes enquanto eu
-- consertava o leitor, um boleto que passou pelo filtro, uma leitura velha que
-- ficou obsoleta quando o layout entrou — não tinha saída nenhuma. A fila só
-- crescia, e uma fila que só cresce para de ser fila.
--
-- Naquele dia a conta 37 tinha quatro linhas: TRÊS eram o mesmo PDF da Mapfre
-- (11:08, 13:56, 14:39) e a quarta era a Porto lida antes do layout dela existir.
-- Só uma prestava.
--
-- DESCARTAR NÃO APAGA (regra 0). A linha fica, com quem descartou e quando: o
-- documento continua no cofre, a leitura continua auditável, e desfazer é um
-- update. O que muda é só a fila parar de mostrar.
--
-- Aditivo e idempotente.

alter table public.apolice_lida
    add column if not exists descartado_em  timestamptz,
    add column if not exists descartado_por bigint;

-- a fila lê por conta e ordena por data; o descarte entra no mesmo recorte
create index if not exists ix_apolice_lida_fila
    on public.apolice_lida (conta_id, criado_em desc)
 where descartado_em is null;

-- rollback:
--   drop index if exists public.ix_apolice_lida_fila;
--   alter table public.apolice_lida drop column if exists descartado_em;
--   alter table public.apolice_lida drop column if exists descartado_por;
