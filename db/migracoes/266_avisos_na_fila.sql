-- 266_avisos_na_fila.sql
-- O dono decide se a faixa de novidade interrompe a Fila dos vendedores dele.
--
-- DE ONDE VEIO. Em 16/09/2026 o dono não conseguia fechar o aviso da Fila. O ✕
-- funcionava — ele tocou quatro vezes e o banco registrou as quatro — mas havia 26
-- avisos por ler acumulados, e a faixa mostra um por vez: fechava um e o seguinte
-- tomava o lugar. Minha primeira proposta foi o ✕ dispensar a fila inteira, e ele
-- barrou com um argumento melhor que o meu:
--
--     "cria um parametro dentro dos ajustes da empresa com o botao de ligar ou
--      nao o aviso acho que fica melhor ao inves de fechar tudo"
--
-- Ele está certo. Marcar 26 como lidos DESTRÓI informação: "não lido" é o que a
-- bolinha do Perfil conta, e não existe desmarcar. O interruptor não apaga nada e
-- é reversível.
--
-- LIGADO POR PADRÃO, e a coluna é NOT NULL: o comportamento de hoje continua
-- valendo pra todo mundo, e quem quiser silêncio escolhe. `novidades.faixa_ligada`
-- falha ABERTA de propósito (sem coluna → True): um parâmetro que não pôde ser
-- lido não pode calar um aviso.
--
-- O QUE ISTO NÃO FAZ: não muda quem recebe o quê (`publico` e `pra_quem` seguem
-- iguais), não apaga leitura, e não mexe na tela de Novidades do Perfil. Desligar
-- tira a INTERRUPÇÃO, não o aviso.
--
-- Aditivo e idempotente.

alter table public.contas
  add column if not exists avisos_na_fila boolean not null default true;

comment on column public.contas.avisos_na_fila is
  'Mostra a faixa de novidade no topo da Fila dos vendedores. Desligado, os avisos '
  'continuam na tela Novidades do Perfil e continuam contando como não lidos.';

-- rollback:
--   alter table public.contas drop column if exists avisos_na_fila;
