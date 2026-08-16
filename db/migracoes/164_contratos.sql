-- 164_contratos.sql
-- O contrato deixa de ser adjetivo do orçamento e vira DOCUMENTO.
--
-- POR QUE AGORA
-- A 160 pendurou cinco colunas de contrato em `orcamentos`. Medido na produção em
-- 16/08/2026: 16 orçamentos, 13 nunca saíram de rascunho/enviado/negociando, e as
-- cinco colunas estavam nulas em 16 de 16. A maioria dos orçamentos NÃO VIRA
-- CONTRATO — e os que viram passam a precisar de coisas que orçamento não tem.
--
-- E havia ZERO contratos assinados. É a janela: separar hoje é criar tabela e
-- mudar de onde a tela lê. Depois de dez assinados, seria migrar documento
-- jurídico congelado sem poder corromper nenhum.
--
-- O QUE A COLUNA COLADA NÃO RESOLVIA
--
--  1. "Quais contratos pendentes" não tinha resposta. O índice da 160 é
--     `where contrato_assinado_em is null` — cobria os 16, inclusive os 13
--     rascunhos que nunca serão contrato. Aqui a pergunta é literal:
--     `status='enviado'`, e só existe linha pra contrato que É contrato.
--  2. Orçamento com contrato ASSINADO podia ser editado por baixo (a trava de
--     edição só barra `status='fechado'`). Documento congelado, com aceite e IP
--     do cliente, e os números de origem mudando embaixo. Com a tabela, a trava
--     tem onde perguntar — é o que web/painel_servicos passa a fazer.
--  3. Rescisão não tinha onde morar, mesmo com cláusula de cancelamento no
--     modelo: o contrato ficava "assinado" pra sempre.
--  4. Aditivo idem — daí `substitui_id`.
--  5. O contrato herdava o número do orçamento. São duas séries de documento.
--
-- AS COLUNAS VELHAS DE `orcamentos` FICAM, sem uso, e saem numa migração
-- posterior. Enquanto esta estiver estreando, elas são o rollback.
--
-- Aditivo e idempotente.
create table if not exists public.contratos (
    id             bigserial primary key,
    conta_id       bigint not null references public.contas(id) on delete restrict,
    -- série PRÓPRIA, por conta. "Contrato nº 1" e "Orçamento nº 1" sendo o mesmo
    -- número é confusão garantida na hora de citar o documento.
    numero         int    not null,
    -- de onde nasceu. Nulo é contrato avulso (renovação, cliente antigo) — não
    -- existe hoje, mas o modelo não pode proibir. Sem FK pelo mesmo motivo do
    -- título a receber: documento assinado não pode ser impedido de existir por
    -- causa do documento comercial que o originou.
    orcamento_id   bigint,
    status         text   not null default 'enviado'
                   check (status in ('rascunho','enviado','assinado','rescindido','cumprido')),
    -- CONGELADO na assinatura: as cláusulas já com os campos substituídos. É a
    -- regra de ouro da 160 e não muda — só muda a coluna onde cai. Nulo enquanto
    -- ninguém assinou, e aí o documento é montado ao vivo (o cliente sempre lê a
    -- versão atual do modelo).
    texto          jsonb,
    -- o valor congelado junto com o texto, pra o histórico não depender de reler
    -- o orçamento (que pode ter sido editado depois de um aditivo).
    valor_centavos bigint,
    assinado_em    timestamptz,
    assinado_por   text,
    assinado_doc   text,
    assinado_ip    text,
    rescindido_em  timestamptz,
    rescisao_motivo text,
    -- aditivo: contrato novo que substitui um anterior, com os dois no histórico.
    substitui_id   bigint references public.contratos(id),
    criado_em      timestamptz not null default now(),
    criado_por     text   not null default ''
);

-- a série por conta, garantida pelo banco (mesmo desenho de orcamentos.numero)
create unique index if not exists ux_contratos_conta_numero
    on public.contratos (conta_id, numero);

-- UM contrato vivo por orçamento. Parcial em `substitui_id is null` de propósito:
-- o aditivo tem o mesmo `orcamento_id` do que ele substitui, e precisa caber.
create unique index if not exists ux_contratos_orcamento
    on public.contratos (orcamento_id)
 where orcamento_id is not null and substitui_id is null;

-- "o que está esperando assinatura" — a pergunta da tela do dono, agora com
-- resposta certa. Ordena por id, não por criado_em: id já cresce com o tempo e é
-- coluna da tabela desde a criação (mesmo cuidado que a 160 documentou).
create index if not exists idx_contratos_conta_status
    on public.contratos (conta_id, status, id desc);

-- rollback:
--   drop table if exists public.contratos;
