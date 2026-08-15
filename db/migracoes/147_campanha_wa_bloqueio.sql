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

-- Alvos QUEIMADOS por falha de configuração da CONTA voltam pra fila.
--
-- A fila de disparo é `where wa_status is null`. Cada alvo que falhava levava
-- wa_status='erro' e saía da fila PRA SEMPRE — mesmo quando a culpa não era dele.
-- Erro de config é da conta, não do lead: agora o motor trava a campanha inteira
-- (wa_bloqueio acima) em vez de marcar alvo por alvo. Aqui desfaz o já feito.
--
-- O que entra e o que NÃO entra:
--   * códigos 2xxxx são da API do Twilio e valem pra conta toda — 20003 é a
--     credencial não autenticando, que sozinha queimou 22 alvos de uma campanha;
--   * erros que o dispatcher devolve antes de chegar no provedor (conta no QR,
--     sem número, sem template) — ainda sem ocorrência em produção, mas há
--     campanha em rascunho nessa situação esperando o primeiro disparo;
--   * códigos 63xxx ficam de fora de propósito: são do WhatsApp e valem por
--     DESTINATÁRIO (63024 = o número não tem WhatsApp). Esses alvos falharam
--     por mérito próprio e não devem voltar.
--
-- Idempotente: depois da primeira passada o `where` não casa mais nada.
update public.campanha_alvos
   set wa_status=null, wa_erro_codigo=null, wa_erro_msg=null, wa_em=null
 where wa_status='erro'
   and (coalesce(wa_erro_msg,'') in ('provedor_sem_template', 'sem_numero_empresa',
                                     'nao_configurado', 'sem_template')
        or coalesce(wa_erro_codigo,'') ~ '^2[0-9]{4}$');
