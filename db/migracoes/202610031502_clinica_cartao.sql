-- 202610031502_clinica_cartao.sql
-- O QUE FAZ: os campos do cartão de clínica (entrega 3a): tipo de atendimento,
-- responsável e parentesco (o paciente é outra pessoa), e as origens da clínica
-- (Google, Rádio, Já é paciente, Telefone, Balcão) na lista de `origem_cliente`.
-- POR QUÊ: desenho "Cartão de clínica", aprovado pelo dono em 03/10/2026, com a
-- decisão E (um cartão por paciente). Na Pelle, em 03/10, nenhum dos 75 cartões
-- tinha cidade nem origem: a recepção não tinha onde marcar Rádio, Telefone ou
-- Balcão, e não havia campo para "consulta, procedimento ou serviço".
--
-- Aditiva e idempotente, no mesmo jeito da 209. A lista de origens continua com
-- as cinco chaves da 209 e só ganha as da clínica. As colunas novas nascem vazias
-- e sem default (não reescrevem a tabela).

alter table public.prospeccao add column if not exists tipo_atendimento text;
alter table public.prospeccao add column if not exists responsavel_nome text;
alter table public.prospeccao add column if not exists responsavel_parentesco text;

alter table public.prospeccao drop constraint if exists prospeccao_tipo_atendimento_check;
alter table public.prospeccao add constraint prospeccao_tipo_atendimento_check
    check (tipo_atendimento is null or tipo_atendimento in
           ('consulta', 'procedimento', 'servico', 'outro'));

alter table public.prospeccao drop constraint if exists prospeccao_origem_cliente_check;
alter table public.prospeccao add constraint prospeccao_origem_cliente_check
    check (origem_cliente is null or origem_cliente in
           ('whatsapp', 'indicacao', 'instagram', 'manual', 'outro',
            'google', 'radio', 'ja_paciente', 'telefone', 'balcao'));

-- rollback:
--   alter table public.prospeccao drop constraint if exists prospeccao_tipo_atendimento_check;
--   alter table public.prospeccao drop column if exists tipo_atendimento;
--   alter table public.prospeccao drop column if exists responsavel_nome;
--   alter table public.prospeccao drop column if exists responsavel_parentesco;
--   (a lista de origens volta à da 209 só depois de limpar as chaves novas:
--    update prospeccao set origem_cliente='outro'
--     where origem_cliente in ('google','radio','ja_paciente','telefone','balcao'))
