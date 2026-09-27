-- 420_novidade_prontuario_acesso.sql
-- O aviso da fase 1 do prontuário (quem vê o quê e o registro de acesso, migração 419),
-- seguindo a seção 5 do CLAUDE.md.
--
-- PÚBLICO `clinica`. PRA QUEM: dono e gestor (quem libera e quem vê o registro).
-- QUEM RECEBE, conferido na produção em 27/09/2026 (só leitura, nicho clinica):
--   39 Espaço Pelle Clínica Dermatologica Ltda (a única conta do nicho)
--
-- Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('clinica-prontuario-acesso', 'novidade', 'clinica', '{dono,gestor}',
 'Prontuário: só lê quem o dono liberou, e tudo fica registrado',
 'O conteúdo clínico abre só pra profissional de saúde que o dono liberou em Configurar › Profissionais; cada leitura fica no registro de acesso, que não se apaga.',
 '/painel/clinica/configurar',
 $txt$Começou o prontuário no Zaq, pela parte mais importante: quem pode ler.

QUEM LÊ

- O conteúdo clínico (hoje a pré-consulta; depois a evolução, as fotos e os documentos) abre só para PROFISSIONAL DE SAÚDE da clínica.
- Quem libera é o dono, em Configurar › Profissionais: o profissional precisa do conselho e número e do login dele ligado.
- O gestor não se libera. Trocar o login ou o conselho do profissional desliga, e o dono libera de novo.
- O dono que é médico marca "este profissional sou eu" e lê com o login de dono.
- A recepção vê a ficha e o aviso de alergia, nunca o conteúdo. O suporte do Zaq e o agente do WhatsApp nunca leem.

O REGISTRO DE ACESSO

- Toda leitura fica registrada: quem, de qual paciente, quando e o quê. O conteúdo nunca aparece no registro.
- O dono e o gestor veem tudo; o profissional, o dos pacientes que atendeu. Na ficha de cada paciente há o botão "Registro de acesso".
- Nada no registro se altera ou se apaga.$txt$,
 timestamptz '2026-09-27 21:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'clinica-prontuario-acesso';
