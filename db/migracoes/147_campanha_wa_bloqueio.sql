-- Por que a campanha NÃO está disparando WhatsApp frio.
--
-- Antes, uma campanha sem template (ou numa conta que usa o WhatsApp por QR, que
-- não tem template nenhum) era pulada em silêncio pelo motor: nada no log, nada
-- na tela. O dono achava que estava rodando.
--
-- null = está tudo certo. Quem escreve é o motor a cada passada; a tela só lê.
alter table public.campanhas
  add column if not exists wa_bloqueio    text,
  add column if not exists wa_bloqueio_em timestamptz;

-- Alvos QUEIMADOS por falha de configuração da conta voltam pra fila.
--
-- A fila de disparo é `where wa_status is null`. Quando a conta estava no QR (ou
-- sem número/credencial), cada alvo levava wa_status='erro' e saía da fila PRA
-- SEMPRE — nem depois de configurar o Twilio ele voltava. Erro de config é da
-- conta, não do alvo: agora o motor trava a campanha inteira (wa_bloqueio acima)
-- em vez de marcar alvo por alvo. Aqui a gente desfaz o estrago que já foi feito.
--
-- Idempotente: depois da primeira passada o `where` não casa mais nada.
update public.campanha_alvos
   set wa_status=null, wa_erro_codigo=null, wa_erro_msg=null, wa_em=null
 where wa_status='erro'
   and coalesce(wa_erro_msg,'') in ('provedor_sem_template', 'sem_numero_empresa',
                                    'nao_configurado', 'sem_template');
