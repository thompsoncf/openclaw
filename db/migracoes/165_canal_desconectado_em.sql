-- 165_canal_desconectado_em.sql
-- O MARCO ZERO do relógio de retenção do histórico de WhatsApp.
--
-- POR QUE PRECISA DE COLUNA NOVA
-- A regra é "apagar o histórico 30 dias depois de desconectar". Hoje não existe
-- nenhum lugar que registre QUANDO o canal desconectou:
--
--   • `canais_config.ativo` só diz que está desligado, não desde quando;
--   • `canais_config.atualizado_em` muda por QUALQUER edição do canal (trocar o
--     provedor, corrigir o número, reconectar), então usá-lo como marco fazia o
--     relógio reiniciar por motivos que não têm nada a ver com desconexão — ou,
--     pior, marcar como "velho" um canal que acabou de cair.
--
-- Sem um marco próprio o relógio não tem zero, e um expurgo que apaga o ativo
-- comercial da empresa não pode ficar pendurado num carimbo ambíguo.
--
-- QUEM ESCREVE
-- Só o webhook /webhooks/wa-qr/deslogado — o ponto único por onde passam as duas
-- formas de desconexão (o botão "Desconectar" do painel e o logout involuntário
-- que o Baileys reporta). Zera quando o serviço de QR é OBSERVADO conectado.
--
-- Deliberadamente NÃO zera no /whatsapp-qr-iniciar, que é a rota óbvia e a
-- errada: o painel a chama sozinho a cada abertura da tela de Canais
-- (qrAutoReconectar), então zerar ali faria o dono reiniciar os 30 dias só por
-- abrir a página, com o WhatsApp ainda desconectado.
--
-- Aditivo e idempotente. Nulo = não está desconectado (ou desconectou antes
-- desta migração, e aí o relógio só começa a contar na próxima desconexão —
-- de propósito: não inventamos passado pra apagar histórico com base nele).

alter table public.canais_config
    add column if not exists desconectado_em timestamptz;

comment on column public.canais_config.desconectado_em is
    'Quando este canal foi desconectado. Marco zero da retenção de histórico '
    '(30 dias). Nulo = conectado, ou desconexão anterior à migração 165.';

-- "quais canais passaram do prazo" — a pergunta que a faxina diária faz. Parcial
-- em `desconectado_em is not null` porque o caso normal é o canal estar conectado:
-- o índice só carrega as linhas que a faxina precisa olhar.
create index if not exists idx_canais_desconectado_em
    on public.canais_config (desconectado_em)
 where desconectado_em is not null;

-- rollback:
--   drop index if exists public.idx_canais_desconectado_em;
--   alter table public.canais_config drop column if exists desconectado_em;
