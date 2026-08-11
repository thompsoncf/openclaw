-- 132_convidado_canal_resposta.sql
-- Bug de produção: o "aviso antes da reunião" pro convidado decide se manda
-- texto livre (dentro da janela de 24h) ou template aprovado olhando só
-- respondido_em — mas respondido_em também é setado quando o convidado
-- confirma pela PÁGINA PÚBLICA (/convite/<token>, sem login), que não abre
-- NENHUMA sessão de WhatsApp. Resultado: pra quem confirma pela web (bem
-- comum), o sistema achava que a janela de 24h tava aberta, tentava texto
-- livre, o WhatsApp recusava (sem sessão de verdade) — e o convidado nunca
-- recebia nada, mesmo com o template aprovado configurado e funcionando.
--
-- respondido_canal ('web' | 'whatsapp') deixa claro qual foi o canal da
-- ÚLTIMA resposta — só uma resposta que veio de verdade pelo WhatsApp (botão
-- de quick-reply) abre a janela de 24h de texto livre. Idempotente.

alter table public.evento_convidados add column if not exists respondido_canal text;
