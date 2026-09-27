-- 414_novidade_ficha_campos.sql
-- O aviso dos campos novos da ficha, do check-in no balcão e das etiquetas (migração 413),
-- seguindo a seção 5 do CLAUDE.md.
--
-- PÚBLICO `clinica`. PRA QUEM: dono, gestor e vendedor (a recepção preenche a ficha).
-- QUEM RECEBE, conferido na produção em 27/09/2026 (só leitura, nicho clinica):
--   39 Espaço Pelle Clínica Dermatologica Ltda (a única conta do nicho)
--
-- Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('clinica-ficha-campos', 'novidade', 'clinica', '{dono,gestor,vendedor}',
 'Ficha do paciente: os campos do Amigo, o check-in no balcão e as etiquetas',
 'Nome social, sexo, profissão, RG, nome da mãe, endereço completo e contato de emergência; o paciente que chega sem ter preenchido faz no tablet; e etiquetas com filtro na lista.',
 '/painel/clinica/pacientes',
 $txt$A ficha do paciente ficou com o que a clínica usava no Amigo.

OS CAMPOS

- Nome social (aparece no alto da ficha), sexo, profissão, RG e nome da mãe.
- O endereço separado em rua, número, complemento e bairro (o que a nota da prefeitura pede).
- Contato de emergência, com o telefone.
- O paciente também preenche os principais pelo link da ficha.

Alergia e tipo sanguíneo ficam no prontuário, com o profissional. A recepção continua vendo o aviso de alergia da pré-consulta.

CHECK-IN NO BALCÃO

O paciente chegou sem ter preenchido o link? Na ficha dele (ou no agendamento), toque em "Preencher no balcão": aparece um QR que vale uma vez, por 15 minutos. Ele abre no tablet da clínica ou no próprio celular, preenche cadastro, pré-consulta e termos, e a ficha fecha sozinha no fim. Use um tablet que NÃO esteja logado no Zaq.

ETIQUETAS

Marque os pacientes como quiser (VIP, pós-operatório…) na aba Cadastro. A lista ganhou o filtro por etiqueta.$txt$,
 timestamptz '2026-09-27 16:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'clinica-ficha-campos';
