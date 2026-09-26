-- 382_novidade_pacotes_e_retornos.sql
-- O aviso dos pacotes com saldo e do retorno programado da clínica
-- (finance/clinica_pacotes.py, 381), seguindo a seção 5 do CLAUDE.md.
--
-- PÚBLICO `clinica`. PRA QUEM: dono, gestor e vendedor (a recepção marca a próxima).
-- QUEM RECEBE, conferido na produção em 26/09/2026 (só leitura, nicho clinica):
--   39 Espaço Pelle Clínica Dermatologica Ltda (a única conta do nicho)
--
-- Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('clinica-pacotes-e-retornos', 'novidade', 'clinica', '{dono,gestor,vendedor}',
 'Pacote com saldo e retorno programado: o paciente volta sozinho',
 'As sessões do plano aceito viram saldo; cada atendimento finalizado baixa uma e a tela já oferece marcar a próxima. O retorno que o médico pede vira prazo, e o Zaq chama o paciente antes.',
 '/painel/clinica/pacotes',
 $txt$O paciente que comprou o tratamento agora volta para todas as sessões, e o retorno não depende de alguém lembrar.

O SALDO DE SESSÕES

Quando o plano de tratamento é aceito, cada procedimento vira um pacote: as sessões compradas, o intervalo entre elas (o prazo de volta do atendimento, ou 21 dias) e a validade (12 meses).

Cada atendimento finalizado daquele procedimento baixa uma sessão, com a data. Na hora, a tela mostra "Sessão 2 de 4 baixada" e o botão "Marcar a 3ª sessão", com o paciente e a data sugerida já preenchidos. Numa sessão de pacote, o Finalizar não pergunta "o médico propôs tratamento?".

O RETORNO

Ao finalizar a consulta, diga em quantos dias o médico pediu retorno (vem preenchido com o prazo de volta do atendimento). Sete dias antes, o paciente recebe "está chegando a hora do seu retorno, qual o melhor dia?". Marcou com o profissional, sai da fila.

OS LEMBRETES

O Zaq avisa o paciente quando a próxima sessão já pode ser marcada, quando o retorno está chegando e quando o pacote vence em 60 dias com saldo. Só no horário de atendimento, no máximo 1 mensagem automática por dia e sem dizer o procedimento.

A TELA

Em Agenda › Pacotes e retornos: quem precisa marcar, os retornos chegando, os pacotes com a barra de sessões, e o número que ninguém mostra: sessões vendidas × usadas × devidas. Pacote com desistência ou reembolso se encerra com o motivo, e o saldo fica registrado.

A regra "sessão só com parcela em dia" existe, mas nasce desligada: quem liga é o dono, no fim da tela.$txt$,
 timestamptz '2026-09-26 22:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'clinica-pacotes-e-retornos';
