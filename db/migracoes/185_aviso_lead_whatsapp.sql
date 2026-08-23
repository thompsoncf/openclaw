-- 185_aviso_lead_whatsapp.sql
-- Aviso de lead novo no WhatsApp do vendedor, pelo chip que o dono escolher.
--
-- O envio já existia e já funcionava: `whatsapp_out.enviar` aceita `chip_id` e, no
-- provedor 'qr', manda por ele (finance/whatsapp_out.py). O que faltava era três
-- coisas, e todas são CONFIGURAÇÃO — por isso a migração é só de colunas:
--
--   1. POR QUAL CHIP. `_avisar_whatsapp` nunca passava `chip_id`, então o aviso
--      saía sempre pelo chip principal — o mesmo que fala com cliente o dia
--      inteiro. Numa empresa com chip de campanha, aviso interno não precisa
--      disputar espaço nem reputação com o número que atende.
--   2. QUAL TEXTO. Era fixo no código: título e corpo do e-mail, colados. Quem
--      escreve pro próprio time sabe melhor que a gente como falar com ele.
--   3. LIGAR SEPARADO. `avisar` governa e-mail, push e WhatsApp de uma vez só.
--      Quem quer push e não quer tocar o celular do vendedor não tinha como.
--
-- `aviso_zap` nasce FALSE: mandar mensagem no WhatsApp de alguém não é coisa que
-- se liga sozinha num deploy. Quem quiser, liga na tela.
alter table public.distribuicao
  add column if not exists aviso_zap         boolean not null default false,
  add column if not exists aviso_zap_chip_id bigint,
  add column if not exists aviso_zap_texto   text;

-- ...com UMA exceção, e ela é o oposto de ligar sozinho: quem JÁ configurou um
-- template de aviso já disse que quer o WhatsApp. Antes desta migração, template
-- preenchido era o próprio interruptor — o envio só dependia de `avisar`. Agora que
-- existe um interruptor de verdade, deixar essas contas em FALSE seria DESLIGAR em
-- silêncio um aviso que estava funcionando, num deploy que ninguém pediu.
--
-- Hoje não pega ninguém (zero contas com template em produção, conferido antes de
-- escrever isto). Está aqui pelo princípio: migração não tira recurso de quem tem.
update public.distribuicao
   set aviso_zap = true
 where coalesce(trim(aviso_template_sid), '') <> ''
   and aviso_zap is not true;

-- Sem FK pro chip de propósito. O chip é uma linha de `contas` (ver `chip_de`), e
-- uma FK com o default `no action` impediria apagar a conta do chip enquanto esta
-- config existisse. O código já valida a posse do chip a cada envio — mesma volta
-- que `_chip_da_conta` faz na tela —, e chip que sumiu cai no fallback de e-mail
-- e push, que é o comportamento certo de qualquer jeito.
comment on column public.distribuicao.aviso_zap_chip_id is
  'contas.id do chip que manda o aviso; nulo = chip principal da empresa';

-- O FREIO, e por que ele fica em `membros`.
--
-- Uma reimportação de histórico faz dezenas de leads nascerem em poucos minutos —
-- em 22/08 foram 21 numa tarde. Por e-mail isso já era ruim; por WhatsApp é pior,
-- porque toca o celular. O intervalo mínimo é POR VENDEDOR (não por conta e não
-- por lead): a fila do rodízio reparte entre vários, e um teto por conta calaria
-- o aviso de quem ainda não tinha recebido nada.
--
-- Mesmo desenho de `conversas.push_avisado_em`, que já resolve a rajada do cliente
-- em finance/cockpit.py: um carimbo, decidido no próprio UPDATE com RETURNING, pra
-- dois webhooks simultâneos não passarem os dois. Ler-e-depois-gravar deixaria.
alter table public.membros
  add column if not exists aviso_zap_em timestamptz;

comment on column public.membros.aviso_zap_em is
  'último aviso de lead por WhatsApp; o intervalo mínimo é decidido em UPDATE ... RETURNING';
