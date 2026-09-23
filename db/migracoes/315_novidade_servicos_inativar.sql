-- 315_novidade_servicos_inativar.sql
-- O "excluir" do catálogo de serviços virou "inativar" — e agora dá pra voltar.
--
-- O QUE MUDOU NA TELA:
--   * Serviços → proposta → "ver os N serviços em ordem alfabética": o botão 🗑
--     de cada linha virou ⊘ "Inativar serviço", e o aviso passa a dizer o que
--     realmente acontece (sai dos orçamentos NOVOS; os já feitos não mudam).
--   * No fim dessa mesma lista nasceu a gaveta "N serviços inativos", fechada.
--     Dentro dela, cada um tem o botão "reativar".
--   * Quem já tem um serviço ATIVO com nome parecido ganha um aviso âmbar na
--     linha e um segundo aviso ao reativar — pra não acabar com dois iguais na
--     lista do orçamento.
--
-- POR QUE. O botão nunca apagou nada: sempre foi `ativo=false`, pra não quebrar
-- o orçamento antigo que aponta pro slug. Só que o inativo sumia sem volta, e o
-- nome "excluir" assustava. Medido na Prime (conta 34) em 23/09/2026: quatro
-- serviços inativos — OUTROS, LOCAÇÃO, LOCAÇÃO DE GERADOR DE ENERGIA e
-- SEGURANÇA — e DOIS deles existem de novo entre os 42 ativos, recadastrados na
-- mão. Alguém desativou, não achou mais, e refez do zero.
--
-- PRA QUEM: dono, gestor e vendedor — é quem mexe no catálogo montando proposta.
--
-- O PORTÃO: `servico` — o catálogo é a tela de quem vende serviço, nos dois
-- modos (eventos e recorrente).
--
-- CONTAS ALCANÇADAS (contas × nichos, leitura em produção):
--   3 ZAQ - SISTEMAS IAs · 16 SUPER FIT · 21 MGB SOLUTIONS · 23 RAMO CAPITAL ·
--   30 PC CONTABILIDADE · 33 PX2 EMPRRENDIMENTOS · 34 PRIME EVENTOS ·
--   35 DOCE MELL · 37 LIBERAL NETO CORRETAGEM DE SEGUROS ·
--   39 ESPACO PELLE CLINICA DERMATOLOGICA
--
-- Aditivo e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('servicos-inativar', 'mudanca', 'servico', '{dono,gestor,vendedor}',
 'Serviço agora se inativa — e volta',
 'No catálogo de serviços, "excluir" virou "inativar", e os serviços inativos ganharam uma gaveta própria com botão de reativar.',
 '/painel/servicos',
 $txt$O serviço que você tira do catálogo não some mais pra sempre.

O QUE MUDOU

Em Serviços, abra uma proposta e clique em "ver os N serviços em ordem alfabética". O botão 🗑 de cada linha virou ⊘ "Inativar serviço".

O efeito é o mesmo de antes — e sempre foi. Nunca apagamos nada de verdade: o serviço só saía da lista, pra não quebrar os orçamentos antigos que já usavam ele. O que mudou é que agora a tela diz isso, e que dá pra voltar atrás.

A GAVETA DOS INATIVOS

No fim dessa mesma lista há agora "N serviços inativos", fechada. Abra e cada um tem o botão "reativar" — ele volta pra lista e pros orçamentos novos na hora.

O AVISO DO NOME PARECIDO

Se um serviço inativo tiver nome parecido com outro que está ativo, a linha mostra um aviso em âmbar dizendo qual é, e reativar pede uma confirmação a mais. É pra evitar o que já aconteceu: alguém não encontrava o serviço inativado, cadastrava outro igual, e os dois acabavam disputando espaço na mesma proposta.

O QUE NÃO MUDA

Nenhum orçamento já feito é afetado, nem ao inativar, nem ao reativar. O orçamento guarda o que foi vendido; o catálogo só decide o que aparece pra vender da próxima vez.$txt$,
 timestamptz '2026-09-23 23:05:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'servicos-inativar';
