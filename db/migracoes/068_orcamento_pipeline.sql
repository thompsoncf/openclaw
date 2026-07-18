-- 068_orcamento_pipeline.sql
-- Fundacao do pipeline de propostas da Aladdin (funil de VENDAS consultivas —
-- separado do funil de aquisicao do Zaq). A tela de orcamento ja COLETA contato,
-- WhatsApp, modulos e escopo, mas o backend so guardava cliente/empresa/valores.
-- Aqui passamos a guardar o resto + os campos de funil (status, dono, follow-up).
-- Tudo add-if-not-exists: rodar N vezes e' seguro, zero impacto em quem ja esta no ar.

alter table orcamentos add column if not exists whatsapp      text;
alter table orcamentos add column if not exists email         text;
alter table orcamentos add column if not exists modulos       jsonb;            -- ids dos modulos escolhidos
alter table orcamentos add column if not exists escopo        text;            -- escopo gerado pela IA
alter table orcamentos add column if not exists status        text not null default 'rascunho'
    check (status in ('rascunho','enviado','negociando','fechado','perdido'));
alter table orcamentos add column if not exists criado_por    text;            -- vendedor/consultor
alter table orcamentos add column if not exists canal         text;            -- origem do lead (indicacao, whatsapp, evento...)
alter table orcamentos add column if not exists follow_up_em  date;            -- proxima acao
alter table orcamentos add column if not exists atualizado_em timestamptz not null default now();

create index if not exists idx_orcamentos_status on orcamentos (status, criado_em desc);
