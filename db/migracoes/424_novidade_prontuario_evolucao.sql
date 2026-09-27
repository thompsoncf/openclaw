-- 424_novidade_prontuario_evolucao.sql
-- O aviso da fase 2 do prontuário (a ficha clínica e a evolução, migração 423),
-- seguindo a seção 5 do CLAUDE.md.
--
-- PÚBLICO `clinica`. PRA QUEM: dono e gestor (quem implanta; os profissionais usam).
-- QUEM RECEBE, conferido na produção em 27/09/2026 (só leitura, nicho clinica):
--   39 Espaço Pelle Clínica Dermatologica Ltda (a única conta do nicho)
--
-- Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('clinica-prontuario-evolucao', 'novidade', 'clinica', '{dono,gestor}',
 'O prontuário no Zaq: a ficha clínica e a evolução assinada',
 'O profissional abre o prontuário pela agenda, escreve a evolução pelo modelo (dermatologia, fisioterapia, procedimento ou texto livre) e assina; assinada, não muda mais — a correção é um adendo.',
 '/painel/clinica/pacientes',
 $txt$O prontuário chegou ao Zaq.

A TELA DO PACIENTE

- No alto, o que não pode ser esquecido: alergias, medicamentos em uso, problemas e antecedentes. Cada mudança fica guardada (quem e quando).
- A pré-consulta que o paciente contou aparece junto, com um clique para levar a alergia para a ficha.
- Embaixo, os atendimentos em ordem de data.

A EVOLUÇÃO

- Na agenda, com o paciente presente, o profissional daquele horário vê "Abrir prontuário".
- Modelos: dermatologia (queixa, exame, hipótese com CID opcional, conduta), fisioterapia dermatofuncional, procedimento (região, produto e lote, intercorrências) e texto livre.
- O rascunho se salva sozinho e só o autor vê. O "retorno em N dias" vira o retorno da agenda.
- Assinada, fica com a hora do servidor, o conselho do profissional e a impressão digital do texto: não muda mais. A correção é um adendo, também assinado. Nada se apaga.

QUEM LÊ

Só os profissionais que o dono liberou em Configurar › Profissionais. Cada abertura fica no registro de acesso. A assinatura com certificado digital (A1 ou nuvem) vem na próxima etapa.$txt$,
 timestamptz '2026-09-27 23:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'clinica-prontuario-evolucao';
