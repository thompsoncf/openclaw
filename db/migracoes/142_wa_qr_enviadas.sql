-- 142_wa_qr_enviadas.sql
-- Cache das mensagens que ENVIAMOS pelo WhatsApp por QR, pra conseguir reenviar
-- quando o aparelho do outro lado não consegue decifrar e pede retry.
--
-- Esse cache já existia, mas só em memória — e memória zera a cada deploy. Foi
-- exatamente o que aconteceu: mensagem enviada 07:48, deploy do serviço logo em
-- seguida, o pedido de reenvio chegou com o processo novo (cache vazio), o
-- Baileys não teve o conteúdo pra re-encriptar e a mensagem ficou eternamente
-- como "Aguardando mensagem" no celular do cliente. No banco ela aparece com
-- status 'enviado' e nunca 'entregue' — o ✓ que não vira ✓✓.
--
-- conteudo = o proto.Message serializado com o BufferJSON do Baileys (mesmo
-- formato usado em wa_qr_auth). Descartável por natureza: o serviço limpa o que
-- passa de 3 dias, porque pedido de reenvio chega em minutos/horas, não semanas.

create table if not exists public.wa_qr_enviadas (
  conta_id  bigint not null references public.contas(id) on delete cascade,
  msg_id    text   not null,
  conteudo  text   not null,
  criado_em timestamptz not null default now(),
  primary key (conta_id, msg_id)
);

create index if not exists ix_wa_qr_enviadas_criado on public.wa_qr_enviadas (criado_em);
