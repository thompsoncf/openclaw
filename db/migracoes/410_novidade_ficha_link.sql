-- 410_novidade_ficha_link.sql
-- O aviso do link "complete sua ficha" (finance/clinica_ficha_link.py, /ficha/{token}),
-- seguindo a seção 5 do CLAUDE.md.
--
-- PÚBLICO `clinica`. PRA QUEM: dono, gestor e vendedor (a recepção cobra a ficha).
-- QUEM RECEBE, conferido na produção em 27/09/2026 (só leitura, nicho clinica):
--   39 Espaço Pelle Clínica Dermatologica Ltda (a única conta do nicho)
--
-- Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('clinica-ficha-link', 'novidade', 'clinica', '{dono,gestor,vendedor}',
 'O paciente completa a ficha no celular, antes da consulta',
 'A confirmação do horário leva o link: cadastro com CPF, pré-consulta e termos. A agenda mostra de quem falta o quê.',
 '/painel/clinica/agenda',
 $txt$A ficha que nasce quando a consulta é marcada agora se completa sozinha.

O LINK

- A confirmação do horário leva "complete sua ficha antes da consulta". Se faltar algo, a mensagem da véspera lembra do mesmo link (não é uma mensagem a mais).
- O paciente abre com a data de nascimento e preenche em 3 minutos: cadastro (com o CPF que vai na nota), a pré-consulta e os termos de uso de dados e de imagem.
- Menor de idade: o responsável preenche, aceita os termos e informa o CPF dele.

QUEM VÊ A PRÉ-CONSULTA

Só os profissionais de saúde da clínica, com login. A recepção vê que foi respondida e o aviso de alergia, sem o texto.

NA AGENDA E NA LISTA

- Cada horário mostra "ficha completa" ou "ficha 60%".
- A lista de pacientes ganhou o filtro "Ficha incompleta".
- Na chegada, falta o CPF? O agendamento avisa antes de receber.

PARA LIGAR

Agenda › Link da ficha. Leia os termos antes: o link nasce desligado.$txt$,
 timestamptz '2026-09-27 16:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'clinica-ficha-link';
