-- 128_lembretes_aviso_convidado.sql
-- finance/lembretes.py::_avisar_convidados_confirmados dedupa o aviso ao
-- convidado com tipo='aviso_convidado' — mas o check nunca permitiu esse
-- valor, então o insert SEMPRE falhava (CheckViolation), reproduzido em
-- produção. Pior: como _primeira_vez roda numa conexão própria mas NÃO é
-- protegida por try/except no chamador, a exceção sobe até _rodar() e aborta
-- o laço de TODAS as contas daquele ciclo do ticker a partir da que falhou —
-- não só o aviso ao convidado, o resumo/aviso de contas seguintes no mesmo
-- tick também ficava sem rodar (recuperava só no próximo ciclo, ~2 min
-- depois). O check é recriado (nome estável desde a 101).

alter table lembretes_enviados drop constraint if exists lembretes_enviados_tipo_check;
alter table lembretes_enviados add constraint lembretes_enviados_tipo_check
    check (tipo in ('resumo','aviso','aviso_convidado'));
