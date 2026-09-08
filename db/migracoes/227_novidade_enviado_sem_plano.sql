-- 227_novidade_enviado_sem_plano.sql
-- Serviços › funil: o aviso de plano de pagamento passa a valer ANTES de fechar,
-- e para de aparecer em quem vende por mensalidade.
--
-- DUAS MUDANÇAS, e uma delas é conserto do dia anterior.
--
-- 1) O AVISO CHEGOU MAIS CEDO (pedido do dono em 08/09/2026, "sim estende o aviso
--    pro enviado sem plano também"). Proposta que JÁ FOI AO CLIENTE — enviada, em
--    negociação ou aprovada — e está sem plano de pagamento ganha o selo âmbar
--    "Enviado sem plano de pagamento". O de 'fechado' continua como estava, com
--    texto próprio: lá o título único já nasceu e o conserto é remendo.
--    Na conta 34 são duas: nº 19 (Kleiton) e nº 22 (Renata Tatiana).
--    Rascunho segue calado — proposta em construção sem plano é o normal —, e o
--    que ainda não saiu de casa também: é o cliente TER RECEBIDO que transforma a
--    falta em problema.
--
-- 2) CONSERTO DO #662, do mesmo dia. Aquele PR mediu só a Prime (34, eventos) e
--    esqueceu o portão do MODO. Só no modo 'evento' o plano mora em
--    `orcamentos.parcelas`; no 'recorrente' ele é setup + mensalidade, e
--    `fechar_orcamento` nem lê `parcelas` nesse modo. Resultado: a conta 3 (ZAQ)
--    tinha UMA proposta recorrente fechada, e ela passou a acusar falta de um
--    plano que nunca teve. Conferido na produção em 08/09/2026:
--        conta  3 (ZAQ)   recorrente · 1 fechada, 1 aprovada, 13 rascunhos
--                         — todas sem `parcelas`, todas legítimas
--        conta 34 (Prime) evento     · 4 fechadas (todas COM plano),
--                         3 enviadas (2 sem plano), 11 rascunhos
--    O portão novo (`modo == 'evento'`) apaga o selo falso da ZAQ e não muda nada
--    na Prime. Falha fechada: quem não informa o modo não recebe o aviso.
--
-- O PORTÃO: `servico`. O funil é do módulo Serviços. Não é por nicho de eventos —
-- o aviso agora EXCLUI o recorrente por dentro, e é justamente essa exclusão que
-- interessa a quem vende por mensalidade.
--
-- PRA QUEM: dono e gestor. O vendedor não opera contas a receber nem monta plano.
--
-- Aditivo e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('funil-avisa-enviado-sem-plano', 'novidade', 'servico', '{dono,gestor}',
 'O aviso de plano de pagamento agora chega antes de fechar',
 'Proposta que já foi ao cliente sem plano de pagamento passou a mostrar aviso no funil, em vez de só depois de fechada — e o aviso deixou de aparecer para quem vende por mensalidade, onde plano em parcelas não existe.',
 '/painel/servicos',
 $txt$Ontem o funil passou a avisar quando um negócio fechava sem plano de pagamento. O aviso estava certo, mas chegava tarde: depois de fechado, o contas a receber já virou um título único do valor total, e arrumar vira remendo.

AGORA ELE CHEGA ANTES. Proposta que já foi ao cliente — enviada, em negociação ou aprovada — e ainda está sem plano de pagamento mostra o selo "Enviado sem plano de pagamento". É o momento em que dá pra montar o plano e mandar de novo, sem nada pra desfazer.

Rascunho continua calado: proposta em construção sem plano é o normal. O que ainda não saiu de casa também — é o cliente ter recebido que transforma a falta em problema.

E UM CONSERTO, de um erro que durou um dia. O aviso de ontem aparecia também para quem vende por mensalidade, e ali ele não fazia sentido nenhum: nesse formato o plano de pagamento é a implantação mais a mensalidade, não uma lista de parcelas — então "sem plano" era o estado correto, não uma falha. Quem viu esse selo numa proposta de mensalidade pode ignorar: ele não aparece mais.$txt$,
 timestamptz '2026-09-08 22:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'funil-avisa-enviado-sem-plano';
