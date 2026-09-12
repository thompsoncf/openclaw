-- 249_novidade_perda_no_app.sql
-- O aviso de que o app do vendedor passou a mostrar a lista de perda DA EMPRESA
-- (CLAUDE.md §5).
--
-- PÚBLICO 'todos' (§6): perder lead é de qualquer ramo, e o texto não nomeia festa,
-- visita nem mensalidade. A lista que cada vendedor vai ver é a da empresa dele.
--
-- PRA QUEM: vendedor em primeiro lugar — é a tela dele que mudou, e é ele quem
-- marca o lead como perdido. Dono e gestor entram porque são eles que editam a
-- lista e que ligam a obrigatoriedade na Régua, e precisam saber que agora o app
-- obedece as duas coisas.
--
-- Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values

('perda-no-app-do-vendedor', 'novidade', 'todos', '{vendedor,gestor,dono}',
 'No app, "Por que perdeu?" passa a listar os motivos da sua empresa',
 'O aplicativo do vendedor passa a mostrar a lista de motivos de perda cadastrada pela empresa — a mesma do painel —, com espaço para explicar quando o motivo pedir.',
 '/cockpit',
 $txt$O app do vendedor tinha uma lista própria de motivos de perda, de seis opções, escrita dentro do programa. O painel, desde a virada da lista, já mostrava a lista DA EMPRESA — aquela que o dono edita e pode aumentar quando quiser.

As duas viviam saindo do lugar. Numa empresa com dez motivos cadastrados, o vendedor abria o celular e via seis — dois deles nem existiam mais na lista dela, e seis dos que ela usa de verdade não apareciam. Enquanto o motivo era opcional isso passava sem barulho. No dia em que a empresa tornou o motivo obrigatório, virou uma parede: o vendedor escolhia uma opção que o sistema recusava, e as que ele precisava não estavam na tela.

O que muda agora, no celular:

A lista é a da sua empresa. A mesma do painel, na mesma ordem. Motivo que a empresa desligou não aparece mais — antes ele podia aparecer e ser recusado depois.

Motivo que pede explicação agora tem onde escrever. Alguns motivos (como "Outro") pedem uma linha contando o que houve. O campo faltava no app: dava para escolher e não dava para explicar, e o sistema devolvia um erro sem saída. Agora o campo está ali, no mesmo formulário.

E quando faltar alguma coisa, o app diz o que faltou em português, e não mais um código.

Uma outra correção na mesma tela: as etapas de DEPOIS da venda — as que a empresa marcou para sair do quadro — não aparecem mais entre os botões de etapa. Elas estavam lado a lado com as etapas normais, e um toque sem querer tirava o lead da sua fila sem passar por Ganho nem por Perdido. Se um lead já estiver numa delas, ela continua aparecendo, marcada, para você ver onde ele está.$txt$,
 timestamptz '2026-09-12 23:55:00+00')

on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'perda-no-app-do-vendedor';
