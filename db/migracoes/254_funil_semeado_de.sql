-- 254_funil_semeado_de.sql
-- De onde veio o rótulo desta linha: da SEMENTE de um ramo, ou do dono.
--
-- O QUE ISTO CONSERTA, medido em 14/09/2026 (docs/mockups/funil_semente_do_ramo.html):
-- de 8 contas com funil, UMA tem as colunas do próprio ramo — e é a Prime, que o
-- dono reconstruiu na mão ao longo de meses. A Doce Mell, citada pelo nome no
-- docstring do `finance/funil_modelo.py` como o caso que aquele módulo veio
-- resolver, seguia nas seis colunas genéricas três dias depois. A ferramenta de
-- adoção existe e funciona; ninguém adota porque nada avisa.
--
-- O DEFEITO QUE ESTA COLUNA MATA. Pra propor "chamar «Reunião marcada» de «Cotação
-- enviada»", o `funil_modelo.plano` precisa saber se aquele nome foi o DONO que deu
-- (e aí a proposta vem desmarcada, com respeito) ou se foi uma semente (e aí vem
-- marcada). Ele adivinhava isso comparando o rótulo com `ETAPAS_GENERICAS` — a
-- lista de antes de 11/09. Desde então as contas nascem semeadas PELO PERFIL, então
-- todo rótulo vindo de outra semente parecia apelido do dono: a Liberal (conta 37)
-- recebeu "Reunião marcada" do sistema e a tela ia lhe dizer "você já renomeou esta
-- etapa". O sistema pedindo desculpa por uma coisa que ele mesmo fez.
--
-- Agora não se adivinha. `semeado_de` guarda a chave do perfil que semeou
-- ('eventos', 'seguros', 'recorrente', 'produto', 'generico') e o VAZIO significa
-- "foi o dono que escreveu". NULL é o estado das linhas que já existiam: o carimbo
-- delas roda no código, na primeira leitura de cada conta (finance.funil_modelo.
-- carimbar), porque só o Python conhece os modelos e duplicá-los aqui em SQL seria
-- criar uma segunda verdade pra sair de sincronia no primeiro ajuste.
--
-- NÃO MUDA RÓTULO NENHUM. Esta migração só acrescenta duas colunas nulas. Nenhum
-- funil de nenhuma conta muda de forma por causa dela — a adoção continua sendo um
-- ato do dono, marcando a caixa (CLAUDE.md §0).
--
-- Aditiva e idempotente.

alter table public.funil_etapas
    add column if not exists semeado_de text;

alter table public.funil_motivos_perda
    add column if not exists semeado_de text;

-- rollback:
--   alter table public.funil_etapas drop column if exists semeado_de;
--   alter table public.funil_motivos_perda drop column if exists semeado_de;
