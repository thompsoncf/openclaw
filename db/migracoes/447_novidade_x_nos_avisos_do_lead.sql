-- 447_novidade_x_nos_avisos_do_lead.sql
-- O ✕ nos avisos acima da conversa (achado em produção, 28/09/2026, Martha),
-- seguindo a seção 5 do CLAUDE.md.
--
-- O QUE MUDOU NA TELA: na conversa do lead (app do vendedor), os avisos que
-- ficam acima do chat — "Data tomada" / "A data abriu" (lista de espera),
-- "O cliente falou de tal data" (pista) e "este número tem outra conversa"
-- (mesmo número, ficha diferente) — ganham um ✕ pra fechar. Um vendedor tinha
-- os três empilhados na tela de um lead de 200 convidados e achou que travava
-- o envio de mensagem pro cliente — não travava (o campo de escrever sempre
-- ficou embaixo, fixo), mas o empilhamento tomava a tela toda num celular.
--
-- O ✕ fecha só NAQUELA tela: não marca nada no banco. Se o motivo do aviso
-- ainda for verdade, ele volta no próximo carregamento da conversa.
--
-- PÚBLICO `todos` (os três avisos não são de um nicho só: pista e "outra
-- conversa" valem pra qualquer conta; "Data tomada" só aparece em quem usa a
-- lista de espera, mas o ✕ em si é a mesma peça de tela). PRA QUEM: vendedor
-- — é quem vive nessa tela.
--
-- Aditiva e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, link, corpo, publicado_em) values
('cockpit-x-nos-avisos-do-lead', 'mudanca', 'todos', '{vendedor}',
 'Dá pra fechar os avisos acima da conversa',
 '/cockpit',
 $txt$"Data tomada", "o cliente falou de tal data" e "este número tem outra conversa" agora têm um ✕ pra fechar.

Fecha só ali: não é "resolvido", é "não preciso ver agora" — se o motivo ainda for verdade, o aviso volta a aparecer da próxima vez que você abrir a conversa.$txt$,
 timestamptz '2026-09-28 19:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'cockpit-x-nos-avisos-do-lead';
