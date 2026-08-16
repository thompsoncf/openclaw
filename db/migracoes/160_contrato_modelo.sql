-- 160_contrato_modelo.sql
-- O contrato de locação passa a viver no sistema, escrito pela própria empresa.
--
-- POR QUE
-- Hoje o cliente assina a PROPOSTA. O contrato existe em Word, fora do Zaq, e é
-- preenchido à mão depois. Os dois documentos então mantêm cópias próprias dos
-- mesmos números — e elas divergem. Medido no contrato vigente da Prime Eventos
-- contra o catálogo dela, em 16/08/2026:
--
--   hora extra      contrato R$ 600,00/h   catálogo R$ 620,00
--   taxa de limpeza contrato R$ 600,00     catálogo R$ 400,00
--
-- E não é teórico: em 15/08 o agente cotou "R$ 620 por hora" para um cliente que,
-- se fechasse, assinaria um contrato dizendo R$ 600. Na limpeza é pior — a
-- proposta promete R$ 400 e o contrato cobra R$ 600.
--
-- A CORREÇÃO ESTRUTURAL é o contrato PARAR de ter números próprios. As cláusulas
-- guardam campos ({preco.hora-extra}, {valor.total}, {evento.data}…) e o texto
-- final é montado na hora, lendo catálogo, orçamento e regras da conta. Corrigir
-- o preço num lugar corrige nos dois, e a divergência não tem por onde voltar.
--
-- SÓ NICHO EVENTO
-- A trava não está só na tela: o modo do orçamento nasce de vendas.modo_por_nicho
-- (migração 147), e o contrato segue a mesma porta. Uma conta de nicho recorrente
-- não tem contrato de locação de espaço — teria um contrato de serviço, que é
-- outro documento e outra conversa.
--
-- Aditivo e idempotente.

-- MODELO por conta: as cláusulas que a empresa edita, e as regras que os campos
-- {regra.*} leem. Uma linha por conta — o histórico de quem assinou não vive
-- aqui (ver `orcamentos.contrato_texto`), então sobrescrever é seguro.
create table if not exists public.contrato_modelo (
    conta_id      bigint primary key references public.contas(id) on delete cascade,
    -- [{"titulo": "Cláusula 1 — Do objeto", "corpo": "1.1. …{evento.data}…"}, …]
    -- jsonb e não tabela filha porque a unidade de edição é o modelo INTEIRO:
    -- o dono arrasta, renomeia e reescreve tudo numa tela só e salva de uma vez.
    clausulas     jsonb not null default '[]'::jsonb,
    -- o que os campos {regra.*} devolvem. Percentuais em inteiro (30 = 30%),
    -- tempos em minutos/horas. Fica aqui, e não no código, porque é a empresa
    -- que decide — e porque o contrato de cada uma tem números diferentes.
    regras        jsonb not null default '{}'::jsonb,
    atualizado_em timestamptz not null default now(),
    atualizado_por text not null default ''
);

-- O CONTRATO DE CADA ORÇAMENTO. Três coisas moram aqui, e a primeira é a que
-- importa juridicamente:
--
-- `contrato_texto` é o documento CONGELADO no momento da assinatura — as
-- cláusulas já com os campos substituídos. Sem isso, o dono editar o modelo
-- amanhã reescreveria retroativamente o que o cliente aceitou ontem, e nenhum
-- contrato assinado no Zaq se sustentaria. Enquanto ninguém assinou, fica null e
-- o documento é montado ao vivo (assim o cliente sempre lê a versão atual).
alter table public.orcamentos add column if not exists contrato_texto      jsonb;
alter table public.orcamentos add column if not exists contrato_assinado_em timestamptz;
alter table public.orcamentos add column if not exists contrato_assinado_por text;
alter table public.orcamentos add column if not exists contrato_assinado_doc text;
alter table public.orcamentos add column if not exists contrato_assinado_ip  text;

-- "quais contratos ainda não foram assinados" é a pergunta da tela do dono.
-- Ordena por `id`, não por `criado_em`: o id já cresce com o tempo e é coluna da
-- própria tabela desde a criação dela, enquanto `criado_em` veio depois, por
-- outra migração. Migração que depende de coluna que outra criou quebra em
-- qualquer base que não tenha rodado as duas — e é exatamente isso que o
-- tests/test_blindagem_migracoes.py existe para pegar.
create index if not exists idx_orcamentos_contrato_pendente
    on public.orcamentos (conta_id, id desc)
 where contrato_assinado_em is null;
