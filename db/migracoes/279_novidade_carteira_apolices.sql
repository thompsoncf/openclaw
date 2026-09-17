-- 279_novidade_carteira_apolices.sql
-- A carteira de apólices e o aviso de renovação (tela Renovações).
--
-- O PEDIDO (dono, 17/09/2026), com uma proposta da Allianz em mãos: "tem dados
-- suficiente pra montar o banco de clientes com informações importantes para gerar
-- o alerta". Mockup aprovado em docs/mockups/apolices_alerta_renovacao.html; as
-- quatro decisões dele, verbatim: "60/30/15, percentual por seguradora, alerta pro
-- corretor, começa por auto". Tabelas na migração 278.
--
-- O QUE MUDOU NA TELA
-- 1) Menu novo: **Renovações** (/painel/renovacoes), só pra conta de corretora de
--    seguros. Lista o que vence nos próximos 90 dias, com quanto falta, prêmio e
--    comissão estimada, e deixa mudar a situação da apólice num toque.
-- 2) Cadastro de apólice (auto) e cadastro do percentual de comissão por
--    seguradora, no mesmo lugar — dono e gestor.
-- 3) Push no celular do corretor faltando 60, 30 e 15 dias pra apólice vencer.
--
-- O PORTÃO: `seguros`. É a tela mais específica de nicho que já entrou — falar de
-- apólice, prêmio e classe de bônus em conta de festa seria o erro que a §6 nasceu
-- pra impedir.
--
-- PRA QUEM: dono e gestor (cadastram e veem a carteira inteira) e vendedor — aqui
-- ele é o CORRETOR, e é justamente ele quem recebe o aviso no celular. Aviso de
-- tela que o vendedor não tem, nunca; esta ele tem.
--
-- Aditivo e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('carteira-de-apolices', 'novidade', 'seguros', '{dono,gestor,vendedor}',
 'A carteira de apólices avisa quando a renovação está chegando',
 'A corretora cadastra as apólices e o sistema passa a avisar o corretor no celular faltando 60, 30 e 15 dias pro fim da vigência — com prêmio, comissão estimada e classe de bônus na mão.',
 '/painel/renovacoes',
 $txt$Tem uma tela nova no menu: RENOVAÇÕES.

Seguro não se vende uma vez. Vende-se todo ano, pro mesmo cliente, na mesma data — e essa data já está marcada no calendário desde o dia em que a apólice foi emitida. O que faltava era o sistema saber dela.

O QUE A TELA MOSTRA: tudo que vence nos próximos 90 dias, do mais urgente pro mais folgado. Cada linha traz o cliente, a seguradora, o ramo, quanto falta, o prêmio, a comissão estimada e a classe de bônus — que é o argumento da conversa de renovação.

O AVISO CHEGA NO CELULAR três vezes por apólice: faltando 60 dias, 30 dias e 15 dias. Vai pro corretor responsável pela apólice; se ela não tem corretor marcado, vai pro dono e pros gestores.

POR QUE TRÊS E NÃO CINCO. O mercado usa uma escada de 90/60/45/30/15. Cinco avisos sobre a mesma apólice viram ruído até a pessoa ignorar todos — então a lista vai até 90 dias (é consulta: "o que vem por aí") e o aviso só sai nos três degraus. Entre 90 e 60 dias a apólice aparece na tela e não interrompe ninguém.

A COMISSÃO. Ela não vem escrita na apólice — é papel do cliente, e o cliente não vê quanto o corretor ganha. Então cadastre o percentual por seguradora uma vez ("Allianz 20%") e toda apólice daquela seguradora passa a mostrar a comissão estimada. Dá pra detalhar por ramo, onde o percentual foge do padrão, e dá pra sobrescrever numa apólice específica. O percentual incide sobre o prêmio LÍQUIDO, sem o IOF: o IOF é imposto repassado ao governo e não entra em comissão.

O CADASTRO COMEÇA POR AUTO, que é o que a carteira tem hoje: placa, modelo, ano, chassi e classe de bônus. Vida, residencial e os outros ramos já gravam — o que falta pra eles é o formulário, não o cadastro.

UMA COISA QUE VALE SABER: ninguém é obrigado a avisar o cliente do vencimento. Nem a corretora, nem a seguradora. Quem tem prazo é a seguradora que NÃO quer renovar, e são 30 dias (Lei 15.040/2024). É por isso que avisar antes segura o cliente — quem liga faltando dois dias não está fazendo consultoria, está emitindo boleto.$txt$,
 timestamptz '2026-09-17 22:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'carteira-de-apolices';
