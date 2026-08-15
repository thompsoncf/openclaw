-- 159_mensagens_sid_por_conversa.sql
-- O id da mensagem do WhatsApp é único no MUNDO, não na nossa conta — e o índice
-- estava tratando como se fosse.
--
-- O QUE ACONTECIA
-- `provider_sid` é o id que o provedor dá à mensagem. No WhatsApp ele é o MESMO
-- para quem envia e para quem recebe: a mensagem "Oi" que sai do celular A para o
-- número B carrega um único id, e as duas pontas veem esse id. O índice era único
-- GLOBAL, e os inserts de entrada/saída/histórico usam
-- `on conflict (provider_sid) do nothing` pra não duplicar quando o Zaq envia e o
-- eco do Baileys volta logo depois.
--
-- Junte as duas coisas e some duas contas do MESMO Zaq conversando entre si:
--
--   1. a conta que enviou grava o eco (out) com o sid X;
--   2. a conta que recebeu chega com o mesmo sid X;
--   3. o `do nothing` descarta — e a mensagem NUNCA aparece pra quem recebeu.
--
-- Medido em produção em 15/08/2026: três mensagens ("Oi", "Testando", "🤖")
-- mandadas às 23:17:23/28/35 de um celular pareado numa conta pra o número de
-- outra. O log do serviço mostra `messages.upsert recebido` e
-- `entrada repassada ao webhook ✓` nos mesmos segundos, e a caixa de quem recebeu
-- ficou vazia. Silêncio total: o `do nothing` não erra, só não faz.
--
-- Vale pra qualquer par de contas nossas, e é justamente o caminho de TESTE — o
-- dono manda do próprio celular pra conta do cliente. Some também no e-mail, onde
-- o `message_id` é o mesmo pra todos os destinatários de uma mesma mensagem: lá o
-- insert não tem `on conflict`, então o segundo destinatário estourava
-- UniqueViolation em vez de sumir calado.
--
-- O CONSERTO
-- Único por (conversa_id, provider_sid). A duplicata que o `do nothing` existe pra
-- evitar é sempre na MESMA conversa (o eco da mensagem que acabou de sair), então
-- a proteção continua inteira; o que sai é a confusão entre contas diferentes.
--
-- O índice global veio da 082, e o motivo dele também sobrevive: lá ele protegia a
-- corrida entre os DOIS workers importando o mesmo e-mail — mesma conta, mesma
-- conversa, mesmo Message-ID. Esse caso continua barrado pelo par.
--
-- Cria o índice novo ANTES de derrubar o velho, e nessa ordem não há janela sem
-- proteção. É relaxamento puro: tudo que passava no índice global passa no novo
-- (global único ⇒ único por conversa), então não existe linha antiga que impeça a
-- criação. Aditivo e idempotente.

create unique index if not exists idx_mensagens_sid_conversa
    on public.mensagens (conversa_id, provider_sid)
    where provider_sid is not null;

drop index if exists public.idx_mensagens_provider_sid;

-- rollback: recriar o global só é possível se não houver sid repetido entre
-- conversas — e depois desta migração passa a haver, de propósito.
