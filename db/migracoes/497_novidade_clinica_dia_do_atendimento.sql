-- 497_novidade_clinica_dia_do_atendimento.sql
-- O aviso da entrega 2b do CRM da clínica (docs/mockups/clinica_crm_telas.html, seção 04,
-- aprovado em 01/10/2026; decisão J do dono em 02/10/2026), seguindo a seção 5 do CLAUDE.md.
--
-- PÚBLICO `clinica`. PRA QUEM: dono, gestor e vendedor (é a recepção que cuida da agenda).
-- QUEM RECEBE, conferido na produção em 02/10/2026 (só leitura, nicho clinica):
--   39 Espaço Pelle Clínica Dermatologica Ltda (a única conta do nicho)
--
-- Sem schema novo. Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('clinica-dia-do-atendimento', 'novidade', 'clinica', '{dono,gestor,vendedor}',
 'Atrasado, Faltou, Ligar e Remarcar pela cidade',
 'A agenda mostra quem está atrasado, só oferece o Faltou depois de 15 minutos, registra a ligação no card e remarca pelas próximas passagens pela cidade do paciente.',
 '/painel/clinica/agenda',
 $txt$O dia do atendimento ficou mais claro na agenda.

O QUE MUDA

- Atrasado: passados 15 minutos do horário sem o paciente chegar, a agenda mostra "atrasado N min". Quem decide a falta continua sendo a recepção.
- Faltou: o botão só aparece depois dos 15 minutos. A falta pode ser desfeita no mesmo dia (o paciente que chega depois da tolerância); para outro dia, use Remarcar.
- Ligar: no agendamento, registre como foi a ligação (atendeu, não atendeu, deixei recado). Fica na linha do tempo do card e conta como contato.
- Remarcar: mostra as próximas passagens do profissional pela cidade do paciente e, à parte, a sede.
- Desmarcar (o paciente avisou antes): é o antigo Cancelar. O cartão vai para Follow-up e a vaga é oferecida, como antes.$txt$,
 timestamptz '2026-10-02 19:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'clinica-dia-do-atendimento';
