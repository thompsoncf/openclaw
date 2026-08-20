-- 181_aviso_vencimento.sql
-- Liga/desliga a FAIXA de vencimento no painel do cliente (app_config).
--
-- Durante o beta ninguem e' cobrado, mas o vencimento do plano continua correndo
-- no banco: contas "vencidas" que estao usando de graca — e com a nossa benca —
-- ganhavam uma faixa vermelha pedindo pagamento em toda tela.
--
-- Esta chave so' decide o que APARECE. Nao libera nem corta acesso de ninguem:
-- quem decide isso e' contas.acesso_liberado, que nao le esta chave.
--
-- Nasce 'on' (avisando) = exatamente o comportamento de hoje. Rodar nao muda
-- nada de quem ja' esta no ar; quem cala e' o botao no /admin.
insert into app_config (chave, valor)
values ('aviso_vencimento', 'on')
on conflict (chave) do nothing;
