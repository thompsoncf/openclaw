-- 433_novidade_documentos.sql
-- O aviso da fase 5 do prontuário (documentos, migração 432), seguindo a seção 5 do
-- CLAUDE.md.
--
-- PÚBLICO `clinica`. PRA QUEM: dono, gestor e vendedor (a recepção manda o link).
-- QUEM RECEBE, conferido na produção em 27/09/2026 (só leitura, nicho clinica):
--   39 Espaço Pelle Clínica Dermatologica Ltda (a única conta do nicho)
--
-- Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('clinica-documentos', 'novidade', 'clinica', '{dono,gestor,vendedor}',
 'Receita, atestado e pedido de exame saem do prontuário',
 'O profissional emite no prontuário (receita, controle especial em 2 vias, atestado, declarações, pedido de exame, laudo, orientações) e o paciente recebe o link no WhatsApp, que abre com a data de nascimento.',
 '/painel/clinica/pacientes',
 $txt$Os documentos da consulta agora saem do Zaq.

NO PRONTUÁRIO

- Receita, receita de controle especial (2 vias), atestado, declaração de comparecimento, declaração de acompanhante, pedido de exame, laudo e orientações. Os modelos já vêm com o nome do paciente e a data.
- Emitido, o documento não muda: leva a hora, o conselho do profissional e um código de conferência. O PDF sai com o nome da clínica.
- Sem certificado digital (em breve), o PDF sai para imprimir e assinar à mão.
- Receita amarela ou azul continua no talão de papel: o Zaq só registra o número.

O ENVIO

- Um clique manda no WhatsApp o link da ficha, que abre com a data de nascimento; os documentos ficam lá por 30 dias.
- A recepção vê só o tipo do documento (nunca o conteúdo) e pode mandar, a pedido do profissional.
- O agente do WhatsApp nunca manda documento. Para mandar, o link da ficha precisa estar ligado.$txt$,
 timestamptz '2026-09-28 00:30:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'clinica-documentos';
