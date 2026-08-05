-- 127_prospeccao_atividade_bounce.sql
-- finance/email_inbound.py::_tratar_bounce grava uma atividade com tipo='bounce'
-- quando um e-mail de campanha retorna (endereço inválido) — mas o check nunca
-- permitiu esse valor, então o insert SEMPRE falhava. Pior: como isso acontece
-- ANTES do commit da mesma transação, o Postgres desfazia TAMBÉM o email_ok=false
-- e a parada da sequência de envio que vieram antes — ou seja, bounce nunca
-- protegeu a reputação de envio como o código promete. O check é recriado (nome
-- estável desde a 075).

alter table prospeccao_atividades drop constraint if exists prospeccao_atividades_tipo_check;
alter table prospeccao_atividades add constraint prospeccao_atividades_tipo_check
    check (tipo in ('ligacao','whatsapp','email','reuniao','visita','nota','bounce'));
