-- 057_natureza_lancamento.sql
-- Separação PF×PJ sem mexer na arquitetura: uma etiqueta por lançamento.
-- natureza = 'pessoal' | 'empresa' | null (null = "a definir", o dono decide).
-- Título e folha entram como 'empresa' sozinhos; o resto fica null até marcar.
-- Só a conta PJ usa isso; a PF ignora. Coluna nova nullable: zero impacto no ar.

alter table lancamentos add column if not exists natureza text;
