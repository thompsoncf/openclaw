-- 055_modulo_fornecedor.sql
-- Registra os módulos PJ no catálogo com nome claro e preço editável.
-- NÃO mexe no gate: 'eh_fornecedor' segue sendo o interruptor real da aba.
-- Isto é só o "registro comercial" (nome + mensalidade) pro card de Planos.

-- módulo Fornecedor (mensalidade maior — mais canais; preço 0 = definir no card)
insert into modulos (codigo, nome, preco_centavos)
values ('fornecedor', 'Módulo Fornecedor (loja/marketplace)', 0)
on conflict (codigo) do nothing;

-- garante o nome amigável do módulo Empresa (criado na 053)
update modulos set nome = 'Módulo Empresa (PJ)'
 where codigo = 'pj' and nome <> 'Módulo Empresa (PJ)';
