-- 383_novidade_numeros_da_clinica.sql
-- O aviso dos Números da clínica (finance/clinica_numeros.py, /painel/clinica/numeros),
-- seguindo a seção 5 do CLAUDE.md.
--
-- PÚBLICO `clinica`. PRA QUEM: dono e gestor (a tela é do dono: tem receita).
-- QUEM RECEBE, conferido na produção em 26/09/2026 (só leitura, nicho clinica):
--   39 Espaço Pelle Clínica Dermatologica Ltda (a única conta do nicho)
--
-- Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('clinica-numeros', 'novidade', 'clinica', '{dono,gestor}',
 'Números da clínica: a agenda, os planos e os pacotes lidos como negócio',
 'Ocupação de cada profissional, horários que passaram vazios, quantas consultas viram plano e quantos planos fecham, ticket médio, sessões devidas, faltas, retornos e vagas — mês a mês.',
 '/painel/clinica/numeros',
 $txt$O Raio-X da clínica ganhou uma tela só dela, com os números que o dono não tinha.

O QUE ELA MOSTRA, MÊS A MÊS

- Ocupação da agenda: quanto da grade de cada profissional foi vendido.
- Horários que passaram vazios, e quanto isso seria se fossem consultas.
- Da consulta ao tratamento: consultas finalizadas, planos enviados e planos fechados, com as taxas. É onde o dinheiro trava.
- Ticket médio por atendimento.
- Sessões devidas: o que foi vendido em pacote e ainda não foi atendido. O dinheiro entrou, mas o atendimento é devido.
- Faltas, cancelamentos e quantos confirmaram na véspera.
- Retornos pedidos, marcados e perdidos; vagas liberadas e preenchidas.
- De onde vieram os agendamentos: recepção, agente no WhatsApp ou vaga liberada.

DE ONDE VÊM OS NÚMEROS

Da agenda, dos planos de tratamento, dos pacotes e das vagas. Receita e ticket são estimados pelo preço de tabela e pelo valor do plano aceito; o caixa de verdade continua no Financeiro.

Fica em Raio-X › Números da clínica, só para o dono e o gestor.$txt$,
 timestamptz '2026-09-27 00:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'clinica-numeros';
