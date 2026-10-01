-- 476_novidade_clinica_agenda_pelo_tipo.sql
-- O aviso da entrega 1c do CRM da clínica (docs/mockups/clinica_crm_telas.html, seções
-- 01 e 02, aprovado em 01/10/2026), seguindo a seção 5 do CLAUDE.md.
--
-- PÚBLICO `clinica`. PRA QUEM: dono, gestor e vendedor (é a recepção que marca e
-- recebe o paciente).
-- QUEM RECEBE, conferido na produção em 01/10/2026 (só leitura, nicho clinica):
--   39 Espaço Pelle Clínica Dermatologica Ltda (a única conta do nicho; o catálogo dela
--   tem "Retorno", "Cortesia" e "Retirada teste alérgico" na categoria retorno)
--
-- Sem schema novo. Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('clinica-agenda-pelo-tipo', 'novidade', 'clinica', '{dono,gestor,vendedor}',
 'A agenda agora olha o tipo do horário',
 'Só consulta e avaliação levam o cartão para Consulta; só um horário de retorno dá o retorno por marcado; e consulta nova reabre o paciente que já tinha concluído.',
 '/painel/clinica/agenda',
 $txt$Vale para quem já aplicou o funil novo da clínica (Funil › Régua › "As etapas do funil"). O tipo do horário é a categoria do atendimento no catálogo (Configurar › Atendimentos).

O QUE MUDA

- Presente só leva o cartão para Consulta em horário de consulta ou avaliação. Exame, teste alérgico, vacina e procedimento avulso se resolvem no Finalizar: concluído, retorno ou plano.
- O retorno pedido pelo médico só sai da fila "a marcar" quando a recepção marca um horário do tipo retorno (Retorno, Cortesia, Retirada de teste alérgico). Antes, qualquer horário com o mesmo profissional fechava o retorno, e uma sessão de pacote o apagava.
- Marcar uma consulta nova para quem já tinha concluído traz o cartão de volta para Agendado. Retorno e sessão não reabrem.

Se a clínica não tiver nenhum atendimento da categoria retorno no catálogo, o retorno continua fechando com qualquer horário, como antes.$txt$,
 timestamptz '2026-10-02 12:30:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'clinica-agenda-pelo-tipo';
