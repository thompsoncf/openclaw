-- 345_novidade_hoje_da_clinica.sql
-- O aviso da tela Hoje da clínica (/painel/hoje, web/painel_hoje.py) e do
-- "Voltar a chamar depois do preço" (finance/voltar_a_chamar.py, migração 344),
-- seguindo a seção 5 do CLAUDE.md.
--
-- O PORTÃO É NOVO E É `clinica`, o terceiro de UM nicho só (depois de `seguros` e
-- `suplementos`). A tela só abre pro perfil clínica; 'servico' ou 'recorrente'
-- avisariam advocacia e consultoria de uma tela que elas não têm.
--
-- QUEM RECEBE, conferido na produção em 25/09/2026 (só leitura, nicho clinica):
--   39 Espaço Pelle Clínica Dermatologica Ltda (a única conta do nicho)
--
-- PRA QUEM: dono, gestor e vendedor. A recepção entra no Zaq com papel de
-- vendedor, e é ela quem usa a tela no dia a dia.
--
-- Aditiva e idempotente (on conflict (chave) do nothing).

-- ────────────────────────────────────────────── 1. o portão entra no check
alter table public.novidades drop constraint if exists novidades_publico_check;
alter table public.novidades add constraint novidades_publico_check
  check (publico in ('todos','produto','servico','eventos','recorrente',
                     'canal_proprio','seguros','suplementos','empresa','clinica'));

-- ────────────────────────────────────────────── 2. o aviso
insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('clinica-hoje', 'novidade', 'clinica', '{dono,gestor,vendedor}',
 'Hoje: quem está esperando resposta e quem recebeu o preço e sumiu',
 'Clínicas ganharam a tela Hoje: quem escreveu e está esperando, quem recebeu o preço da consulta e não marcou, com a mensagem de retorno pronta para mandar.',
 '/painel/hoje',
 $txt$A clínica ganhou uma tela nova no menu: Hoje. Ela junta num lugar só o que a recepção precisa resolver no dia.

QUEM ESTÁ ESPERANDO RESPOSTA

Os pacientes que escreveram e ainda não receberam resposta, do mais antigo para o mais novo. Quem escreveu com a clínica fechada aparece marcado.

VOLTAR A CHAMAR DEPOIS DO PREÇO

Quando a recepção passa o valor da consulta e a conversa para sem o paciente marcar, o Zaq prepara uma mensagem de retorno: 3 horas depois, no dia seguinte, em 3 dias e em 7 dias. A mensagem só fala da consulta e do horário, nunca de preço ou de saúde.

Cada mensagem tem quatro botões: Mandar, Já marcou, Não chamar e Não é paciente. Quem marca ou pede para sair não recebe mais nada. Se o paciente fez uma pergunta e ninguém respondeu, a mensagem espera: ele aparece em Esperando resposta.

Quem recebeu o preço antes de a tela ser ligada aparece em Repescagem, com uma mensagem só, e ela sempre espera alguém apertar Mandar.

COMEÇA DESLIGADO

O dono ou o gestor escolhe o modo no fim da tela:
- Desligado: nada é preparado.
- Sugere: a mensagem fica pronta e a recepção decide se manda.
- Ligado: o Zaq manda sozinho, pelo mesmo número da conversa, só no horário de atendimento, no máximo 1 por paciente por dia.

Os textos das mensagens podem ser trocados ali mesmo.

O PLACAR DO MÊS

Quantos receberam o preço, quantos marcaram e quantos nunca foram chamados de novo.$txt$,
 timestamptz '2026-09-25 21:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'clinica-hoje';
--   alter table public.novidades drop constraint if exists novidades_publico_check;
--   alter table public.novidades add constraint novidades_publico_check
--     check (publico in ('todos','produto','servico','eventos','recorrente',
--                        'canal_proprio','seguros','suplementos','empresa'));
