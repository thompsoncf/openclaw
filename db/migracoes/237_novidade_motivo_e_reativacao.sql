-- 237_novidade_motivo_e_reativacao.sql
-- O aviso do motivo de perda e da reativação (CLAUDE.md §5).
--
-- PÚBLICO 'todos' (§6): motivo de perda e reativação são mecânica de funil, de
-- qualquer nicho. O que veio de eventos foi a LISTA — e a lista é por conta, cada
-- uma nascendo com a do ramo dela. O texto não fala de festa nem de data.
--
-- PRA QUEM: dono, gestor e vendedor. O vendedor entra porque é ele que passa a ter
-- que dizer por que perdeu, e é a fila dele que recebe de volta o cliente que
-- voltou a falar. A lista em si é do dono, na Régua.
--
-- Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values

('motivo-de-perda-e-reativacao', 'novidade', 'todos', '{dono,gestor,vendedor}',
 'Por que perdemos vira uma lista sua — e o cliente que volta reabre o mesmo cadastro',
 'A lista de motivos de perda passa a ser da sua empresa: você liga, desliga, renomeia e acrescenta. Cada etapa pode exigir o motivo, e pode devolver para o funil quem voltar a falar.',
 '/painel/prospeccao/regua',
 $txt$Duas coisas novas, e as duas nascem desligadas.

A lista de "por que perdemos" era fixa no sistema, igual para todo mundo. Agora ela é da sua empresa. Na Régua, cada motivo tem nome, ordem e dois interruptores: se aparece na lista do vendedor e se pede um texto explicando. Você renomeia, reordena, desliga o que não usa e acrescenta o que for seu — "não aceitou o regulamento", "estacionamento", o que fizer sentido aí.

Sua conta já começa com a lista do seu ramo, pronta para usar.

Desligar um motivo tira ele da tela de quem vai encerrar um lead agora, mas não apaga de quem já foi perdido por ele — o relatório continua contando certo.

Cada etapa pode passar a exigir o motivo. Ligando isso no Perdido, o lead não entra lá sem alguém dizer por quê: a tela recusa, no painel e no aplicativo. Junto com o motivo ficam gravados a data da perda, a etapa de onde o lead saiu e quanto tempo ele ficou no funil.

A segunda: cada etapa pode dizer para onde o lead volta se o cliente reaparecer. Configurando o Perdido para devolver ao Contatado, quem foi dado como perdido e manda mensagem de novo reabre o mesmo cadastro, com o histórico inteiro, e volta para a fila do vendedor — sem ninguém precisar procurar e arrastar.

E nada é apagado. O motivo da perda continua registrado mesmo depois de o lead voltar: é exatamente isso que permite olhar depois e entender quantos clientes retornam, e de quais motivos.$txt$,
 timestamptz '2026-09-11 22:00:00+00')

on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'motivo-de-perda-e-reativacao';
