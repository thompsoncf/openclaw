-- 408_novidade_pacientes.sql
-- O aviso da lista e da ficha de pacientes (finance/clinica_pacientes.py,
-- /painel/clinica/pacientes), seguindo a seção 5 do CLAUDE.md.
--
-- PÚBLICO `clinica`. PRA QUEM: dono, gestor e vendedor (a recepção usa a lista o dia todo).
-- QUEM RECEBE, conferido na produção em 26/09/2026 (só leitura, nicho clinica):
--   39 Espaço Pelle Clínica Dermatologica Ltda (a única conta do nicho)
--
-- Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('clinica-pacientes', 'novidade', 'clinica', '{dono,gestor,vendedor}',
 'Pacientes: a lista e a ficha de cada um, num lugar só',
 'Quem escreveu no WhatsApp, quem tem horário, quem está em tratamento e quem sumiu, com filtros e a ficha de cada paciente — e a mãe que marca pro filho gera a ficha do filho.',
 '/painel/clinica/pacientes',
 $txt$A clínica ganhou a tela Pacientes, no menu ao lado de Agenda e Hoje.

A LISTA

- Todo mundo num lugar só: quem escreveu no WhatsApp e ainda não veio (Novos contatos), quem tem horário, quem está em tratamento, quem tem retorno vencido, os assinantes e quem não vem há 6 meses.
- Busca por nome ou telefone (sem precisar de acento) e filtro por cidade.
- Em cada linha: agendar e abrir a ficha.

A FICHA

- Resumo, agenda, plano e pacotes, financeiro, produtos e cadastro.
- O cadastro guarda nascimento, CPF (o que vai na nota fiscal), cidade, endereço, como conheceu a clínica e o responsável de quem é menor de idade.
- Um aviso mostra o que falta completar.

UM WHATSAPP, VÁRIOS PACIENTES

Quando a mãe marca a consulta do filho pelo WhatsApp dela, o agendamento gera a ficha do filho, ligada ao mesmo número. Cada um tem a sua agenda e o seu tratamento.

E "cliente" e "lead" viraram "paciente" em todas as telas da clínica.$txt$,
 timestamptz '2026-09-27 12:30:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'clinica-pacientes';
