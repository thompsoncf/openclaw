-- 295_novidade_aviso_tres_canais.sql
-- O aviso da cobrança nos três canais, na hora certa — e o card contando de novo.
--
-- O QUE MUDOU:
--   * TRÊS CANAIS, sempre: WhatsApp, e-mail E push. A cobrança da manhã voltou a
--     ter push (ele ficou órfão quando a esteira tomou o lugar do follow-up), e o
--     fecho do dia passou a ter também.
--   * O FECHO CHEGA AO DONO SEM E-MAIL NO CADASTRO: além dos membros, ele sai
--     pros endereços já cadastrados em Empresa → resumo semanal (o do dono e o da
--     gestão). Endereço repetido recebe UMA vez.
--   * O AVISO NÃO SAI DE MADRUGADA. A cobrança respeita a janela de atendimento,
--     como a régua sempre fez. Nada de aviso às 00:0x nem em dia não útil.
--   * O CARD "Como os avisos chegaram" voltou a contar: ele media só o motor
--     antigo e, desde que a esteira assumiu, mostrava um número que definhava
--     enquanto o aviso saía todo dia.
--   * O FECHO DIZ QUANTOS FECHAM AMANHÃ, no total e por vendedor. "7 na esteira"
--     não separa o que é urgente do que tem prazo.
--   * O AVISO DO VENDEDOR pede o histórico: ligação e conversa pessoal não
--     existem no sistema, e quem resolveu no telefone sem escrever aparece como
--     quem não fez nada — e perde o lead no dia 7 por "sem tratativa".
--
-- POR QUE. Em 19/09/2026, montando o quadro de como o aviso chega hoje, três
-- coisas apareceram juntas: o push estava montado e medido sem nada passando por
-- ele; o MANOEL, dono da conta, é membro SEM e-mail — o fecho registrava "membro
-- sem e-mail cadastrado" e ia embora só pelo WhatsApp, com o endereço da empresa
-- cadastrado num campo ao lado; e a trava do aviso era só "já saiu hoje?", então
-- o primeiro ciclo do poller depois da meia-noite mandaria a cobrança no WhatsApp
-- dos vendedores às 00:0x — no domingo.
--
-- PRA QUEM: o vendedor volta a receber push da cobrança; dono e gestor ganham o
-- push do fecho, o e-mail no endereço cadastrado e o card de novo com números.
--
-- Aditivo e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('aviso-tres-canais', 'novidade', 'servico', '{dono,gestor,vendedor}',
 'A cobrança chega pelos três canais, na hora certa, e o fecho avisa quem vence amanhã',
 'O aviso da esteira volta a sair também por push, o fecho do dia passa a dizer quantos leads fecham amanhã e a chegar no e-mail cadastrado da empresa, e nenhuma cobrança sai de madrugada ou em dia não útil.',
 '/painel/follow-up',
 $txt$Três ajustes no mesmo lugar: por onde o aviso sai, a que hora, e o que a tela conta.

OS TRÊS CANAIS, SEMPRE

A cobrança da manhã sai por WhatsApp, e-mail e push. O push tinha ficado de fora quando a esteira tomou o lugar do motor antigo: continuava montado e medido no card, sem nada passando por ele. O fecho do dia, que era e-mail e WhatsApp, também ganhou push.

Cada canal sabe dizer uma coisa diferente, e a tela não finge o contrário: o WhatsApp tem entrega e leitura, o push tem o toque na notificação, o e-mail tem só "o servidor aceitou".

O FECHO CHEGA A QUEM NÃO TEM E-MAIL NO CADASTRO

Dono sem e-mail no cadastro de membro recebia o fecho só pelo WhatsApp — com o endereço da empresa cadastrado num campo ao lado. Agora o fecho também sai pros e-mails de Empresa → resumo semanal (o do dono e o da gestão). Quem já recebe como membro não recebe duas vezes.

NADA DE AVISO DE MADRUGADA

O dia da cobrança vira à meia-noite e o motor roda de minuto em minuto — então a cobrança saía no primeiro ciclo depois da meia-noite, inclusive em dia não útil. Agora ela respeita a janela de atendimento da empresa, como a régua sempre fez: ninguém entra na esteira, ninguém é cobrado e nada fecha fora do expediente.

O FECHO DIZ QUEM VENCE AMANHÃ

  📋 O dia fechou: 3 tratados, 7 na esteira, 2 fecham amanhã

  · THIAGO — 1 tratados, 4 na esteira (2 fecham amanhã)
  · PEDRO YAN — 2 tratados, 3 na esteira

"7 na esteira" não separa o que é urgente do que ainda tem prazo. O que fecha amanhã é a única parte do placar sobre a qual ainda dá pra fazer alguma coisa hoje — e dia em que ninguém vence não ganha linha nenhuma.

O HISTÓRICO É DO VENDEDOR, E O AVISO DIZ ISSO

Ligação por telefone e conversa pessoal não existem no sistema. Quem resolveu no telefone e não escreveu aparece no placar como quem não fez nada, e o lead fecha no dia 7 dizendo "sem tratativa".

Agora a cobrança da manhã termina assim:

  Resolveu por telefone ou pessoalmente? Escreva no histórico do lead — o que não está escrito não conta.

O CARD VOLTOU A CONTAR

"Como os avisos chegaram" media só o motor antigo. Desde que a esteira assumiu a cobrança, ele mostrava um número que ia definhando enquanto o aviso saía todo dia. Agora conta a cobrança de verdade, venha do motor que vier — e o ensaio do botão "Testar agora" continua de fora, como sempre esteve.$txt$,
 timestamptz '2026-09-19 19:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'aviso-tres-canais';
