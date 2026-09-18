-- 280_follow_up_zap.sql
-- O aviso de follow-up também no WhatsApp do vendedor. NASCE DESLIGADO.
--
-- PEDIDO DO DONO, 17/09/2026: "vamos implementar o whatsapp pra mandar pro
-- vendedor, pro número dele".
--
-- POR QUE NÃO ANTES, E POR QUE AGORA. O encanamento existe desde sempre — é o
-- mesmo `whatsapp_out.enviar` que o aviso de lead novo usa. O que faltava era a
-- TRAVA: quando o chip da empresa fala com o número do vendedor e ele responde, a
-- resposta volta pelo webhook e vira LEAD. Medido em 17/09, antes de ligar: nove
-- leads de membro em cinco contas, 4.309 mensagens — um dono com 2.307 mensagens
-- penduradas num lead do próprio funil. Ligar o WhatsApp do follow-up sem
-- consertar isso multiplicaria a sujeira. A trava foi consertada no mesmo dia
-- (`_eh_numero_da_equipe` passou a ler os dois campos de número E a valer pra quem
-- já era lead); só depois disto esta coluna faz sentido.
--
-- O INTERRUPTOR É SEPARADO do `aviso_zap` da distribuição de propósito. São dois
-- avisos diferentes com ritmos diferentes: o de lead novo é na hora e esporádico, o
-- de follow-up é uma vez por dia e previsível. Quem quer um pode não querer o
-- outro, e um interruptor só faria a conta escolher os dois de uma vez.
--
-- POR QUAL NÚMERO SAI: o mesmo `aviso_zap_chip_id` da distribuição. É a mesma
-- pergunta — "por qual chip saem os recados internos" — e duas respostas pra ela
-- seria uma a mais.
--
-- Aditivo e idempotente. `default false`: mandar no WhatsApp de alguém não começa
-- ligado, e a régua inteira segue essa regra desde a 218.

alter table public.funil_regua
  add column if not exists fu_zap boolean not null default false;

comment on column public.funil_regua.fu_zap is
  'Manda o aviso de follow-up também no WhatsApp do vendedor. Nasce desligado; '
  'sai pelo chip de `distribuicao.aviso_zap_chip_id`.';

-- rollback:
--   alter table public.funil_regua drop column if exists fu_zap;
