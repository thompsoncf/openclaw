-- 420_novidade_lista_de_espera_no_funil.sql
-- O aviso da migração 419 (a lista de espera no funil, parte 2b do funil novo de
-- eventos — docs/mockups/funil_novo_rotinas.html), seguindo a seção 5 do CLAUDE.md.
--
-- PÚBLICO `eventos` (§6): disputar data é de quem vende festa.
-- PRA QUEM: dono, gestor e vendedor — o vendedor ganha o selo, o botão e o aviso.
-- QUEM RECEBE: toda conta que vende festa. Na Prime (34) a lista já vem ligada (419,
-- 1 festa por dia); nas outras, o corpo diz onde ligar (Empresa › festas por dia).
--
-- Aditiva e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('lista-de-espera-no-funil', 'novidade', 'eventos', '{dono,gestor,vendedor}',
 'A lista de espera virou coluna do funil',
 'Quem vende festa vê no card quando o cliente pede uma data que outro já tem, põe na lista de espera com um toque, e o sistema devolve o card pra Proposta quando a data abrir.',
 '/painel/prospeccao',
 $txt$A lista de espera por data agora mora no funil:

- DATA OCUPADA: o card que pede uma data que outro cliente já tem ganha o selo "data ocupada". Ofereça outra data (o app mostra as livres mais perto).
- ESPERAR: se o cliente só quer aquela data e aceita esperar, toque em "esperar" no card (ou "O cliente aceita esperar esta data" no app). O card vai pra coluna Lista de espera e sai da cobrança: ninguém cobra quem está esperando.
- A DATA ABRIU: quando a reserva do outro vence ou a festa é cancelada, o 1º da fila volta pra Proposta e o vendedor é avisado. Se o cliente é da IA, a IA chama.
- A DATA PASSOU: quem esperava vai pra Perdido, "data indisponível".

Se o funil ainda não mostra os selos, diga em Empresa quantas festas vocês fazem por dia.$txt$,
 timestamptz '2026-09-28 11:45:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'lista-de-espera-no-funil';
