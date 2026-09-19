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
    assert PA.count("def _leitura_em_json(") == 1
    assert PA.count("_leitura_em_json(") == 3    # a definição e os dois caminhos
    assert PA.count('_env.get_template("renovacoes_conf")') == 1


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
