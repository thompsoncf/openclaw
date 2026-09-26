-- 374_novidade_lead_volta_na_data.sql
-- O aviso do lead perdido que volta sozinho na data que ele deu, seguindo a seção
-- 5 do CLAUDE.md: PR que muda tela leva o aviso, no mesmo PR. Precisa da 373 e da
-- 350 (o portão `construcao`). Desenho aprovado pelo dono em 25/09/2026:
-- docs/mockups/nicho_construcao.html, seção 08.
--
-- PORTÃO `construcao`: o campo só aparece na ficha do lead das contas de obra.
--
-- QUEM RECEBE, conferido na produção em 26/09/2026 (contas × nichos, só leitura):
--   conta 33 · Pablo Thyago G. Dias / PX2 Empreendimentos · Lago da Pedra-MA
--
-- PRA QUEM: dono, gestor e vendedor (quem mexe no funil).
--
-- Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values

('funil-lead-volta-na-data', 'novidade', 'construcao', '{dono,gestor,vendedor}',
 'O comprador com nome sujo volta sozinho',
 'Perdeu a venda porque o comprador está com restrição no CPF? Anote a data que ele deu pra limpar o nome: nesse dia o lead volta sozinho pro Follow-up.',
 '/painel/prospeccao',
 $txt$Na casa do Minha Casa Minha Vida, quem reprova é a Caixa, não o cliente. O comprador com restrição no CPF que diz "limpo o nome até dezembro" não é venda perdida — é venda adiada. O problema é lembrar de voltar.

NA FICHA DO LEAD

Ao marcar como perdido (restrição no CPF, adiou a compra, o que for), preencha "Voltar a procurar em" com a data que ele deu.

NO DIA

O lead sai de Perdido e volta pro Follow-up sozinho, com o próximo contato marcado pra aquele dia — aparece na fila de quem atende. O motivo da perda antiga fica no histórico, e a conversa ganha uma nota dizendo por que ele voltou.

Sem data, nada muda: o perdido continua perdido.$txt$,
 timestamptz '2026-09-26 21:00:00+00')

on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'funil-lead-volta-na-data';
