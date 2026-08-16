-- 165_contrato_token.sql
-- O contrato ganha LINK PRÓPRIO.
--
-- A 164 separou o contrato do orçamento no banco, mas o DOCUMENTO continuava
-- saindo dentro da folha da proposta: mesma URL, mesma página, o contrato como um
-- bloco no rodapé do orçamento. São dois documentos, com dois aceites e dois
-- momentos — a proposta aprova valores e vai antes do pagamento; o contrato aceita
-- cláusulas de cancelamento e multa, e só existe depois da entrada. Empilhados na
-- mesma página, o segundo lê como anexo do primeiro.
--
-- Com token próprio o contrato vira o que é: um documento que se manda, se abre,
-- se imprime e se assina por conta própria — como a proposta já fazia.
--
-- Token e não id na URL pelo mesmo motivo da proposta: o link vai por WhatsApp e
-- não pode ser adivinhável por incremento.
--
-- Aditivo e idempotente.
alter table public.contratos add column if not exists token text;

create unique index if not exists ux_contratos_token
    on public.contratos (token) where token is not null;

-- os contratos que já existirem sem token ganham o deles (não há nenhum assinado
-- em produção nesta data; isto é pra bases de teste e pra reaplicação segura).
update public.contratos
   set token = substr(md5(random()::text || id::text || clock_timestamp()::text), 1, 22)
 where token is null;

-- rollback:
--   drop index if exists ux_contratos_token;
--   alter table public.contratos drop column if exists token;
