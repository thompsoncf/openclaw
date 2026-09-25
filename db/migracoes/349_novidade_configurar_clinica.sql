-- 349_novidade_configurar_clinica.sql
-- O aviso da tela Configurar › Clínica (web/painel_clinica.py, migração 348),
-- seguindo a seção 5 do CLAUDE.md.
--
-- PÚBLICO `clinica` (o portão da 347): a tela só abre pro perfil clínica.
-- PRA QUEM: dono e gestor — são os únicos que alcançam a tela.
-- QUEM RECEBE, conferido na produção em 25/09/2026 (só leitura, nicho clinica):
--   39 Espaço Pelle Clínica Dermatologica Ltda (a única conta do nicho)
--
-- Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('clinica-configurar', 'novidade', 'clinica', '{dono,gestor}',
 'Configurar a clínica: profissionais, atendimentos, locais e a grade de horário',
 'Clínicas ganharam o cadastro de quem atende, dos tipos de atendimento com duração e preço, dos locais e da grade de horário de cada profissional, com os horários livres calculados na hora.',
 '/painel/clinica/configurar',
 $txt$O menu ganhou o item Clínica. É o cadastro que a agenda e o agente vão usar para saber quem atende, onde, o quê, quando e quanto custa.

PROFISSIONAIS

Quem atende, com a função, a cor na agenda e os atendimentos que faz. O médico não precisa ter login: a agenda dele existe do mesmo jeito. Quem também entra no Zaq fica ligado ao cadastro da equipe.

ATENDIMENTOS

Cada tipo de atendimento com duração, categoria, cor e preço particular. Preço em branco é "sob consulta": o agente nunca inventa valor. O prazo de volta e o "por que volta" ficam guardados para o retorno programado, e são só para a recepção.

LOCAIS

A sede, com o endereço que vai na mensagem de confirmação, e as cidades onde a clínica atende.

GRADE

A semana de cada profissional: toda semana, a cada 15 dias ou uma vez por mês (por exemplo, a 3ª quinta), com encaixes. Os bloqueios cobrem congresso, férias e feriado, da clínica toda ou de uma pessoa. Cada profissional mostra os próximos horários livres, para conferir se a grade está certa.

JÁ VEM PREENCHIDO

A Espaço Pelle já abre com os profissionais, os atendimentos, a sede, as cidades e a grade do Dr. Manoel. No topo da tela aparece o que ainda falta confirmar.$txt$,
 timestamptz '2026-09-25 22:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'clinica-configurar';
