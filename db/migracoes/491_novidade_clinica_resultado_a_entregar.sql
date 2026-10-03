-- 491_novidade_clinica_resultado_a_entregar.sql
-- O aviso da entrega 1d do CRM da clínica (docs/mockups/clinica_crm_telas.html, seções 01
-- e 05, aprovado em 01/10/2026), seguindo a seção 5 do CLAUDE.md.
--
-- PÚBLICO `clinica`. PRA QUEM: dono, gestor e vendedor (a recepção finaliza o
-- atendimento e cuida da fila de resultados).
-- QUEM RECEBE, conferido na produção em 01/10/2026 (só leitura, nicho clinica):
--   39 Espaço Pelle Clínica Dermatologica Ltda (a única conta do nicho)
--
-- Sem schema novo (a tabela é a 490). Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('clinica-resultado-a-entregar', 'novidade', 'clinica', '{dono,gestor,vendedor}',
 'Resultado de exame a entregar',
 'Ao finalizar, marque se há resultado a entregar (biópsia, coleta, exame). O paciente só conclui depois da entrega, e a recepção acompanha a fila.',
 '/painel/clinica/pacotes',
 $txt$O Finalizar ganhou a terceira pergunta: há resultado a entregar?

COMO FUNCIONA

- Ao finalizar, marque "Resultado a entregar" e, se o laboratório disse, a data prevista.
- No funil novo, o cartão do paciente espera na coluna Retorno até a entrega, em vez de ir para Concluído.
- Em Agenda › Pacotes e retornos, a fila "Resultados a entregar": clique em "Chegou" quando o laboratório devolver, marque a entrega com o paciente e clique em "Entregue" depois.
- A tela Hoje avisa o resultado que chegou e o que passou da data prevista sem chegar.
- Entregue (ou tirado da fila), o cartão vai para Concluído, se não houver retorno a fazer.

O nome do exame não aparece na fila nem na tela Hoje: só o paciente e a situação.$txt$,
 timestamptz '2026-10-02 13:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'clinica-resultado-a-entregar';
