-- 056_interesse_modulos.sql
-- Guarda o interesse comercial marcado no cadastro (ex.: "quero módulo Fornecedor").
-- É só captura de lead: NÃO liga nada (o gate real do Fornecedor segue sendo
-- 'eh_fornecedor'). Coluna nova + nullable: rodar não muda quem já está no ar.
alter table contas add column if not exists interesse_modulos text;
