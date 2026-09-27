-- 422_novidade_proposta_data_pos_festa.sql
-- O aviso da migração 421 (a validade da proposta, a data segurada e o pós-festa —
-- parte 2b do funil novo de eventos), seguindo a seção 5 do CLAUDE.md.
--
-- PÚBLICO `eventos` (§6). PRA QUEM: dono, gestor e vendedor — o vendedor ganha os selos
-- e os avisos. QUEM RECEBE: toda conta que vende festa; na Prime (34) já vem ligado
-- (421); nas outras, o corpo diz onde ligar.
--
-- Aditiva e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('proposta-data-pos-festa', 'novidade', 'eventos', '{dono,gestor,vendedor}',
 'Validade da proposta, prazo do sinal e pós-festa no funil',
 'Quem vende festa vê no card até quando a proposta vale e quanto falta pro sinal da data segurada, é avisado quando outro cliente pede a mesma data, e recebe o agradecimento pronto no dia seguinte à festa.',
 '/painel/prospeccao',
 $txt$Três coisas que o card não contava:

- VALIDADE DA PROPOSTA: o card em Proposta mostra "vale até 02/10", depois "vence amanhã" e "venceu". O link do cliente passa a dizer a mesma data (antes dizia a data da festa). Vale pras propostas emitidas daqui pra frente. O número de dias se muda na Régua › Rotinas de festa.
- DATA SEGURADA: o card mostra quanto falta pro sinal ("⏳ 41h pro sinal"). Se outro cliente pedir a mesma data, a reserva passa a vencer em 48h e quem segura é avisado pra lembrar do sinal. Se vencer sem sinal, o card volta pra Proposta com "reserva venceu" e a data vai pro 1º da lista de espera.
- PÓS-FESTA: no dia seguinte à festa, o vendedor recebe o texto pronto pra agradecer e pedir a avaliação e a indicação. No cliente da IA, a própria IA manda. O link de avaliação do Google se cola na Régua › Rotinas de festa.$txt$,
 timestamptz '2026-09-28 12:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'proposta-data-pos-festa';
