-- 217_novidade_lista_espera.sql
-- O aviso da lista de espera por data (seção 5 do CLAUDE.md). Precisa da 199.
--
-- QUEM RECEBE
--   lista-de-espera-por-data (eventos · dono, gestor, vendedor)
--     Só quem VENDE FESTA (portão 'eventos' = tem contrato de locação, o mesmo
--     que decide o modo do orçamento). Quem vende mensalidade não tem data pra
--     disputar, e a seção 6 do CLAUDE.md manda mirar pelo nicho. Hoje alcança
--     Prime Eventos e Doce Mell; a tela em si só liga na conta que preencher
--     "festas por dia" em Empresa — a Prime, nesta rodada.
--
-- Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values

('lista-de-espera-por-data', 'novidade', 'eventos', '{dono,gestor,vendedor}',
 'Lista de espera por data: quando o dia abre, você fica sabendo',
 'Quem pede uma data que a empresa já vendeu entra numa lista. Quando a data abre, por cancelamento ou pré-reserva vencida, o vendedor é avisado na hora.',
 '/painel/agenda',
 $txt$O cliente pede um sábado que já foi vendido, o vendedor vê o dia ocupado na agenda e a conversa morre ali. Agora não.

Na tela do lead, quando a data pedida já tem festa, aparece "Data tomada" e, junto, as três datas livres mais próximas — a resposta pronta pra oferecer sem sair da conversa. O cliente entra na lista de espera daquele dia sozinho, sem ninguém digitar nada.

Quando a data abre — a festa foi cancelada, ou a pré-reserva venceu sem o sinal — quem esperava vira a primeira linha do "Responda hoje", em verde, com push no celular do vendedor. O dono recebe o resumo, do mesmo jeito que já recebe quando uma pré-reserva vence.

No painel, a Agenda ganhou o card "Lista de espera": uma linha por data, quem espera, desde quando e com qual vendedor. As linhas verdes são as datas que abriram.

Pra ligar: em Empresa, preencha "Festas por dia" — quantas festas você faz no mesmo dia. Com 1, uma festa toma o dia. Em branco, a lista não funciona e nada muda.

O Zaq avisa; quem vende é o vendedor. Nada é reservado automaticamente, e ninguém perde a vez por ordem de chegada.$txt$,
 timestamptz '2026-09-06 12:00:00+00')

on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'lista-de-espera-por-data';
