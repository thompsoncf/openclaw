-- 138_chave_ofx_lancamentos.sql
-- A coluna lancamentos.chave era varchar(44), dimensionada só pra chave de
-- acesso da NFC-e (sempre 44 dígitos). O import de extrato bancário (OFX)
-- reaproveita a MESMA trava de idempotência (índice ux_lanc_chave_conta, da
-- migração 018) só que com uma chave sintética "ofx:{bankid}:{acctid}:{fitid}"
-- — precisa de mais espaço. Aditivo e idempotente (ALTER ... TYPE é seguro
-- rodar de novo; widen nunca trunca dado existente).

alter table lancamentos alter column chave type varchar(80);
