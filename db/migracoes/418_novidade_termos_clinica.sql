-- 418_novidade_termos_clinica.sql
-- O aviso dos termos da clínica (migração 417, finance/clinica_termos.py), seguindo a
-- seção 5 do CLAUDE.md.
--
-- PÚBLICO `clinica`. PRA QUEM: dono e gestor (quem escreve os termos).
-- QUEM RECEBE, conferido na produção em 27/09/2026 (só leitura, nicho clinica):
--   39 Espaço Pelle Clínica Dermatologica Ltda (a única conta do nicho)
--
-- Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('clinica-termos', 'novidade', 'clinica', '{dono,gestor}',
 'Os termos da clínica: o seu texto, o termo de cada procedimento e a cópia em PDF',
 'Escreva o termo de uso de dados e de imagem da clínica e um termo para cada procedimento; o paciente aceita no link da ficha e a cópia em PDF fica na ficha dele.',
 '/painel/clinica/termos',
 $txt$Os termos que o paciente aceita no link da ficha agora são da clínica.

O TEXTO DA CLÍNICA

- Em Agenda › Link da ficha › "Editar os termos", escreva o termo de uso de dados (LGPD) e o de uso de imagem. Sem texto da clínica, vale o padrão do Zaq.
- Use {clinica} e {paciente} no texto: o Zaq troca pelo nome de cada um.

UM TERMO POR PROCEDIMENTO

- Peeling, laser, botox…: um termo para cada atendimento que pede consentimento.
- Quando o próximo agendamento do paciente é desse atendimento, o termo aparece no link e conta para a ficha completa.

A CÓPIA EM PDF

- Cada aceite guarda o texto exato que a pessoa leu, quem aceitou (o paciente ou o responsável), a data, a hora e a versão.
- A recepção baixa o PDF na ficha do paciente (aba Cadastro); o paciente, no fim do link.
- Editar um termo cria uma versão nova: quem já aceitou fica com o texto que leu.

Confira os textos com o jurídico da clínica antes de ligar o link.$txt$,
 timestamptz '2026-09-27 19:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'clinica-termos';
