-- 413_novidade_funil_novo_eventos.sql
-- O aviso da migração 412 (o funil novo de eventos, parte 1: as colunas e os
-- gatilhos — docs/mockups/funil_novo_eventos.html), seguindo a seção 5 do CLAUDE.md.
--
-- PÚBLICO `eventos` (quem vende festa: o modelo novo é só desse ramo, §6).
-- PRA QUEM: dono, gestor e vendedor — é o quadro onde o vendedor passa o dia, e as
-- colunas dele mudam (Qualificado, Visita feita, Data segurada, Pós-festa).
-- QUEM RECEBE: toda conta que vende festa. Na Prime (34) as colunas já vêm prontas
-- (migração 412, autorizada pelo dono); nas outras o modelo novo aparece como
-- proposta na faixa "colunas fora do modelo", e nada muda sem o dono marcar.
--
-- Aditiva e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('funil-novo-eventos', 'novidade', 'eventos', '{dono,gestor,vendedor}',
 'O funil de festa ganhou quatro colunas que andam sozinhas',
 'O funil de quem vende festa ganhou as colunas Qualificado, Visita feita, Data segurada e Pós-festa, que andam sozinhas conforme a conversa, a agenda e a aprovação do cliente.',
 '/painel/prospeccao',
 $txt$O funil de quem vende festa mudou. As colunas agora seguem o caminho do cliente, e andam sozinhas:

- QUALIFICADO: quando a ficha tem o tipo de festa, a data e os convidados.
- VISITA MARCADA: é a antiga "Agendado Visita", com outro nome.
- VISITA FEITA: quando a visita é marcada como realizada na agenda. Se o cliente faltou, o card volta pra Qualificado com a nota "remarcar". Faltar não é desistir.
- DATA SEGURADA: o cliente aprovou no link e a data fica segura esperando o sinal. O card continua no quadro até o contrato.
- PÓS-FESTA: no dia seguinte à festa, a venda volta pro quadro pra agradecer e pedir avaliação e indicação.

A coluna "Follow-up" saiu do quadro: a tela Follow-up continua fazendo esse papel. O Resgate da IA continua igual, na frente do quadro.

Se o seu funil ainda não tem essas colunas, a faixa "colunas fora do modelo", no topo do Funil, mostra o que muda, e você escolhe o que adotar.$txt$,
 timestamptz '2026-09-28 11:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'funil-novo-eventos';
