-- 238_etapa_sai_do_quadro.sql
-- Fechado sai do Kanban e a gestão do evento passa pra Agenda. Regra 6 do
-- FLUXO_FINAL_FUNIL_PRIME_EVENTOS_DESENVOLVEDOR_V3.
--
-- O QUE O DONO PEDIU
--   "Retirar do funil/Kanban comercial as colunas 'EVENTO A REALIZAR' e 'EVENTO
--    REALIZADO'. Essas etapas não representam mais prospecção, pois o cliente já foi
--    convertido. (...) Ao entrar em FECHADO, o cliente deve sair do Kanban de
--    prospecção. O cadastro, contrato e histórico permanecem no sistema, mas a
--    gestão do evento passa automaticamente para AGENDA e RELATÓRIOS."
--
-- DUAS COLUNAS, PORQUE SÃO DUAS DECISÕES DIFERENTES
--   `sai_do_quadro`     a etapa não vira coluna no Kanban, e os leads dela somem
--                       do quadro comercial. O cadastro fica inteiro — some da
--                       TELA de prospecção, não do banco.
--   `agenda_ao_entrar`  ao entrar aqui, o lead com data de evento ganha (ou já tem)
--                       o compromisso na Agenda, ligado à ficha.
--
-- Separadas porque uma empresa pode querer tirar do quadro sem mexer na agenda
-- (não vende data), e porque tirar do quadro ANTES de a agenda receber o evento é
-- exatamente como se troca uma coluna cheia por uma agenda vazia.
--
-- POR QUE ISSO IMPORTA AQUI (medido na conta 34 em 11/09/2026)
-- Dos 86 compromissos da agenda, só 14 estão ligados a um lead — a ponte entre as
-- duas telas quase não existe. Se "Fechado" saísse do quadro hoje, sem a ponte, os
-- 7 leads de "Evento A Realizar" e "Evento Realizado" sumiriam da prospecção sem
-- aparecer em lugar nenhum. Por isso a ponte é uma coluna, e não um efeito colateral
-- de `sai_do_quadro`: ligar a segunda sem a primeira é uma escolha que o dono faz
-- vendo o que cada uma faz.
--
-- NASCEM FALSE as duas, em toda etapa: nada muda no dia do deploy, e o quadro da
-- Prime continua com as oito colunas até alguém marcar a caixa na Régua.
--
-- Aditiva e idempotente.

alter table public.funil_etapas
  add column if not exists sai_do_quadro    boolean not null default false,
  add column if not exists agenda_ao_entrar boolean not null default false;

comment on column public.funil_etapas.sai_do_quadro is
  'a etapa não aparece no Kanban e seus leads somem do quadro; o cadastro fica inteiro';
comment on column public.funil_etapas.agenda_ao_entrar is
  'ao entrar nesta etapa, o lead com data de evento ganha o compromisso na Agenda';

-- rollback:
--   alter table public.funil_etapas drop column sai_do_quadro, drop column agenda_ao_entrar;
