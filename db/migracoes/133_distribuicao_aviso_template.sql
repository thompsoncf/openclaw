-- 133_distribuicao_aviso_template.sql
-- Template do aviso "caiu um lead pra você" no WhatsApp do vendedor. Fora da janela de
-- 24h a Meta exige TEMPLATE aprovado — então guardamos aqui o identificador do template:
--   * Twilio  → Content SID (HX...)
--   * Cloud API (número próprio) → o NOME do template aprovado na Meta
-- Vazio = só avisa por e-mail (o WhatsApp fica desligado até ter template).
-- Ver finance/distribuicao.avisar_vendedor.

alter table public.distribuicao add column if not exists aviso_template_sid text;
