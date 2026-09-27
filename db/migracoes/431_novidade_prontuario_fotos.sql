-- 431_novidade_prontuario_fotos.sql
-- O aviso da fase 3 do prontuário (fotos e anexos cifrados, migração 430), seguindo a
-- seção 5 do CLAUDE.md.
--
-- PÚBLICO `clinica`. PRA QUEM: dono e gestor.
-- QUEM RECEBE, conferido na produção em 27/09/2026 (só leitura, nicho clinica):
--   39 Espaço Pelle Clínica Dermatologica Ltda (a única conta do nicho)
--
-- Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('clinica-prontuario-fotos', 'novidade', 'clinica', '{dono,gestor}',
 'Fotos clínicas e anexos no prontuário, cifrados',
 'O profissional tira a foto pelo celular direto no prontuário (sem passar pela galeria), guarda o exame em PDF, e compara o antes e o depois. Tudo cifrado; cada abertura fica registrada.',
 '/painel/clinica/pacientes',
 $txt$O prontuário ganhou fotos e anexos.

- No celular, "Guardar" abre a câmera direto: a foto não vai para a galeria do aparelho.
- O Zaq tira a localização e os dados do aparelho, cifra a foto e guarda numa área privada. Nem quem tem acesso ao armazenamento vê a imagem.
- Anexos: o exame em PDF ou a foto do papel, ligados ao atendimento.
- Comparar: marque duas fotos e veja o antes e o depois lado a lado.
- Foto só com o termo de imagem aceito pelo paciente (no link da ficha) ou com a autorização em papel. Quem marcou "não autorizo" não tem foto; anexo continua.
- Só os profissionais liberados abrem, e cada abertura fica no registro de acesso. Nada se apaga.$txt$,
 timestamptz '2026-09-28 00:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'clinica-prontuario-fotos';
