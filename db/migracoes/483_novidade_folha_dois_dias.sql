-- 483_novidade_folha_dois_dias.sql
-- O aviso da folha em dois dias (adiantamento + saldo), seguindo a seção 5 do
-- CLAUDE.md. Pedido do dono em 02/10/2026: "colocar por exemplo 5 e 20 como
-- saldo salário e adiantamento salário". Decisões dele no mesmo dia: o
-- adiantamento é escolhido por funcionário (percentual ou valor fixo) e o saldo
-- cai no 5º dia útil do mês seguinte. Precisa da 482.
--
-- O QUE MUDOU NA TELA. Em Empresa › Equipe e folha, cada pessoa ganhou "📅 Datas
-- de pagamento": o dia do adiantamento e quanto (percentual do salário ou valor
-- fixo), o saldo no dia de sempre ou no 5º dia útil do mês seguinte, e a opção
-- de gerar as duas contas a pagar sozinho. A linha da pessoa mostra as duas
-- datas do mês com valor e situação; a conta a pagar gerada leva o selo
-- "📅 folha".
--
-- O PORTÃO: `empresa` (mesmo de 338/448/449/450) — é quem tem o módulo PJ e vê a
-- folha. PRA QUEM: dono e gestor. O vendedor não tem a aba Empresa.
--
-- QUEM RECEBE, conferido na produção em 02/10/2026 (só leitura, módulo PJ ativo
-- — mesma consulta da 450, mesma lista):
--   3 Thompson · 7 João Pedro · 9 Zé do Arroz · 16 Danilo · 21 Maylson ·
--   23 Rawilson · 26 Katheley · 30 Paulo · 31 Juliana · 33 Pablo · 34 Manoel
--   (Prime) · 35 Louana · 37 Liberal · 39 Espaço Pelle Clínica Dermatológica ·
--   40 M.R. Rocha Assessoria de Imprensa
--
-- Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values

('folha-dois-dias', 'novidade', 'empresa', '{dono,gestor}',
 'Folha em dois dias: adiantamento e saldo',
 'Escolha, por funcionário, o dia e o valor do adiantamento e pague o saldo no 5º dia útil do mês seguinte — as duas contas a pagar nascem sozinhas.',
 '/painel/empresa#folha',
 $txt$Agora dá pra pagar o salário em duas datas — por exemplo, adiantamento no dia 20 e o saldo no 5º dia útil do mês seguinte.

ONDE

Em Empresa › Equipe e folha, abra a pessoa e toque em "📅 Datas de pagamento":

- Adiantamento: o dia, e quanto — um percentual do salário (ex.: 40%) ou um valor fixo. Cada funcionário tem o seu.
- Saldo: no dia de sempre do mês seguinte, ou no 5º dia útil (conta o sábado e pula domingo e feriado nacional, como manda o Ministério do Trabalho).
- Contas a pagar: marque "gerar sozinho" e o adiantamento e o saldo aparecem em Contas a pagar, aguardando a sua liberação, com o selo "📅 folha".

O SALDO ACOMPANHA A FOLHA

Lançou um extra, um desconto ou deu aumento? O saldo se ajusta. Deu baixa no adiantamento? Ele entra na folha e no holerite sozinho — sem lançar o caixa duas vezes. E se pagar a folha inteira pelo "pagar ✓", as contas que sobraram daquele mês são canceladas.

Começa no mês de hoje: mês que já passou não ganha conta.$txt$,
 timestamptz '2026-10-02 16:00:00+00')

on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'folha-dois-dias';
