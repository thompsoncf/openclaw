"""O cadastro de apólice é uma JANELA, e ela não troca de página.

Três regras que o dono deu em 18/09/2026, olhando a tela em produção:

    "o botao + nova apolice nao ta funcionando e nao deixa abrir outra pagina
     sempre abre uma janela popup nada de refresh faz uma coisa rapida e deixa
     o back trabalhando"

O botão não funcionava por um motivo concreto: o cadastro era um `<details
id="nova">` DENTRO da aba "Renovações", e o botão do topo era uma âncora
`href="#nova"`. Nas abas Carteira e Percentuais a âncora não existia — clicar
não fazia nada, sem erro nenhum. O que este arquivo impede de voltar é isso: o
cadastro preso a uma aba, e qualquer caminho que recarregue a página pra mostrar
o que acabou de ser cadastrado.
"""
import pathlib

import pytest

RAIZ = pathlib.Path(__file__).resolve().parent.parent / "web"
PA = (RAIZ / "painel_apolices.py").read_text(encoding="utf-8")
PORTAL = (RAIZ / "portal.py").read_text(encoding="utf-8")


def corpo_de(nome: str) -> str:
    """Só ESTA função. Fatiar até o fim do arquivo varre o módulo inteiro e faz
    o teste passar (ou falhar) por causa de outra rota."""
    i = PA.index(f"def {nome}(")
    resto = PA[i + 1:]
    fins = [resto.index(m) for m in ("\n@router.", "\ndef ", "\n_TPL") if m in resto]
    return PA[i:i + 1 + min(fins)] if fins else PA[i:]


def test_o_botao_do_topo_abre_a_janela_e_nao_uma_ancora():
    """A âncora era o defeito: `#nova` só existia numa das três abas."""
    assert 'href="#nova"' not in PA
    assert 'id="nova"' not in PA
    assert PA.count('onclick="rnAbrir(event)"') >= 2      # topo e chip da fila


def test_a_janela_mora_fora_das_abas():
    """Fora do if/elif das abas — senão o botão volta a não funcionar em duas delas."""
    assert PA.count('id="rn-jan"') == 1
    fim_das_abas = PA.index("</div>{# .rn-pag #}")
    assert PA.index('id="rn-jan"') > fim_das_abas


def test_nada_dentro_da_janela_navega():
    """Os dois formulários são interceptados; sem isso o POST troca de página."""
    assert 'onsubmit="return rnLerPdf(this)"' in PA
    assert 'onsubmit="return rnSalvar(this)"' in PA
    # e o que o JavaScript usa pra não recarregar: a linha vem pronta do servidor
    assert "d.linha_html" in PA and 'el(\'rn-corpo\')' in PA
    assert "location.reload" not in PA


def test_o_caminho_sem_javascript_continua_de_pe():
    """Quem está sem JavaScript posta do jeito antigo e recebe a página inteira."""
    assert 'method="post" action="/painel/renovacoes/importar"' in PA
    assert 'method="post" action="/painel/renovacoes/apolice"' in PA
    # o botão de enviar existe no HTML e o próprio JavaScript o esconde
    assert 'class="rn-bt fraco semjs"' in PA
    assert "if(semjs) semjs.hidden = true;" in PA
    # e o servidor continua sabendo responder a página (o 303 só sai sem `json`)
    assert 'return _render("renovacoes", request, **ctx)' in PA


def test_uma_marcacao_so_pra_linha_da_carteira():
    """A linha nova entra na tabela pelo MESMO template que a tabela usa."""
    assert '{% include "renovacoes_linha" %}' in PA
    assert '_env.get_template("renovacoes_linha")' in PA
    assert PA.count("<tr data-id=") == 1                  # só no template da linha


def test_a_linha_carrega_o_que_o_javascript_precisa():
    """`data-vence` é como o JavaScript acha o lugar dela — a tabela é ordenada."""
    from datetime import date

    import web.painel_apolices  # noqa: F401  (registra os templates)
    from web.portal import _env
    a = {"id": 7, "cliente_id": 3, "cliente": "Fulano", "seguradora": "Allianz",
         "ramo_txt": "Auto", "vigencia_fim": date(2027, 7, 23), "dias": 308,
         "situacao_txt": "Proposta", "premio_centavos": 380757,
         "comissao_estimada": 76151, "bem": {"placa": "ABC1D23"}, "tem_pdf": True}
    html = _env.get_template("renovacoes_linha").render(a=a, brl=lambda c: f"R$ {c/100:.2f}")
    assert 'data-id="7"' in html
    assert 'data-vence="2027-07-23"' in html
    assert "kbAbrirSegurado(event,3," in html


def test_a_conferencia_e_um_template_so():
    """O mesmo painel serve a página (sem JavaScript) e a janela (em JSON)."""
    assert '{% include "renovacoes_conf" %}' in PA
    assert '_env.get_template("renovacoes_conf")' in PA

    import web.painel_apolices  # noqa: F401  (registra os templates)
    from web.portal import _env
    html = _env.get_template("renovacoes_conf").render(conferir={
        "pdf_nome": "x.pdf", "reconhecida": True, "seguradora": "Allianz",
        "n_campos": 20, "paginas": 7, "checagens": [("CPF", True, "bate")],
        "avisos": [], "nao_achou": []})
    assert "O que eu li de" in html and "CPF" in html


def test_o_visto_da_conferencia_nao_herda_a_caixa_verde_global():
    """`.ok` existe no portal como caixa de status; sem zerar, cada ✓ vira quadrado."""
    assert ".ok{background:#15301f;border:1px solid var(--verde)" in PORTAL
    assert ".rn-conf .ok,.rn-conf .no{background:none;border:0" in PA


@pytest.mark.parametrize("rota", ["importar_pdf", "salvar_apolice"])
def test_as_rotas_respondem_json_quando_a_janela_pede(rota):
    """Um 303 pra quem pediu JSON vira HTML no `fetch` e a janela mente "não respondeu"."""
    corpo = corpo_de(rota)
    assert 'alias="json"' in corpo
    assert "def _falhou(msg: str" in corpo
    assert "JSONResponse({\"ok\": False, \"erro\": msg})" in corpo
    assert "quer_json" in corpo


def test_o_botao_da_janela_vence_o_css_global_de_formulario():
    """`button{width:100%}` do portal transformaria cada botão numa barra verde."""
    assert ".rn-bt{background:var(--verde);color:var(--sobre-verde);border:0;border-radius:8px;width:auto;margin:0;" in PA
    assert ".rn-chip{" in PA and "width:auto" in PA


# ───────────── a segunda porta: o PDF que já chegou no WhatsApp ─────────────


def test_a_lista_do_whatsapp_nao_cadastra_nada_sozinha():
    """Ler tudo e gravar encheria a carteira de boleto — e vigência errada é
    alerta que não dispara, o pior defeito desta tela."""
    corpo = corpo_de("ler_pdf_do_whatsapp")
    assert "ap.salvar(" not in corpo
    assert "_leitura_em_json(" in corpo          # termina na conferência, e só


def test_o_pdf_do_whatsapp_passa_pela_mesma_conferencia_e_pelo_mesmo_salvar():
    """Dois caminhos de entrada, uma saída: `_conferir_de` + `_leitura_em_json`."""
    assert PA.count("def _conferir_de(") == 1
    # uma montagem só da resposta, pros TRÊS caminhos que chegam na conferência:
    # o PDF solto, o do WhatsApp lido na hora, e o que o leitor automático já leu
    assert PA.count("def _conferencia_em_json(") == 1
    assert PA.count('_env.get_template("renovacoes_conf")') == 1
    # a definição e as TRÊS portas: PDF solto, WhatsApp já lido, pré-cadastro do
    # Telegram. O que o teste guarda é não existir uma quarta montagem da resposta
    assert PA.count("return _conferencia_em_json(") == 3
    # e uma montagem só da conferência guardada, pras duas origens que a usam
    assert PA.count("def _conferir_guardado(") == 1


def test_o_escopo_do_documento_e_da_conta():
    """O id da mensagem é sequencial: sem o casamento com a conta, trocar o número
    na URL leria o documento do cliente de outra corretora."""
    import inspect

    from finance import apolices as ap
    fonte = inspect.getsource(ap.ref_do_pdf) + inspect.getsource(ap.pdfs_do_whatsapp)
    assert "cv.conta_id = %s" in fonte
    assert "join conversas cv on cv.id = m.conversa_id" in fonte
    # e a rota não monta consulta própria — usa a função que já tem o escopo
    corpo = corpo_de("ler_pdf_do_whatsapp")
    assert "ap.ref_do_pdf(get_pool(), conta[0], mensagem_id)" in corpo
    assert "select" not in corpo.lower()


def test_o_recado_de_arquivo_expirado_e_o_certo():
    """O CDN do WhatsApp apaga. Mandar tentar de novo seria mentira."""
    assert "except _wm.Expirou:" in PA
    assert "o WhatsApp já apagou este arquivo" in PA


def test_a_marca_de_origem_e_o_que_tira_o_documento_da_lista():
    assert '"whatsapp_msg": mensagem_id' in PA
    from finance import apolices as ap
    import inspect
    assert "pdf_lido->'origem'->>'whatsapp_msg'" in inspect.getsource(ap.pdfs_do_whatsapp)


# ────────── quem pode mandar: a lista é limitada por remetente ──────────


def test_a_lista_so_mostra_pdf_de_quem_foi_liberado():
    """Pedido do dono em 19/09: o número da empresa não bastava, porque o
    fornecedor de boleto escreve pro mesmo número."""
    import inspect

    from finance import apolices as ap
    fonte = inspect.getsource(ap.pdfs_do_whatsapp)
    assert "exists (select 1 from apolice_remetentes r" in fonte
    assert "r.contato_ref = cv.contato_ref" in fonte


def test_liberar_exige_conversa_na_propria_conta():
    """Sem isso, mandar outro número na requisição liberaria quem quisesse."""
    import inspect

    from finance import apolices as ap
    fonte = inspect.getsource(ap.liberar_remetente)
    assert "select 1 from conversas where conta_id = %s and contato_ref = %s" in fonte
    assert "este número não tem conversa nesta conta" in fonte


def test_as_rotas_de_remetente_sao_de_gerencia():
    for rota in ("remetentes_do_whatsapp", "mudar_remetente"):
        corpo = corpo_de(rota)
        assert "if not gerencia:" in corpo
        assert "só o dono e o gestor" in corpo


def test_o_painel_de_remetentes_vive_na_mesma_janela():
    """Uma segunda tela pra isso seria um lugar a mais pra esquecer."""
    assert 'id="rn-p-fontes"' in PA
    assert "'fontes'" in PA and "PASSOS = ['pdf', 'lendo', 'form', 'ok', 'fontes']" in PA
    assert 'onclick="rnFontes()"' in PA


def test_o_estado_vazio_diz_o_que_fazer():
    """Lista vazia sem explicação pareceria defeito; ela tem dois motivos."""
    assert "Ninguém liberado ainda. " in PA
    assert "Nada novo de quem você liberou" in PA


def test_a_migracao_nasce_vazia_e_e_reversivel():
    import pathlib
    sql = (pathlib.Path(__file__).resolve().parent.parent
           / "db" / "migracoes" / "289_apolice_remetentes.sql").read_text(encoding="utf-8")
    assert "create table if not exists public.apolice_remetentes" in sql
    assert "create unique index if not exists ux_apolice_remetentes" in sql
    assert "insert into public.apolice_remetentes" not in sql      # nada semeado
    assert "-- rollback:" in sql


# ────────── a segunda porta: o Telegram entra na mesma lista ──────────


def test_o_telegram_nao_ganha_tela_propria():
    """Duas entradas pro mesmo lugar; separar faria a pessoa procurar em dois
    cantos o documento que ela acabou de mandar."""
    assert "sem_mensagem(pool, conta[0])" in PA
    assert '"fonte": "lida"' in PA and '"fonte": "msg"' in PA
    assert "já chegou no WhatsApp ou no Telegram" in PA


def test_cada_fonte_tem_o_proprio_endereco():
    assert '"/painel/renovacoes/lida/{lida_id}"' in PA      # a rota
    assert "'/painel/renovacoes/lida/'" in PA                 # o endereço no JavaScript
    assert "window.rnDoWhats = function(fonte, id)" in PA
    assert "rnDoWhats(it.fonte, it.id)" in PA


def test_o_pre_cadastro_do_telegram_nao_baixa_nada():
    """Ele não tem mensagem pra procurar, e a leitura já está guardada."""
    corpo = corpo_de("abrir_pre_cadastro")
    assert "_apl.por_id(get_pool(), conta[0], lida_id)" in corpo
    assert "wa_midia" not in corpo and "buscar(" not in corpo


def test_a_porta_do_telegram_e_de_gerencia():
    corpo = corpo_de("abrir_pre_cadastro")
    assert "if not gerencia:" in corpo


def test_o_bot_do_telegram_nao_sequestra_o_comprovante():
    """Documento sem as marcas de um seguro volta pro caminho do caixa: quem usa o
    bot pra comprovante não pode perder isso porque a conta virou corretora."""
    import pathlib
    bot = (pathlib.Path(__file__).resolve().parent.parent
           / "telegram_bot.py").read_text(encoding="utf-8")
    assert "async def _tentar_apolice(" in bot
    assert "if not _apl.tomou(r):" in bot
    assert "_e_seguros" in bot, "só corretora entra nesta porta"
    # e a falha da leitura devolve False, deixando o caminho antigo seguir
    corpo = bot[bot.index("async def _tentar_apolice("):]
    corpo = corpo[:corpo.index("\nasync def ")]
    assert "return False" in corpo and "except Exception" in corpo


def test_o_agente_sabe_o_que_e_documento_numa_corretora():
    """O agente só enxerga texto: um anexo chega como a palavra 'Documento'."""
    import pathlib
    ag = (pathlib.Path(__file__).resolve().parent.parent
          / "finance" / "agente.py").read_text(encoding="utf-8")
    assert 'perfil_por_nicho(_slug_n[0] if _slug_n else "") == "seguros"' in ag
    assert "seguros_txt" in ag and "{seguros_txt}" in ag
    assert "NUNCA diga que leu" in ag
    assert "Nunca fale de festa" in ag


# ────────── a terceira porta: o assistente no número da ZAQ (21/09/2026) ──────────


def _app() -> str:
    return (RAIZ / "app.py").read_text(encoding="utf-8")


def test_o_assistente_tenta_apolice_antes_do_caixa():
    """Depois do caixa já é tarde. Em 21/09/2026 a apólice da Azul mandada pra este
    número virou lembrete de pagar parcela — leu o carnê e guardou o documento na
    gaveta errada."""
    app = _app()
    i = app.index('elif "pdf" in ctype:')
    trecho = app[i:i + 1600]
    assert "_tentar_apolice_wpp(" in trecho
    assert trecho.index("_tentar_apolice_wpp(") < trecho.index("imagem_b64 = base64")
    assert "return" in trecho


def test_a_porta_do_assistente_tem_o_mesmo_portao_do_telegram():
    app = _app()
    corpo = app[app.index("def _tentar_apolice_wpp("):]
    corpo = corpo[:corpo.index("\ndef ")]
    assert "_e_seguros" in corpo, "só corretora entra nesta porta"
    assert "_cofre.configurado()" in corpo
    assert "_apl.tomou(r)" in corpo


def test_a_porta_do_assistente_devolve_o_comprovante_pro_caixa():
    """O que NÃO pode quebrar: o comprovante de Pix de quem usa o mesmo número
    pras duas coisas. Qualquer saída que não seja apólice devolve False."""
    app = _app()
    corpo = app[app.index("def _tentar_apolice_wpp("):]
    corpo = corpo[:corpo.index("\ndef ")]
    assert corpo.count("return False") >= 3
    assert "except Exception" in corpo


def test_a_porta_do_assistente_nao_cadastra_apolice():
    app = _app()
    corpo = app[app.index("def _tentar_apolice_wpp("):]
    corpo = corpo[:corpo.index("\ndef ")]
    assert "ler_bytes" in corpo
    assert "salvar_apolice" not in corpo and "insert into apolices" not in corpo


def test_as_tres_portas_dizem_a_mesma_coisa():
    """Um só lugar monta o aviso; o que muda entre as portas é o negrito."""
    app = _app()
    bot = (pathlib.Path(__file__).resolve().parent.parent
           / "telegram_bot.py").read_text(encoding="utf-8")
    for texto in (app, bot):
        assert "_apl.campos_do_aviso(leitura)" in texto
        assert "_apl.rodape_do_aviso(leitura)" in texto


def test_o_pre_cadastro_sem_mensagem_entra_na_lista_seja_qual_for_a_porta():
    """Por origem, o que chega pelo assistente (origem 'whatsapp', sem mensagem)
    ficaria invisível: fora de `sem_mensagem` e fora de `pdfs_do_whatsapp`."""
    import pathlib as _p
    leitor = (_p.Path(__file__).resolve().parent.parent
              / "finance" / "apolice_leitor.py").read_text(encoding="utf-8")
    corpo = leitor[leitor.index("def sem_mensagem("):]
    corpo = corpo[:corpo.index("\ndef ")]
    assert "where l.conta_id = %s and l.mensagem_id is null" in corpo
    assert "l.origem = " not in corpo, "a origem não pode voltar a ser o filtro"


# ────────── a saída da conferência e o descarte (21/09/2026) ──────────
#
# "quando abri e clico na apólice para aprovar não consigo sair caso não guarde, e
# como tem apólice repetida lá tenho que ver uma forma de resolver isso".
#
# Eram dois becos. O passo do formulário só tinha "cancelar", que FECHA a janela —
# e `rnAbrir` preserva a conferência em andamento de propósito, então reabrir caía
# no mesmo formulário. E a fila só esvaziava CADASTRANDO.


def test_o_formulario_tem_saida_pra_lista():
    """Sem isto, quem abriu o documento errado só saía cadastrando ou recarregando."""
    assert 'onclick="rnParaLista()"' in PA
    assert "window.rnParaLista = function()" in PA


def test_voltar_pra_lista_limpa_o_rascunho():
    """Sem limpar, a conferência antiga reaparece por cima da próxima."""
    corpo = PA[PA.index("window.rnParaLista = function()"):]
    corpo = corpo[:corpo.index("\n  window.")]
    assert "c.innerHTML = ''" in corpo
    assert "f.reset()" in corpo
    assert "wppLida = false" in corpo, "a lista pode ter mudado enquanto se conferia"
    assert "passo('pdf'" in corpo


def test_cada_pre_cadastro_tem_como_sair_da_fila():
    assert "x.className = 'rn-descartar'" in PA
    assert "'/painel/renovacoes/lida/' + id + '/descartar'" in PA
    assert "function wppDescartar(id, linha)" in PA
    assert ".rn-descartar{" in PA, "o botão global é width:100%; sem o próprio, estica"


def test_o_descarte_nao_dispara_a_abertura_do_documento():
    """O ✕ vive DENTRO da linha clicável; sem parar a propagação, descartar abriria
    a conferência do que acabou de sair da fila."""
    corpo = PA[PA.index("if(it.fonte === 'lida'){"):]
    corpo = corpo[:corpo.index("lista.appendChild(b);")]
    assert "ev.stopPropagation()" in corpo


def test_o_descarte_oferece_desfazer():
    corpo = PA[PA.index("function wppDescartar(id, linha)"):]
    corpo = corpo[:corpo.index("\n  // a faixa do topo")]
    assert "desfazer" in corpo and "fd.append('desfazer', '1')" in corpo


def test_a_faixa_do_topo_acompanha_sem_recarregar():
    """Ela conta a MESMA fila: descartar ali e a faixa continuar dizendo 4 é a tela
    mentindo."""
    assert "function faixaAtualizar(quantos)" in PA
    corpo = PA[PA.index("function faixaAtualizar(quantos)"):]
    corpo = corpo[:corpo.index("\n  window.")]
    assert ".rn-espera" in corpo and "f.hidden = true" in corpo


def test_a_rota_de_descarte_e_de_gerencia():
    corpo = corpo_de("descartar_pre_cadastro")
    assert "if not gerencia:" in corpo
    assert "_apl.descartar(" in corpo and "_apl.voltar_da_lixeira(" in corpo


def test_cadastrar_limpa_as_irmas_da_fila():
    """Casar por `pdf_caminho` tira só o documento confirmado; as outras leituras
    do mesmo papel têm cada uma o seu caminho no cofre."""
    corpo = corpo_de("salvar_apolice")
    assert "_apl.descartar_irmas(pool, conta[0], numero_apolice" in corpo
    assert "except Exception" in corpo, "limpar a fila não pode derrubar o cadastro"


# ────────── reler com o leitor de hoje (22/09/2026) ──────────
#
# "ver a questão do layout logo". Cada seguradora nova entra em `_LAYOUTS` DEPOIS
# que o primeiro PDF dela chega, e quem já estava na fila ficava com a leitura
# velha pra sempre: a Liberal tinha duas Mapfre lidas antes do layout da Mapfre e
# uma Porto lida antes do layout da Porto. O documento certo estava guardado o
# tempo todo; o que estava velho era a leitura.


def test_a_fila_pode_ser_relida_da_propria_tela():
    assert 'onclick="rnReler(this)"' in PA
    assert "window.rnReler = function(bt)" in PA
    assert '"/painel/renovacoes/reler"' in PA


def test_reler_nao_baixa_nada_de_fora():
    """O arquivo já está no cofre. Ir ao CDN do WhatsApp de novo seria pedir um
    arquivo que pode ter expirado, pra ler o que já está guardado."""
    import pathlib
    leitor = (pathlib.Path(__file__).resolve().parent.parent
              / "finance" / "apolice_leitor.py").read_text(encoding="utf-8")
    corpo = leitor[leitor.index("def reler(pool"):]
    corpo = corpo[:corpo.index("\ndef reler_a_fila")]
    assert "_cofre.ler(" in corpo
    assert "wa_midia" not in corpo and "buscar(" not in corpo


def test_reler_atualiza_em_vez_de_inserir():
    """Criar linha nova daria uma quarta cópia do que já está na fila."""
    import pathlib
    leitor = (pathlib.Path(__file__).resolve().parent.parent
              / "finance" / "apolice_leitor.py").read_text(encoding="utf-8")
    corpo = leitor[leitor.index("def reler(pool"):]
    corpo = corpo[:corpo.index("\ndef reler_a_fila")]
    assert "update apolice_lida set" in corpo
    assert "insert into apolice_lida" not in corpo


def test_a_rota_de_reler_e_de_gerencia_e_exige_cofre():
    corpo = corpo_de("reler_a_fila")
    assert "if not gerencia:" in corpo
    assert "_cofre.configurado()" in corpo


def test_reler_recarrega_a_lista_e_a_faixa():
    corpo = PA[PA.index("window.rnReler = function(bt)"):]
    corpo = corpo[:corpo.index("\n  function wppDescartar")]
    assert "wppLida = false" in corpo and "wppCarregar()" in corpo
    assert "faixaAtualizar(d.esperando)" in corpo
    assert "bt.disabled = true" in corpo, "sem travar o botão, dois cliques relêem duas vezes"


# ────────── a linha da carteira ganha ações (22/09/2026) ──────────
#
# "la no final coloca uma seta com o botão igual tem na aba serviços no funil pra
# aparecer os comandos editar, excluir e demais funções" — e, no mesmo pedido, "a
# página ficar tamanho do raio-x" e "colocar a placa do Vicente que não aparece".


def test_a_largura_da_pagina_vem_do_token():
    """O painel tinha CINCO larguras (960, 1040, 1120, 1180 e o Raio-X sem teto).
    A resposta pra "ficar do tamanho do Raio-X" não é acertar uma tela: é ter um
    lugar só onde a largura é decidida."""
    import pathlib
    tema = (pathlib.Path(__file__).resolve().parent.parent
            / "web" / "tema.py").read_text(encoding="utf-8")
    assert "--pag:" in tema
    assert "max-width:var(--pag" in PA
    assert "max-width:1040px" not in PA, "o número solto voltou"


def test_a_placa_vem_antes_do_modelo():
    """O modelo da Ranger do Vicente tem 58 caracteres, e a placa ia no fim —
    aparecia onde ninguém olha. Placa é como a corretora procura um carro."""
    import pathlib
    pa_py = (pathlib.Path(__file__).resolve().parent.parent
             / "web" / "painel_apolices.py").read_text(encoding="utf-8")
    i = pa_py.index("bem_txt = \" · \".join(")
    trecho = pa_py[i:i + 200]
    assert trecho.index('bem.get("placa")') < trecho.index('bem.get("modelo")')


def test_a_placa_do_card_nao_depende_do_modelo():
    """Estava aninhada dentro do `if modelo`: apólice sem modelo cadastrado
    deixava de mostrar a placa junto."""
    assert "{%- if a.bem.placa %}<b>{{ a.bem.placa }}</b> · {% endif -%}" in PA


def test_a_linha_tem_o_menu_de_acoes():
    assert 'onclick="rnMenu(event,{{ a.id }},this)"' in PA
    assert "window.rnMenu = function(ev, id, bt)" in PA
    assert ".rn-menu-bt{" in PA and ".rn-menu{" in PA


def test_o_menu_abre_um_por_vez_e_fecha_no_esc():
    """Dois menus abertos disputam a mesma tela — o mesmo cuidado do funil."""
    corpo = PA[PA.index("var _menuLinha = null;"):]
    corpo = corpo[:corpo.index("window.rnEditar")]
    assert "function fecharMenuLinha()" in corpo
    assert "ev.key === 'Escape'" in corpo
    assert "if(jaEra) return;" in corpo, "clicar de novo no mesmo botão fecha"


def test_editar_manda_o_apolice_id():
    """Sem o campo, salvar CRIA outra apólice em vez de atualizar a que está
    aberta — `salvar_apolice` decide por ele desde a 278."""
    assert '<input type="hidden" name="apolice_id" value="">' in PA
    assert '"apolice_id": str(a["id"])' in PA


def test_voltar_pra_lista_zera_o_apolice_id():
    """Um id esquecido faz o PRÓXIMO cadastro sobrescrever a apólice de outro."""
    corpo = PA[PA.index("window.rnParaLista = function()"):]
    corpo = corpo[:corpo.index("\n  window.")]
    assert "aid.value = ''" in corpo


def test_editar_limpa_o_formulario_antes():
    """Na conferência campo vazio não apaga o padrão; numa EDIÇÃO é o contrário —
    manter o valor de um documento conferido antes encheria o formulário com dado
    de outro cliente."""
    corpo = PA[PA.index("function preencher(d, editando)"):]
    corpo = corpo[:corpo.index("\n  window.rnLerPdf")]
    assert "if(editando){ try { form.reset(); }" in corpo
    assert "editando ? 'Salvar alterações'" in corpo


def test_excluir_e_de_gerencia_e_oferece_desfazer():
    corpo = corpo_de("excluir_apolice")
    assert "if not gerencia:" in corpo
    assert "ap.excluir(" in corpo and "ap.voltar_apolice(" in corpo
    js = PA[PA.index("window.rnExcluir = function(id, tr)"):]
    js = js[:js.index("\n  // o contador da aba")]
    assert "desfazer" in js and "f2.append('desfazer', '1')" in js


def test_excluir_atualiza_o_contador_da_aba():
    """"Carteira (4)" continuar dizendo 4 depois de excluir é a tela mentindo."""
    corpo = PA[PA.index("function contarCarteira(n)"):]
    corpo = corpo[:corpo.index("\n  window.rnReler")]
    assert "rn-conta-carteira" in corpo, "escrever no <a> apagaria a palavra Carteira"
