-- Até onde o vendedor JÁ VIU esta conversa.
--
-- O PEDIDO (28/08/2026): "por que o lead não marca como visto, ficou como 'sua
-- vez' sendo que abri até pra fazer o print".
--
-- Até aqui o Cockpit só sabia responder "tem mensagem esperando resposta?" — a
-- conta do `n_pend` em finance/cockpit.py, que é `in` mais novo que a última
-- `out`. Ela não distingue duas coisas que o vendedor vive como diferentes: VI e
-- RESPONDI. Abrir a conversa, ler e sair pra pensar deixava o card exatamente
-- como estava antes de abrir — bolinha vermelha e "sua vez" — e aí o selo deixa
-- de informar: se ele nunca muda com o que você faz, para de ser lido.
--
-- POR QUE ID E NÃO TIMESTAMP
-- Mesmo motivo que o `n_pend` já documenta: `now()` no Postgres é o início da
-- TRANSAÇÃO, então duas mensagens gravadas juntas nascem com o mesmo instante e
-- um corte por tempo pega as duas ou nenhuma. `id` é serial, sempre crescente e
-- sem empate. Guardando o ID da última mensagem vista, mensagem que chega DEPOIS
-- de o vendedor abrir volta a contar como não vista — que é o comportamento certo
-- e o que um `visto_em` por tempo daria com muito mais chance de erro.
--
-- Nulo = nunca aberta. É o estado de todas as conversas que já existem, e ele
-- devolve exatamente o comportamento de hoje: sem nada visto, o não-visto é igual
-- ao pendente. Ou seja, esta coluna não reescreve o passado de ninguém.
alter table conversas add column if not exists visto_ate_id bigint;

comment on column conversas.visto_ate_id is
  'id da última mensagem que o vendedor viu ao abrir a conversa no Cockpit. '
  'Nulo = nunca aberta. Serve pra separar "vi" de "respondi" nos selos da fila.';
