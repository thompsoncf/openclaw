-- 155_render_evento_corrige_veredito.sql
-- Desfaz o veredito que a 154 carimbou errado em evento que não é de desfecho.
--
-- O QUE ACONTECEU
-- A regra "details.status == 2 é sucesso" vem do exemplo oficial do Render e
-- vale pra `deploy_ended`. O receptor aplicava a qualquer tipo de evento — e
-- com os 64 eventos assinados chega muito mais que deploy. Resultado visto no
-- primeiro dia de produção:
--
--     18:43  ok     openclaw-web-bcu3  live
--     18:42  FALHA  openclaw-web-bcu3  pre_deploy_ended     <-- mentira
--
-- O pre-deploy tinha ido bem (o deploy ficou `live` um minuto depois), mas o
-- enum dele não é o do deploy, o número não era 2, e a linha virou FALHA — com
-- alerta no Telegram junto. Além disso, evento de ETAPA que trazia deployId
-- herdava o status do deploy inteiro e aparecia como "live", carimbando o
-- desfecho final num evento que só marcava o começo.
--
-- O código já não faz mais nenhuma das duas coisas (core/render_eventos.py:
-- o número só é lido em `deploy_ended`, e o status do deploy só é copiado
-- pra ele). Esta migração limpa o que ficou gravado antes da correção,
-- senão `historico --falhas` seguiria mostrando falha que nunca houve.
--
-- O ALVO, COM CUIDADO
-- Só linha que (a) não é `deploy_ended` e (b) NÃO trazia status no próprio
-- corpo do webhook. A condição (b) é o que protege o caso legítimo: cron que
-- reporta `data.status` = "succeeded"/"failed" decide pelo TEXTO do próprio
-- evento, não pelo enum do deploy — esse veredito está certo e fica.
--
-- Idempotente: rodar de novo não acha mais nada.

update public.render_evento
   set sucesso = null,
       -- o status também era emprestado do deploy; sem ele a leitura cai no
       -- tipo do evento, que é o que a linha realmente descreve.
       status  = null
 where tipo <> 'deploy_ended'
   and payload -> 'data' ->> 'status' is null
   and (sucesso is not null or status is not null);

-- rollback: não tem volta útil — o dado corrigido era falso. Se precisar
-- reprocessar, o corpo cru de cada evento continua em `payload`.
