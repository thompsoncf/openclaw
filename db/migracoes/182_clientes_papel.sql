-- 182_clientes_papel.sql
-- Papel do cadastro em `clientes`: CLIENTE e/ou FORNECEDOR — nao exclusivos.
--
-- POR QUE. A aba Clientes deixa cadastrar qualquer PF/PJ, mas nao existia como
-- dizer QUE TIPO de relacao e' essa. O efeito aparecia no card de Titulos a
-- pagar e receber: o campo de "quem" numa divida A PAGAR estava rotulado
-- "Cliente" mesmo sendo um fornecedor, e o nome digitado nem chegava a ser
-- salvo (so' era ligado quando o titulo era A RECEBER).
--
-- NAO E' ESCOLHA UNICA: a mesma empresa pode te vender material E te contratar
-- pra um servico ao mesmo tempo. Mesmo padrao de `contas.vende_produto` e
-- `contas.vende_servico`, ja independentes hoje.
--
-- eh_cliente nasce TRUE em todo mundo — preserva o cadastro de quem ja esta
-- na base sem exigir nenhuma acao. eh_fornecedor nasce FALSE: so' passa a
-- valer quem for marcado explicitamente.
alter table clientes add column if not exists eh_cliente boolean not null default true;
alter table clientes add column if not exists eh_fornecedor boolean not null default false;
