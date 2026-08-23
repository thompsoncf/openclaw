"""O JS que o NAVEGADOR recebe tem que compilar.

POR QUE ESTE TESTE EXISTE. Em 17/08 o bloco <script> da aba Canais ficou morto por
erro de sintaxe e ninguém percebeu por horas: o botão do QR ficava preso em
"Verificando…", `qrShow`/`qrPoll`/`qrIniciar`/`qrSair` nunca eram definidos, e a
tela mostrava o estado estático do HTML.

A causa foi de ESCAPE, e é uma armadilha estrutural deste arquivo: os templates são
strings Python COMUNS (`_COMUNICACAO_TPL = \"\"\"...\"\"\"`, sem `r`), então um `\\n`
escrito dentro de uma string JavaScript vira newline LITERAL no HTML servido — e
uma string de aspas simples cortada no meio é SyntaxError, que derruba o bloco
inteiro, não só aquela linha.

E o pior: nada disso aparece lendo o .py. `node --check` no trecho copiado do
código-fonte PASSA, porque ali `\\n` ainda são dois caracteres. O erro só existe
depois do render. Foi exatamente assim que passou pelo CI e por três revisões.

Este teste fecha esse buraco pelo único caminho que funciona: renderiza o template
de verdade e roda `node --check` em cada <script> do resultado.

Pula sozinho se não houver `node` no ambiente — o valor está em rodar no CI, não em
travar quem não tem Node instalado.
"""
from __future__ import annotations

import inspect
import json
import re
import shutil
import subprocess

import jinja2
import pytest

from finance import vendas as v
from web import painel_prospeccao as pp
from web import painel_servicos as _ps  # registra "servicos" no mesmo loader

# páginas que carregam JS próprio, pelo nome com que são registradas no loader, e
# o contexto MÍNIMO que abre os ramos de {% if %} onde o JS mora. Crescer esta
# lista é de graça; deixar uma de fora é o que custou caro.
#
# Os contextos não são decoração: com `_Mudo` (falso pra tudo) o {% if %} esconde
# justamente o bloco que interessa, e o teste passaria sem ter olhado nada.
PAGINAS = {
    # 'gerencia' e provedor 'qr': é o ramo que traz o bloco do QR, que foi o que
    # quebrou em 17/08.
    "prospeccao_comunicacao": dict(
        gerencia=True,
        canais={"whatsapp": True, "wa_provedor": "qr",
                "numeros": {"whatsapp": "+5586999999999"}, "tokens_set": {}}),
    # o kanban: um card com conv_whatsapp/conv_email preenchidos é o que abre o
    # ramo do selo virando BOTÃO (kbAbrirChat) — sem isso o {% if %} escondia
    # justamente o JS que o teste existe pra proteger.
    "prospeccao": dict(
        status=[("novo", "Novo"), ("contatado", "Contatado")],
        colunas={
            "novo": [{"id": 1, "empresa": "Padaria Bom Pão", "segmento": "Varejo",
                      "cidade": "Teresina", "uf": "PI", "temperatura": "quente",
                      "valor": 420000, "proximo": None, "vendedor": None, "vendedor_id": None,
                      "tem_whatsapp": True, "tem_email": True, "tem_instagram": False,
                      "enriquecido": True,
                      "conv_whatsapp": 501, "conv_email": None, "conv_instagram": None}],
            "contatado": []},
        temp_cor={"quente": "#e0574f"}, temp_pill={"quente": "Quente"},
        gerencia=False, pode_atribuir=False, vendedores=[], filtro_vend="",
        total_valor=420000, total_alvos=1, tem_places=False, tem_maps_js=False,
        maps_js_key=""),
    # 'pode_contrato': o card do contrato, que só existe pro dono de conta de
    # eventos — e é onde vive o JS do modelo, da prévia e do aviso de ajustes.
    # `icones_paleta` entra porque a página o serializa com |tojson, e `_Mudo`
    # não é serializável — é o preço de renderizar de verdade em vez de fingir.
    "servicos": dict(servico_avulso=True, pode_contrato=True, icones_paleta=[]),
}


class _Mudo(jinja2.Undefined):
    """Undefined que aceita qualquer acesso e imprime nada.

    O alvo aqui é a SINTAXE do JS, não a semântica dos dados: montar um contexto
    fiel de cada tela seria manutenção sem retorno, e um dado faltando não muda se
    o script compila."""

    def __getattr__(self, n):
        if n.startswith("__"):
            raise AttributeError(n)
        return _Mudo()

    def __getitem__(self, k):
        return _Mudo()

    def __call__(self, *a, **k):
        return _Mudo()

    def __str__(self):
        return ""

    def __html__(self):
        return ""

    def __iter__(self):
        return iter(())

    def __bool__(self):
        return False

    def __len__(self):
        return 0


def _render(nome: str, **over) -> str:
    env = jinja2.Environment(
        loader=jinja2.DictLoader(dict(pp._env.loader.mapping)),
        undefined=_Mudo, autoescape=True)
    for k, v in pp._env.filters.items():
        env.filters.setdefault(k, v)
    for k, v in pp._env.globals.items():
        env.globals.setdefault(k, v)
    return env.get_template(nome).render(**{**PAGINAS[nome], **over})


def _scripts(html: str) -> list[str]:
    return [b for b in re.findall(r"<script[^>]*>(.*?)</script>", html, re.S) if b.strip()]


@pytest.mark.parametrize("pagina", sorted(PAGINAS))
def test_todo_script_da_pagina_compila(pagina, tmp_path):
    if not shutil.which("node"):
        pytest.skip("sem node no ambiente")
    html = _render(pagina)
    blocos = _scripts(html)
    assert blocos, f"{pagina}: nenhum <script> encontrado — o render mudou de forma?"
    erros = []
    for i, bloco in enumerate(blocos):
        alvo = tmp_path / f"{pagina}_{i}.js"
        alvo.write_text(bloco, encoding="utf-8")
        r = subprocess.run(["node", "--check", str(alvo)],
                           capture_output=True, text=True)
        if r.returncode != 0:
            erros.append(f"bloco {i}:\n{r.stderr.strip()[:600]}")
    assert not erros, (
        f"{pagina}: {len(erros)} bloco(s) de <script> não compilam no navegador.\n"
        + "\n\n".join(erros))


def test_conectado_nao_promete_reconectar():
    """O botão dizia "Reconectar" com a sessão de pé e não reconectava nada: o
    /iniciar devolve a sessão viva sem tocar nela e o painel nunca manda
    {forcar:true}. Prometer ação que não acontece é pior que não ter botão.

    E ele CONTINUA clicável: desabilitar tiraria a única saída de quem está
    "conectado" e mudo — e este arquivo já pagou caro por botão travado."""
    js = "\n".join(_scripts(_render("prospeccao_comunicacao")))
    assert "'✓ Conectado'" in js
    assert "'Reconectar'" not in js, "o texto que mentia voltou"
    assert "btn.disabled=false;}" in js, "o botão não pode nascer travado quando conectado"


def test_o_bloco_do_contrato_esta_mesmo_na_pagina():
    """Mesma guarda do QR, pro card do contrato: o JS dele só existe no ramo
    `pode_contrato`, e um {% if %} mudado deixaria o teste de sintaxe verde sem
    ter olhado nada."""
    js = "\n".join(_scripts(_render("servicos")))
    for fn in ("pintarAjustes", "resumir", "previa", "desenhar"):
        assert fn in js, f"{fn} não está no JS servido"


def test_o_funil_sabe_desenhar_o_estado_da_data():
    """A linha do funil ganhou os quatro estados da data e o botão que conserta os
    dois ruins. Sem estas funções no JS servido, a resposta da rota chega e ninguém
    desenha — que é exatamente como uma data fora da agenda passou despercebida."""
    html = _render("servicos")
    js = "\n".join(_scripts(html))
    for fn in ("marcarData", "pintarSemHora"):
        assert fn in js, f"{fn} não está no JS servido"
    assert "ev-sem-hora" in html, "o aviso da hora de início sumiu do formulário"


def test_todo_tom_de_selo_tem_cor_na_folha_de_estilo():
    """O selo é pintado por classe (`oc-badge pend <tom>`), e o tom vem do Python.

    Um tom novo no `finance/vendas.py` sem a regra correspondente aqui não quebra
    nada: renderiza uma caixinha transparente, sem borda e com a cor do texto ao
    redor — o aviso continua na tela e para de ser visto. É o modo de falhar mais
    silencioso que esta linha tem, então é o que este teste guarda."""
    html = _render("servicos")
    tons = set()
    for caso in (
        dict(status="aprovada", data_estado={"estado": v.DATA_FORA, "dica": ""}),
        dict(status="aprovada",
             data_estado={"estado": v.DATA_SEGURADA, "texto": "até 22/08", "dica": ""}),
        dict(status="aprovada", plano_difere=1),
        dict(status="aprovada", contrato_numero=5, contrato_assinado=False),
        dict(status="rascunho"),
        dict(status="aprovada", pagamentos={"pagas": 1, "total": 2, "sem_comprovante": 1}),
    ):
        tons |= {sl["tom"] for sl in v.linha_do_funil(**caso)["selos"]}
    assert tons, "nenhum selo foi produzido — o teste parou de testar"
    for tom in sorted(tons):
        assert f".oc-badge.pend.{tom}" in html, f"o tom '{tom}' não tem cor no CSS"


def test_a_linha_do_funil_desenha_pendencia_acao_e_menu():
    """As três peças da linha nova. Se qualquer uma sumir do JS servido, a rota
    continua respondendo `painel` e a tela continua não mostrando nada."""
    js = "\n".join(_scripts(_render("servicos")))
    for fn in ("abrirMenuLinha", "fecharMenuLinha", "acaoDaLinha"):
        assert fn in js, f"{fn} não está no JS servido"
    assert "oc-badge pend" in js, "os selos de pendência sumiram da linha"
    assert "oc-nada" in js, "o ✓ de 'nada pendente' sumiu"
    assert "oc-menu-btn" in js, "o botão Ações sumiu"


def test_o_js_da_agenda_compila(tmp_path):
    """A Agenda serve o JS como ARQUIVO (web/estaticos.py), então dá pra checar a
    fonte direto — sem render, sem contexto. Vale o mesmo que os outros: erro de
    sintaxe mata o bloco inteiro, e a tela fica com cara de travada."""
    if not shutil.which("node"):
        pytest.skip("sem node no ambiente")
    from web import painel_agenda as pa
    alvo = tmp_path / "agenda.js"
    alvo.write_text(pa._JS_CRU, encoding="utf-8")
    r = subprocess.run(["node", "--check", str(alvo)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr.strip()[:600]


def test_o_bloco_do_qr_esta_mesmo_na_pagina():
    """Guarda do teste acima: se o bloco do QR sumir do render, o teste de sintaxe
    fica verde sem ter olhado o código que já quebrou uma vez."""
    js = "\n".join(_scripts(_render("prospeccao_comunicacao")))
    for fn in ("qrShow", "qrPoll", "qrIniciar", "qrSair", "qrApagar", "qrEsperando"):
        assert fn in js, f"{fn} não está no JS servido"


@pytest.mark.parametrize("pagina", sorted(PAGINAS))
def test_nenhuma_string_js_quebrada_por_newline_literal(pagina):
    """A armadilha específica, dita por extenso: string JS de aspas simples com
    newline literal dentro. `node --check` já pega, mas este teste NOMEIA a causa —
    quem quebrar de novo lê o motivo em vez de decifrar um SyntaxError."""
    for bloco in _scripts(_render(pagina)):
        for n, linha in enumerate(bloco.split("\n"), 1):
            # aspas simples ímpares na linha = string aberta que atravessa o \n
            if linha.count("'") % 2:
                pytest.fail(
                    f"linha {n} deixa uma string JS aberta: {linha.strip()[:90]!r}\n"
                    "Se era pra ser uma quebra de linha no texto, escreva \\\\n — "
                    "o template é string Python comum e um \\n vira newline literal.")


def test_selo_de_conversa_vira_botao_so_com_conversa_de_verdade():
    """O card usava ter um só ramo de badge (span apagado, sem clique). Agora tem
    dois: BOTÃO quando existe conversa de verdade (`conv_whatsapp`/etc.), span
    inerte quando só tem o telefone/e-mail cadastrado. Perder esse if seria
    voltar a abrir o chat de quem nunca conversou — ou pior, nunca abrir nada."""
    html = _render("prospeccao")
    js = "\n".join(_scripts(html))
    assert "kbAbrirChat" in js, "a função que abre o chat sumiu do JS servido"
    assert "kbAbrirChat(event,501,'conversas',this)" in html, (
        "o card com conv_whatsapp preenchido não virou botão")


def test_o_clique_no_selo_nao_propaga_pro_card():
    """O card inteiro tem onclick pra abrir a FICHA. Sem stopPropagation, clicar
    no selo abriria os dois drawers ao mesmo tempo (ficha por baixo do chat)."""
    fonte = inspect.getsource(pp).split("function kbAbrirChat")[1][:400]
    assert "ev.stopPropagation()" in fonte


def test_o_balao_do_chat_e_so_mensagens_nao_a_tela_inteira():
    """20/08: o primeiro formato abria o hub de Comunicação inteiro (lista de
    conversas + ficha do lead + abas) dentro do drawer da ficha, via iframe — só
    pra ler o que a pessoa escreveu. Pedido do dono: só o balão, sem essa janela
    toda. `kbAbrirChat` não pode voltar a tocar no #kb-drawer/#kb-dframe da
    ficha — ele cria o próprio elemento (`.chatpop`), busca o thread direto
    (fetch), e desenha só as bolhas."""
    js = "\n".join(_scripts(_render("prospeccao")))
    for fn in ("kbFecharChat", "kbMsgsHtml", "kbResponderChat", "cxEscK"):
        assert fn in js, f"{fn} não está no JS servido"
    fonte_chat = inspect.getsource(pp).split("function kbAbrirChat")[1]
    fonte_chat = fonte_chat[:fonte_chat.index("function kbResponderChat")]
    assert "kb-dframe" not in fonte_chat, "voltou a carregar o hub inteiro num iframe"
    assert "kb-drawer" not in fonte_chat, "voltou a usar o drawer da ficha pro chat"
    assert "/painel/prospeccao/comunicacao/thread/" in fonte_chat, (
        "o balão não busca a conversa pelo endpoint que já existe")


def test_o_balao_so_deixa_um_aberto_por_vez():
    """Abrir o chat de um segundo lead tem que fechar o balão do primeiro —
    dois balões abertos ao mesmo tempo se sobrepõem e nenhum fica legível."""
    fonte = inspect.getsource(pp).split("function kbAbrirChat")[1][:200]
    assert "kbFecharChat()" in fonte, "kbAbrirChat não fecha o balão anterior antes de abrir o novo"


def test_o_balao_fecha_clicando_fora_e_com_esc():
    fonte = inspect.getsource(pp)
    assert "_chatPopFora" in fonte and "e.target" in fonte
    assert "_chatPopEsc" in fonte and "'Escape'" in fonte


def test_responder_pelo_balao_usa_a_rota_que_ja_existe():
    """A caixa de responder do balão não é feature nova no backend — é a mesma
    rota que o hub de Comunicação já usa pra enviar."""
    fonte = inspect.getsource(pp).split("function kbResponderChat")[1][:600]
    assert "/painel/prospeccao/comunicacao/responder" in fonte
    assert "conversa_id" in fonte and "texto" in fonte


def test_o_balao_sempre_cabe_na_tela_mesmo_perto_da_borda():
    """21/08: `top: botão+6px` sem olhar o espaço disponível deixava o balão
    nascer cortado quando o card ficava perto do fim da janela — e por ser
    `position:fixed`, rolar a página não revelava o resto (fixed não se move
    com a rolagem). `kbAbrirChat` tem que medir o espaço acima/abaixo do botão
    e escolher o lado com folga, prendendo a altura a esse espaço."""
    fonte_chat = inspect.getsource(pp).split("function kbAbrirChat")[1]
    fonte_chat = fonte_chat[:fonte_chat.index("function kbResponderChat")]
    assert "innerHeight" in fonte_chat, (
        "não mede mais o espaço disponível na janela — pode voltar a nascer fora da tela")
    assert "pop.style.bottom" in fonte_chat, (
        "perdeu o lado 'abre pra cima' quando não cabe embaixo do botão")
    assert "maxHeight" in fonte_chat, (
        "sem altura presa ao espaço disponível o balão pode ultrapassar a tela de novo")


def test_rolar_a_pagina_fecha_o_balao_mas_o_auto_scroll_interno_nao():
    """21/08: balão `fixed` não acompanha a rolagem da página, então deixá-lo
    aberto longe do card que o abriu ('desvinculado do lead') era pior que só
    fechar — fecha e reabre certinho no próximo clique. MAS a lista de
    mensagens rola sozinha pra mostrar a última assim que abre
    (`box.scrollTop=box.scrollHeight`), e isso dispara um scroll capturado no
    window também — sem o guard de `contains`, o balão se fechava sozinho no
    instante em que as mensagens chegavam, ANTES do usuário conseguir ler
    qualquer coisa (era exatamente o sintoma relatado: abre e não dá pra ver
    as mensagens)."""
    fonte = inspect.getsource(pp)
    assert "window.addEventListener('scroll',_chatPopRolou,true)" in fonte, (
        "balão não fecha mais quando a página rola de verdade")
    fonte_guard = fonte.split("function _chatPopRolou")[1][:200]
    assert "_chatPop.contains(e.target)" in fonte_guard, (
        "sem checar se o scroll veio de DENTRO do próprio balão, o auto-scroll "
        "interno das mensagens (pra mostrar a última) fecha o balão sozinho "
        "assim que elas chegam")


def test_clicar_no_card_abre_o_resumo_nao_a_ficha_inteira_numa_gaveta():
    """21/08: clicar no card abria uma gaveta de 1080px com um iframe carregando
    a página INTEIRA da ficha (edição de cadastro, IA, decisor, orçamento) — só
    pra decidir a próxima ação. Virou um balão resumido, mesma engenharia do
    balão de chat: sem iframe, sem gaveta de tela cheia."""
    html = _render("prospeccao")
    js = "\n".join(_scripts(html))
    assert "kbAbrirLead(event,1,this)" in html, (
        "o clique no card não está mais ligado a kbAbrirLead")
    assert "kbAbrir(" not in js.replace("kbAbrirLead(", "").replace("kbAbrirChat(", ""), (
        "a função antiga (iframe/gaveta) voltou a existir")
    assert "kb-dframe" not in js and "kb-drawer" not in js and "kb-dtit" not in js, (
        "a gaveta de 1080px com iframe da ficha inteira voltou"
    )
    for fn in ("kbFecharLead", "kbLeadHtml", "kbLeadStatus"):
        assert fn in js, f"{fn} não está no JS servido"


def test_o_balao_do_lead_segue_as_etapas_reais_do_funil():
    """Pedido explícito: a situação trocada no balão tem que seguir as etapas de
    verdade configuradas pela conta (as mesmas do board), não uma lista fixa
    inventada no JS — cada conta pode ter etapas diferentes."""
    js = "\n".join(_scripts(_render("prospeccao")))
    m = re.search(r"var _KB_STATUS=(\[.*?\]);", js)
    assert m, "_KB_STATUS não foi embutido no JS servido"
    assert json.loads(m.group(1)) == [["novo", "Novo"], ["contatado", "Contatado"]], (
        "as etapas embutidas no JS não batem com as da conta (contexto `status`)")
    corpo_kbleadhtml = js.split("function kbLeadHtml")[1].split("function kbLeadStatus")[0]
    assert "_KB_STATUS" in corpo_kbleadhtml, (
        "o <select> de situação do balão não usa a lista real de etapas")


def test_trocar_situacao_no_balao_move_o_card_e_fecha_o_balao():
    """Sem mover o card, quem troca a situação no balão vê o board "mentindo"
    até recarregar a página — o mesmo problema que o drag-and-drop já resolve
    (kbDrop). Reaproveita a mesma varredura de contagem (_kbAposMoverStatus)
    em vez de duplicá-la, e fecha o balão: como ele é `fixed` (não acompanha o
    card), deixá-lo aberto depois do card se mudar de coluna reproduziria o
    mesmo "balão desgrudado do lead" que motivou o fechar-ao-rolar do chat."""
    fonte = inspect.getsource(pp)
    fonte_status = fonte.split("function kbLeadStatus")[1][:900]
    assert "/status'" in fonte_status, "não usa a rota de status que já existe"
    assert "_kbAposMoverStatus(d)" in fonte_status, (
        "não reaproveita a mesma atualização de contagem do drag-and-drop")
    assert "colNova.appendChild(card)" in fonte_status, (
        "não move o card pra coluna nova depois de trocar a situação")
    assert "kbFecharLead()" in fonte_status.split("colNova.appendChild(card)")[1], (
        "não fecha o balão depois de mover o card — ficaria flutuando desgrudado do lead")


def test_abrir_o_chat_e_abrir_o_resumo_do_lead_se_excluem():
    """Só um balão por vez, do tipo que for — chat e resumo do lead não podem
    ficar abertos ao mesmo tempo (se sobrepõem e nenhum fica legível)."""
    fonte = inspect.getsource(pp)
    fonte_chat = fonte.split("function kbAbrirChat")[1][:400]
    assert "kbFecharLead()" in fonte_chat, "abrir o chat não fecha o resumo do lead"
    fonte_lead = fonte.split("function kbAbrirLead")[1][:400]
    assert "kbFecharChat()" in fonte_lead, "abrir o resumo do lead não fecha o chat"


def test_dados_do_balao_tem_o_cadastro_todo_nao_so_4_campos():
    """21/08, 2ª rodada: o usuário aprovou um mockup com contato, telefone,
    WhatsApp, e-mail, Instagram, site, valor e observação — a 1ª versão só
    tinha telefone/e-mail/valor/documento. Essa é a guarda de fidelidade."""
    fonte = inspect.getsource(pp).split("function kbLeadDadosHtml")[1]
    fonte = fonte[:fonte.index("function kbLeadHistHtml")]
    for campo in ("d.contato", "d.whatsapp", "d.instagram", "d.site_url", "d.obs", "d.valor_fmt"):
        assert campo in fonte, f"{campo} sumiu do 'Dados' do balão"


def test_secao_de_atividades_chama_historico_igual_a_ficha_completa():
    fonte = inspect.getsource(pp).split("function kbLeadHistHtml")[1][:300]
    assert "Histórico" in fonte
    assert "Últimas atividades" not in fonte, "nome antigo não bate mais com a ficha completa"


def test_editar_o_lead_vira_formulario_no_proprio_balao():
    """O pedido: um botão de editar que salva rápido, sem abrir a ficha
    inteira de novo (senão volta ao problema original). `kbLeadEditar` só
    troca a visibilidade de dois <div> que já estão no balão — não navega."""
    fonte = inspect.getsource(pp)
    assert "kbLeadEditar" in fonte and "kbLeadCancelarEdicao" in fonte and "kbLeadSalvar" in fonte
    fonte_editar = fonte.split("function kbLeadEditar()")[1][:300]
    assert "lp-view" in fonte_editar and "lp-edit" in fonte_editar
    fonte_salvar = fonte.split("function kbLeadSalvar")[1][:900]
    assert "/editar-rapido" in fonte_salvar, "não salva na rota de edição rápida"
    assert "location.href" not in fonte_salvar and "location.reload" not in fonte_salvar, (
        "salvar não pode navegar/recarregar a página — o balão fecharia sozinho")


def test_edicao_rapida_do_balao_nao_toca_documento_nem_dados_de_empresa():
    """Documento (CNPJ/CPF), tipo PF/PJ, segmento, cidade/UF, sócio, regime e
    porte ficam só na ficha completa — a ficha tem verificação própria de
    CNPJ e esses dados não são pra digitar livre num balão rápido."""
    fonte_edit = inspect.getsource(pp).split("function kbLeadEditHtml")[1]
    fonte_edit = fonte_edit[:fonte_edit.index("function kbLeadEditar")]
    for proibido in ("lp-ed-cnpj", "lp-ed-cpf", "lp-ed-documento", "lp-ed-tipo",
                      "lp-ed-segmento", "lp-ed-cidade", "lp-ed-uf", "lp-ed-socio",
                      "lp-ed-regime", "lp-ed-porte"):
        assert proibido not in fonte_edit, f"{proibido} não devia estar editável no balão"


def test_x_de_limpar_a_busca_nao_esmaga_o_campo_de_texto():
    """22/08: o texto digitado na busca da Comunicação sumia — a caixa
    filtrava direitinho (a busca É funcional), mas ficava visualmente vazia.

    Causa: `button{width:100%}` é regra GLOBAL do template base (pros botões
    de formulário de login/cadastro) e `.cx-busca-x` (o ✕ de limpar) não tinha
    `width` própria pra vencer essa herança. O ✕ nasce `display:none` — some
    junto com a caixa vazia — e só aparece quando existe texto (JS liga o
    display em `cxBuscaDigitou`). No instante em que aparece, ele vira um
    flex-item pedindo 100% da largura da barra como flex-basis, e o campo de
    texto (que tem `min-width:0`, exatamente pra poder encolher) é espremido
    até ~0px — a caixa continua com o valor certo por dentro, só não sobra
    pixel nenhum pra desenhar as letras. Comprovado com Playwright: media a
    largura real do campo antes/depois do ✕ aparecer.

    `width:auto` (+ `flex:none`, no mesmo padrão já usado em `.cx-lupa` do
    outro lado do campo) tira o ✕ da disputa por espaço."""
    fonte = inspect.getsource(pp)
    regra = fonte.split(".cx-busca-x{")[1].split("}")[0]
    assert "width:auto" in regra, (
        "sem width:auto, o ✕ herda o button{width:100%} global e esmaga o campo de busca "
        "assim que aparece (ao digitar algo) — a busca funciona, mas o texto digitado some")


def test_trocar_vendedor_no_card_so_aparece_pra_quem_pode_atribuir():
    """22/08: pedido pra organizar leads na fase inicial sem sair do funil —
    o card ganha um <select> de vendedor. Só pra quem `pode_atribuir` (dono),
    igual a regra que já existe hoje pra atribuir lead na ficha/Comunicação;
    quem só tem gerência (gestor) continua vendo o nome como texto fixo."""
    html_dono = _render("prospeccao", pode_atribuir=True,
                         vendedores=[{"id": 9, "nome": "Jacqueline Prime", "papel": "vendedor"},
                                     {"id": 11, "nome": "Thiago Pinheiro", "papel": "vendedor"}])
    assert 'class="kbvend"' in html_dono, "o seletor de vendedor não apareceu pro dono"
    assert 'onchange="kbAtribuirVendedor(this,1)"' in html_dono
    assert "Jacqueline Prime" in html_dono and "Thiago Pinheiro" in html_dono
    assert "— sem responsável —" in html_dono

    html_gestor = _render("prospeccao", gerencia=True, pode_atribuir=False,
                          vendedores=[{"id": 9, "nome": "Jacqueline Prime", "papel": "vendedor"}])
    assert 'class="kbvend"' not in html_gestor, (
        "gestor sem pode_atribuir não devia ver o seletor — só o dono atribui, hoje")


def test_trocar_vendedor_usa_a_mesma_rota_que_a_ficha_ja_usa():
    """Sem rota nova: reaproveita POST /painel/prospeccao/<id>/atribuir, que já
    existe e já é chamada por fetch em outro lugar deste mesmo arquivo (o menu
    de responsável da Comunicação) — mesma regra de permissão nos dois."""
    fonte = inspect.getsource(pp).split("function kbAtribuirVendedor")[1][:700]
    assert "/atribuir'" in fonte
    assert "'X-Requested-With':'fetch'" in fonte, (
        "sem esse header a rota devolve redirect (não JSON) — /atribuir só responde "
        "JSON quando reconhece o pedido como ajax")
    assert "sel.value=prev" in fonte, (
        "sem reverter o <select> num erro, a tela mostra um vendedor que não foi salvo")


def test_botao_de_fechar_o_balao_fica_cravado_no_canto_nao_no_fluxo_do_cabecalho():
    """22/08: pedido de polish — o ✕ vira um círculo `position:absolute` colado
    no canto superior direito do balão inteiro (não mais dentro da linha
    flex do cabeçalho), pra nunca ser empurrado por um título comprido nem
    quebrar de linha junto com o resto. Mesmo botão nos dois balões (chat e
    resumo do lead) — `.pop-close` é compartilhada de propósito aqui: é
    estilo puramente visual, sem estado, sem risco de um balão fechar o
    outro por engano."""
    fonte = inspect.getsource(pp)
    regra = fonte.split(".pop-close{")[1].split("}")[0]
    assert "position:absolute" in regra and "top:" in regra and "right:" in regra
    assert "border-radius:50%" in regra, "botão de fechar não virou um círculo"
    js = "\n".join(_scripts(_render("prospeccao")))
    assert 'class="pop-close"' in js, "o balão de chat/lead não usa mais o botão novo de fechar"
    assert 'class="x" onclick="kbFecharChat()"' not in js, "sobrou o botão antigo no balão de chat"
    assert 'class="x" onclick="kbFecharLead()"' not in js, "sobrou o botão antigo no balão do lead"


def test_x_de_excluir_lead_no_card_nao_esmaga_igual_o_da_busca_ja_esmagou():
    """22/08, 3ª rodada: MESMO bug do ✕ da busca da Comunicação (#537) e quase
    do ✕ de fechar os balões (#538) — achado agora no ✕ de EXCLUIR lead do
    card do funil, reportado com print mostrando o ✕ (vermelho, hover)
    flutuando solto perto do rodapé do card em vez de ficar ao lado do nome.

    Comprovado com Playwright: `.kbx` tinha `margin-top` computado de
    22.4px (o `button{margin-top:1.4rem}` global, pros formulários de
    login/cadastro, vazando por falta de `margin:0` na classe) — o botão
    ficava ~22px mais abaixo da linha do nome. `width:auto` sozinho (que
    `.kbx` também não tinha) não bastava; era o `margin-top` que empurrava."""
    fonte = inspect.getsource(pp)
    regra = fonte.split(".kbx{")[1].split("}")[0]
    assert "margin:0" in regra, (
        "sem margin:0, o ✕ de excluir lead herda margin-top:1.4rem do button{} global "
        "e nasce empurrado pra baixo, longe do nome do lead")
    assert "width:auto" in regra


def test_botao_editar_do_balao_tambem_tinha_o_mesmo_vazamento_de_margem():
    """Achado ao investigar o ✕ de excluir: `.lp-edit-btn` só zerava
    `margin-left` (pra empurrar pra direita) — o `margin-top` do `button{}`
    global continuava vazando, e o botão "✎ Editar" nascia ~22px mais baixo
    dentro de ".lp-sh" (mascarado por `align-items:center`, por isso não foi
    percebido na primeira verificação). `margin:0 0 0 auto` zera tudo e
    mantém só o empurrão pra direita."""
    fonte = inspect.getsource(pp)
    regra = fonte.split(".lp-edit-btn{")[1].split("}")[0]
    assert "margin:0 0 0 auto" in regra or ("margin-top:0" in regra and "margin-left:auto" in regra), (
        "margin-left:auto sozinho não zera margin-top — o button{} global ainda vaza")


def test_nome_de_empresa_longo_nao_empurra_o_x_de_excluir_pra_fora_do_card():
    """22/08, 4ª rodada: bug DIFERENTE do vazamento de margem — reportado com
    print mostrando dois cards lado a lado ("Bruna", nome curto, com o ✕
    visível; "Jacque/Verônica", nome mais longo e sem espaço pra quebrar,
    sem ✕ nenhum aparecendo). "quando o nome não quebra o X vai pra fora e
    não consigo apagar o lead".

    Causa: a linha `<div style="display:flex;align-items:center;gap:.4rem">`
    do card tem `.tdot` + `.emp` (nome) + `.kbx` (✕). `.emp` não tinha
    `min-width:0`, então um nome sem espaço pra quebrar vira min-content e
    empurra o ✕ inteiro pra fora da largura fixa do card — e como nem
    `.kbcard` nem a linha têm `overflow:hidden`, o ✕ não é cortado, some de
    vez (renderiza fora da área visível).

    Comprovado com Playwright: card com nome curto ficava com o botão dentro
    da borda do card; card com nome longo sem espaços tinha o botão inteiro
    fora da largura do card. Com `min-width:0;overflow:hidden;text-overflow:
    ellipsis;white-space:nowrap;flex:1` no `.emp`, o nome trunca com "…" e o
    ✕ (com `flex:none`) sempre sobra visível dentro do card."""
    fonte = inspect.getsource(pp)
    regra = fonte.split(".kbcard .emp{")[1].split("}")[0]
    assert "min-width:0" in regra, (
        "sem min-width:0, um nome de empresa sem espaço pra quebrar vira "
        "min-content e empurra o ✕ pra fora do card")
    assert "overflow:hidden" in regra and "text-overflow:ellipsis" in regra and "white-space:nowrap" in regra, (
        "sem truncar o nome com ellipsis, ele estoura a largura do card")


def test_selo_de_campanha_de_origem_trunca_igual_ao_nome_da_empresa():
    """Mockup aprovado (docs/mockups/funil_campanha_origem.html, opção B): o card
    do lead ganha um selo "📣 <campanha>" quando o lead veio de uma campanha de
    prospecção, com "· 📱 <apelido do chip>" quando a conta tem 2+ chips.

    O selo não é item de um flex row (é um <div> de linha inteira, como `.sub`),
    então não precisa de `min-width:0` — mas precisa da MESMA lição do `.emp` da
    3ª rodada: sem truncar, um nome de campanha comprido estoura a largura fixa
    do card."""
    fonte = inspect.getsource(pp)
    regra = fonte.split(".kbcard .camp{")[1].split("}")[0]
    assert "overflow:hidden" in regra and "text-overflow:ellipsis" in regra and "white-space:nowrap" in regra, (
        "sem truncar, um nome de campanha comprido estoura a largura do card")
    assert "max-width:100%" in regra


def test_selo_de_campanha_so_aparece_no_template_quando_o_lead_tem_campanha():
    """O selo não pode ser incondicional — boa parte dos leads vem da Base
    manual, sem campanha nenhuma associada (ver docs/mockups/, seção 2)."""
    fonte = inspect.getsource(pp)
    assert '{% if c.campanha %}<div class="camp">' in fonte, (
        "o selo de campanha no card Jinja precisa estar atrás de um {% if c.campanha %}")
    assert "l.campanha?('<div class=\"camp\">" in fonte, (
        "o addCard() em JS (lead capturado sem recarregar a página) precisa da mesma condição")
