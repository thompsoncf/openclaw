-- 340_novidade_visita_igual_e_card_na_agenda.sql
-- A visita conta igual no Raio-X, no Relatório e no resumo do grupo; a festa
-- aprovada nasce como festa; e o novo compromisso pergunta de qual card é.
--
-- DE ONDE VEIO (24/09/2026): o gestor da Prime pôs o Relatório → Funil e o Raio-X
-- lado a lado e achou 4 visitas do Pedro Yan num e 3 no outro. A visita da Renata
-- (05/09) foi digitada na Agenda sem card, e o Raio-X só contava visita com card.
-- A régua única mora em finance/visita.py.
--
-- DOIS AVISOS, porque o alcance é diferente (regra 5 e 6 do CLAUDE.md):
--
--   * as VISITAS e a FESTA são palavra de quem vende festa: público `eventos`,
--     pra dono e gestor (é o número que eles leem);
--   * o campo "Card do funil" aparece em toda conta que tem funil: público
--     `servico`, e vai pro VENDEDOR também, porque muda o jeito de marcar
--     compromisso no app dele.
--
-- Aditivo e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('visita-conta-igual-em-toda-tela', 'mudanca', 'eventos', '{dono,gestor}',
 'As visitas agora batem entre o Raio-X e o Relatório',
 'O Raio-X, o Relatório e o resumo semanal passaram a contar as visitas pela mesma regra, incluindo as marcadas sem card no funil.',
 '/painel/raio-x',
 $txt$O Raio-X e o Relatório → Funil contavam as visitas de jeitos diferentes, e os números não batiam.

COMO ERA

O Raio-X só contava a visita ligada a um card do funil. A visita marcada direto na Agenda, sem card, não entrava — e o Relatório contava. O Relatório dava a visita pra quem marcou; o Raio-X, pro dono do card.

E a festa que nascia da aprovação do orçamento entrava na Agenda sem dizer que era festa: depois do dia, o Raio-X passava a contá-la como visita sem resposta.

COMO FICOU

Visita é o compromisso que começa com "Visita" ou que está ligado a um card, e nunca a festa. Com ou sem card, ela conta — no Raio-X, no Relatório, no resumo semanal e no Cockpit.

A visita é do vendedor dono do card. Sem card, é de quem marcou.

No Raio-X, o rodapé das visitas diz quantas ainda vão acontecer no período, e "Da visita ao contrato" mostra à parte as visitas sem card no funil — ligar ao card é o que diz se viraram orçamento.

A festa aprovada agora nasce na Agenda como festa, ligada ao card e ao cliente.$txt$,
 timestamptz '2026-09-25 03:00:00+00'),
('agenda-pergunta-de-qual-card-e', 'novidade', 'servico', '{dono,gestor,vendedor}',
 'Novo compromisso: diga de qual card é',
 'Ao marcar um compromisso na Agenda ou no app, dá pra dizer de qual cliente do funil ele é — e ele passa a contar pro vendedor certo.',
 '/painel/agenda',
 $txt$O formulário de novo compromisso ganhou o campo "Card do funil".

POR QUÊ

O compromisso marcado na Agenda nascia solto, sem saber de qual cliente era. Aí ele não aparecia no card do cliente, e nos números contava pra quem marcou — não pro vendedor do cliente.

COMO USAR

Na Agenda do painel, comece a digitar o nome ou o telefone do cliente e escolha na lista. No app, escolha o cliente na lista "De qual cliente". É opcional: reunião interna não tem card.

Quando a proposta é aprovada ou o contrato é assinado e o cliente ainda não tinha card no funil, o card agora nasce sozinho, com o vendedor que fez o orçamento.$txt$,
 timestamptz '2026-09-25 03:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave in ('visita-conta-igual-em-toda-tela',
--                                                'agenda-pergunta-de-qual-card-e');
