-- 640_novidade_clinica_funil_tela.sql
-- O aviso da tela "Funil da clínica" (rascunho "Ligar o funil da Pelle", decisões A a D,
-- aprovado em 03/10/2026), seguindo a seção 5 do CLAUDE.md. Número 640 com folga (regra
-- de numeração: o maior em uso era 610, o das travas).
--
-- PÚBLICO `clinica`. PRA QUEM: dono e gestor (só eles aplicam o funil e ligam as regras).
-- QUEM RECEBE, conferido na produção em 03/10/2026 (só leitura, nicho clinica):
--   39 Espaço Pelle Clínica Dermatologica Ltda (a única conta do nicho; o funil dela ainda
--   tem as colunas antigas)
--
-- Sem schema novo. Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('clinica-funil-tela', 'novidade', 'clinica', '{dono,gestor}',
 'Funil da clínica: aplicar e ligar numa tela só',
 'O funil aprovado (Consulta, Em tratamento, Retorno) e o que anda sozinho, em três passos, com ensaio antes de ligar.',
 '/painel/clinica/funil',
 $txt$O funil da clínica agora se aplica numa tela só, em vez da lista item por item da Régua.

OS TRÊS PASSOS

1. As colunas: Consulta, Em tratamento e Retorno, e os nomes novos (Em conversa, Agendado, Plano ou orçamento enviado, Concluído). Tudo do desenho aprovado já vem marcado, inclusive os nomes que você tinha dado. Nenhum cartão muda de coluna.
2. O que anda sozinho, cada regra em desligado, ensaio ou ligado:
   - a primeira resposta nossa leva o cartão para Em conversa;
   - o prazo de Em conversa: 3 dias e 2 renovações, com justificativa;
   - o Perdido volta para Em conversa quando a pessoa escreve.
   Em ensaio a regra não mexe em nada: a tela conta o que ela teria feito nos últimos 7 dias, e você liga quando os números fizerem sentido.
3. Conferir e aplicar.

As mensagens automáticas (chamar de novo, resgate) continuam desligadas: vêm nas próximas entregas, depois da recepção.$txt$,
 timestamptz '2026-10-03 15:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'clinica-funil-tela';
