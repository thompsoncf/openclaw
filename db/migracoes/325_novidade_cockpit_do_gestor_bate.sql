-- 325_novidade_cockpit_do_gestor_bate.sql
-- O cockpit do gestor passa a dizer o mesmo que o resto do sistema.
--
-- O QUE MUDOU, na tela Equipe do app (/cockpit):
--   * "Fechado no período" e o PLACAR somam o valor do CONTRATO (o mesmo
--     `coalesce(primeiro_ano, setup)` que gera os títulos), e não o palpite
--     `prospeccao.valor_estimado_centavos`, que está zerado na base inteira.
--   * O FUNIL DO TIME usa as etapas e os nomes da conta, em vez de quatro chaves
--     fixas no código.
--   * "Parados há +3 dias" e "quentes sem contato hoje" olham a última mensagem
--     que saiu, e não só `ultimo_contato_em`.
--   * "Em atendimento" virou "Na carteira" — o número sempre foi a carteira
--     aberta inteira.
--
-- POR QUE. Medido na Prime em 24/09/2026, comparando a tela com o banco:
--   * "Fechado no período: R$ 0" com OITO contratos assinados na semana, todos
--     com orçamento fechado e títulos gerados — R$ 55.490. No Placar, os três
--     vendedores empatados em R$ 0, e o pódio decidido pelo desempate.
--   * O gestor lia "Qualificado" onde o painel diz "Agendado Visita" e
--     "Proposta" onde diz "Negociação"; "ORCAMENTO ASSINADO" não aparecia.
--     Mesmo defeito que a aba Leads corrigiu em 17/09 (migração 286), na tela de
--     cima, por uma constante esquecida. CLAUDE.md §6.
--   * "Parados há +3 dias: 363" — `ultimo_contato_em` está preenchido em 34 dos
--     423 leads abertos, então a conta caía no `criado_em`. 118 daqueles
--     "parados" tinham recebido mensagem nossa nos últimos três dias. Agora são
--     303, e é gente que a empresa realmente não procurou.
--
-- PRA QUEM: dono e gestor, que são quem abre essa tela.
--
-- Aditivo e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('cockpit-do-gestor-bate', 'novidade', 'servico', '{dono,gestor}',
 'O app da Equipe passa a mostrar o mesmo número que o painel',
 'O "fechado no período" e o placar agora somam o valor do contrato em vez de um campo que ninguém preenche, o funil usa os nomes que você deu às etapas, e "parados" olha a última mensagem que saiu.',
 '/cockpit',
 $txt$Quatro números da tela Equipe estavam medindo outra coisa. Agora medem o que dizem.

O DINHEIRO ERA O PIOR

"Fechado no período: R$ 0" — com oito contratos assinados na semana, cada um com orçamento fechado e títulos gerados. A tela somava o campo "valor estimado" da ficha do lead, que ninguém preenche; o dinheiro está no orçamento.

Agora ela soma o valor do contrato — o mesmo que vira título no contas a receber. Na prática, R$ 55 mil onde antes havia um zero. O Placar também: os vendedores estavam todos empatados em R$ 0, e quem aparecia em primeiro era só quem tinha mais leads na fila.

O FUNIL COM OS SEUS NOMES

Se você renomeou as etapas, o app mostrava os nomes de fábrica: "Qualificado" onde o painel diz "Agendado Visita", "Proposta" onde diz "Negociação" — e as etapas que você criou não apareciam. Agora a lista é a sua, na sua ordem.

"PARADOS" VIROU PARADO DE VERDADE

O alerta contava desde o cadastro do lead quando ninguém tinha mexido nele no painel — mesmo que a empresa estivesse conversando com a pessoa pelo WhatsApp todo dia. Agora ele olha a última mensagem que saiu: sai da lista quem foi procurado, fica quem não foi.

"EM ATENDIMENTO" VIROU "NA CARTEIRA"

O número nunca foi de quem está sendo atendido agora: é a carteira aberta inteira, com lead que ninguém encosta há semanas dentro. O número continua o mesmo; o nome é que parou de prometer atendimento.$txt$,
 timestamptz '2026-09-24 12:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'cockpit-do-gestor-bate';
