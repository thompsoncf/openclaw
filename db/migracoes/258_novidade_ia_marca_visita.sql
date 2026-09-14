-- 258_novidade_ia_marca_visita.sql
-- O aviso da visita combinada pela IA (CLAUDE.md §5).
--
-- PÚBLICO 'eventos' (§6): visita ao espaço é vocabulário de quem vende festa com
-- data. Quem vende por mensalidade marca reunião, que é outra coisa e ainda não
-- existe — anunciar "visita ao espaço" pra essa conta seria prometer uma tela
-- que ela não tem.
--
-- E nem toda conta de eventos recebe visita: medido em 14/09/2026, a Prime tem
-- 46 pedidos em 510 conversas e a Doce Mell tem 0 em 361. Por isso a chave nasce
-- DESLIGADA e o aviso diz onde ligar, em vez de anunciar coisa ligada. Quem não
-- recebe visita lê e ignora; quem recebe vai ligar no mesmo dia.
--
-- PRA QUEM: dono, gestor E vendedor. O vendedor entra porque é ELE quem recebe o
-- cartão e confirma a visita no celular — muda a rotina dele. Ligar e desligar
-- continua sendo de quem configura o agente.
--
-- Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values

('ia-marca-visita', 'novidade', 'eventos', '{dono,gestor,vendedor}',
 'A IA combina a visita ao espaço dentro da conversa',
 'Quando o cliente pede pra conhecer o espaço, a IA oferece os horários que estão livres na agenda e deixa a visita pronta — o vendedor confirma num toque, ou a IA marca sozinha, como a empresa preferir.',
 '/painel/prospeccao/comunicacao?aba=agente',
 $txt$Até agora o cliente pedia pra conhecer o espaço e a IA respondia que ia passar pro time. A conversa seguia, e marcar dependia de alguém abrir o Cockpit e lembrar. Medimos numa conta de eventos: 46 clientes pediram pra visitar, 10 tinham visita na agenda.

Agora a IA resolve dentro da conversa. Ela olha a agenda, oferece só horários que estão de fato livres e, quando o cliente escolhe, deixa a visita encaminhada.

**Você escolhe até onde ela vai**, em Prospecção → Comunicação → Agente:

**Desligado** — como sempre foi: ela responde e passa pro time. É assim que a chave nasce.

**Propõe** — ela combina dia e hora com o cliente e manda o cartão pro vendedor, que confirma num toque. Nada entra na agenda sem uma pessoa dizer sim.

**Marca** — ela marca direto, manda pro cliente a confirmação com o endereço e o convite pro calendário, e avisa o vendedor.

Nos dois modos que agem, a visita segue o caminho de sempre: entra na agenda do vendedor dono do lead, liga no cadastro do cliente, move o lead pra "Qualificado" e manda a confirmação por WhatsApp. É o mesmo botão que já existia — só que apertado na hora certa.

**Fora do horário comercial a IA não marca.** Se o cliente pedir num domingo ou às dez da noite, ela responde sem prometer horário e avisa na hora o vendedor dono do lead — ou a gerência, se o lead ainda não tem dono. O pedido não se perde; muda de mãos.

E a IA nunca oferece horário ocupado: ela lê a agenda antes de falar, inclusive as datas seguradas. Se o vendedor abrir o cartão e o horário tiver sido tomado no meio-tempo, o cartão avisa antes de ele confirmar.$txt$,
 timestamptz '2026-09-14 20:00:00+00')

on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'ia-marca-visita';
