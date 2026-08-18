-- 171_conta_chip.sql
-- Uma empresa pode ter mais de um chip de WhatsApp.
--
-- O caso concreto: a Prime Eventos (conta 34) roda um anúncio de tráfego pago que
-- divide os leads entre DOIS números. O Zaq enxerga um. Nos últimos 5 dias entraram
-- 77 leads pelo chip conectado (9 · 18 · 14 · 14 · 22 por dia, subindo) — e a metade
-- que cai no outro número não vira lead, não entra no funil e não tem quem responda.
-- Já foi paga.
--
-- POR QUE NÃO "CRIAR OUTRA EMPRESA"
--
-- Era a saída óbvia e ela erra por pouco. Duas empresas = duas agendas para um salão
-- só (risco de vender a mesma data duas vezes), dois rodízios entre os mesmos três
-- vendedores, relatório e comissão partidos ao meio — e não existe transferência de
-- lead entre contas, então "migrar depois" é recadastrar na mão.
--
-- O QUE O CÓDIGO JÁ FAZ (e que resolve isso sem inventar nada)
--
-- O services/wa-qr NÃO CONHECE a tabela contas: nunca lê, nunca escreve. Ele mexe em
-- cinco tabelas, todas dele (wa_qr_auth, _sessao_lock, _sessao_estado, _enviadas,
-- _log), todas indexadas por um inteiro que ele chama de conta_id — mas que, para
-- ele, é só a identidade de UM chip. O restaurarSessoes() faz
--
--     select conta_id from wa_qr_auth where arquivo='creds' and creds.me is not null
--
-- e liga uma sessão para cada id que estiver ali. Não pergunta se é empresa, não
-- confere plano, não olha nada fora dele.
--
-- Ou seja: o isolamento entre empresas não vem de nenhuma regra sobre empresas — vem
-- de IDS DIFERENTES, e de mais nada. A credencial da Doce Mell nunca encostou na da
-- Prime porque (35,'creds') e (34,'creds') são linhas distintas de uma chave
-- primária, não porque alguém tomou cuidado.
--
-- Então o segundo chip ganha id próprio e herda esse isolamento inteiro, de graça. O
-- wa-qr não muda uma linha: ele vai achar o id novo no cofre e tratá-lo como trata os
-- outros três.
--
-- POR QUE O CHIP PRECISA SER UMA LINHA EM `contas`
--
-- Não é elegância, é chave estrangeira: wa_qr_auth.conta_id, wa_qr_sessao_lock.conta_id
-- e wa_qr_enviadas.conta_id apontam todos para contas(id). Sem uma linha lá, o chip
-- não tem onde guardar credencial nem trava. `chip_de` é o que impede essa linha de
-- virar uma empresa de verdade: preenchido, ela é um chip DE alguém.
--
-- ATENÇÃO PARA QUEM MEXER DEPOIS
--
-- A partir do momento em que existir uma linha com chip_de preenchido, toda consulta
-- que LISTA ou CONTA empresas precisa de `where chip_de is null` — são ~11 lugares,
-- quase todos painel de admin e estatística (web/admin.py, finance/estatisticas.py,
-- finance/empresa.py, finance/wa_silencio.py). Errar um deles mostra um número a
-- mais; não quebra dado. O sistema já tem esse tipo de linha: `eh_fornecedor` e a
-- conta '__degustacao_visitantes__' também não são empresa cliente. A diferença é que
-- aqui o marcador é explícito em vez de comparação por nome.
--
-- Resolução é de UM salto só, sempre: `empresa = chip_de ?? id`. Não é recursivo de
-- propósito — mesmo que alguém encadeie por engano, não existe laço infinito, o
-- resultado é o pai imediato. O check abaixo cobre o único encadeamento que daria
-- resultado absurdo (ser chip de si mesmo).
--
-- ESTA MIGRAÇÃO NÃO MUDA COMPORTAMENTO NENHUM
--
-- As duas colunas nascem NULAS nas 22 linhas de contas e nas 1.058 de conversas, e
-- nenhum código as lê ainda. Nulo quer dizer exatamente o estado de hoje:
--   contas.chip_de   nulo = esta linha é uma empresa de verdade
--   conversas.chip_id nulo = responder pelo chip da própria empresa
-- Dá para subir isto e parar aqui sem dívida nenhuma.
--
-- Sem índice de propósito: a leitura quente é `select chip_de from contas where
-- id=$1`, que é busca por chave primária. Filtrar conversa POR chip (a etiqueta no
-- inbox) só vai existir no passo 04, e com 1.058 linhas um índice hoje seria enfeite.
--
-- Aditivo e idempotente.

alter table public.contas
  add column if not exists chip_de bigint references public.contas(id);

comment on column public.contas.chip_de is
  'Nulo = empresa de verdade. Preenchido = esta linha não é cliente, é um chip de '
  'WhatsApp da empresa apontada. Existe só para dar id próprio ao chip, porque '
  'wa_qr_auth/_sessao_lock/_enviadas têm FK para contas(id). Toda listagem de '
  'empresas precisa filtrar `where chip_de is null`.';

-- Chip de si mesmo faria `empresa = chip_de ?? id` devolver a própria linha-chip como
-- dona do lead — e aí o lead nasceria numa conta sem funil, sem equipe e sem agenda.
do $$
begin
  if not exists (select 1 from pg_constraint where conname = 'contas_chip_de_nao_si_mesmo') then
    alter table public.contas
      add constraint contas_chip_de_nao_si_mesmo
      check (chip_de is null or chip_de <> id);
  end if;
end $$;

alter table public.conversas
  add column if not exists chip_id bigint references public.contas(id);

comment on column public.conversas.chip_id is
  'Por qual chip esta conversa ENTROU. Nulo = o chip da própria empresa (todo o '
  'histórico até aqui). É o que faz a resposta sair pelo mesmo número que recebeu — '
  'sem isso o lead escreve para um número e é respondido por outro.';

-- rollback:
--   alter table public.conversas drop column if exists chip_id;
--   alter table public.contas drop constraint if exists contas_chip_de_nao_si_mesmo;
--   alter table public.contas drop column if exists chip_de;
