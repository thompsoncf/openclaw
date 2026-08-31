-- 192_evento_cliente.sql
-- O compromisso passa a saber DE QUEM ele é.
--
-- POR QUE. O relatório de Agenda mostrava "—" na coluna Cliente em 51 dos 60
-- compromissos da Prime (medido em 31/08/2026), e a causa não era o relatório: o
-- formulário de novo compromisso nunca teve campo de cliente. Tem título, data,
-- hora, encerramento, "só segurar a data", descrição, local, tipo, participantes
-- e avisos — e nenhum lugar pra dizer de quem é a festa. O nome acabava dentro do
-- texto do título ("Locação — Fulano"), e ali ele é texto, não dado: não dá pra
-- somar por cliente, não liga na ficha, não vira histórico.
--
-- Os 9 que tinham nome vinham dos dois únicos caminhos que existiam:
-- `orcamentos.evento_agenda_id` (2, de proposta aprovada) e `prospeccao_id` (7,
-- de visita marcada pelo Cockpit). Dos 43 EVENTOS — locação, casamento, formatura
-- — nenhum tinha lead, e é esperado: locação não nasce de lead de WhatsApp.
--
-- POR QUE UMA COLUNA NOVA E NÃO REUSAR `prospeccao_id`
-- São coisas diferentes. `prospeccao_id` é o LEAD (quem chamou no WhatsApp e
-- ainda está sendo trabalhado); `cliente_id` é a RELAÇÃO desta loja com uma
-- pessoa (`clientes`, o modelo pessoa+relação da 066). A maioria das locações da
-- Prime nunca foi lead — foi telefonema, indicação, balcão. Enfiar as duas no
-- mesmo campo faria o funil contar como lead quem nunca passou por ele.
--
-- SEM FK, de propósito, pelo mesmo motivo de `orcamentos.cliente_id` (152):
-- `clientes` é relação e pode ser ARQUIVADA (inclusive por uma fusão de
-- duplicados, migração 191). Um compromisso de dezembro apontando pra relação
-- arquivada continua válido — o nome se lê pelo join, e some se a relação sumir,
-- em vez de travar a exclusão.
--
-- NÃO HÁ BACKFILL. Adivinhar o cliente a partir do título é palpite, e palpite
-- não se grava como fato — ele é lido na hora de mostrar, marcado como palpite,
-- e só vira `cliente_id` quando o dono confirma. Aditiva e idempotente.
alter table public.eventos_agenda
    add column if not exists cliente_id bigint;

-- Só pra achar os compromissos de um cliente (a ficha dele, e o relatório
-- agrupado). Parcial porque a maioria das linhas antigas segue nula.
create index if not exists idx_eventos_agenda_cliente
    on public.eventos_agenda (cliente_id) where cliente_id is not null;
