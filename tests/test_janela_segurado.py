"""A JANELA DO SEGURADO É UM MODO DA JANELA DO LEAD — não uma segunda janela.

Aprovado em 18/09/2026 (docs/mockups/ficha_segurado_janela.html). O que este
arquivo impede de voltar é a cópia: uma segunda `.leadpop` com outra largura e
outro fechamento, ou uma segunda folha de "Por que perdeu?". Duas janelas
divergem — foi o que o balão de conversa (07/09) e a própria janela do lead
(16/09) já tinham pago pra aprender.
"""
import pathlib
import re

RAIZ = pathlib.Path(__file__).resolve().parent.parent / "web"
JL = (RAIZ / "janela_lead.py").read_text(encoding="utf-8")
PA = (RAIZ / "painel_apolices.py").read_text(encoding="utf-8")


def test_o_modo_segurado_mora_no_mesmo_modulo_da_janela_do_lead():
    assert "function kbAbrirSegurado(" in JL
    assert "function kbAbrirSegurado(" not in PA


def test_usa_a_mesma_moldura_e_o_mesmo_fechamento():
    """Mesma classe, mesma largura, mesmo fechar — só o conteúdo muda."""
    seg = JL[JL.index("function kbAbrirSegurado("):]
    assert "pop.className='leadpop'" in seg
    assert "LARG=378" in seg
    assert "kbFecharLead()" in seg and "_leadPopEsc" in seg and "_leadPopFora" in seg
    assert JL.count("className='leadpop'") == 2          # lead e segurado, e mais nenhuma


def test_perdi_usa_a_mesma_folha_de_motivo_do_funil():
    """`kbPerguntarMotivo` ganhou a URL de destino em vez de ser copiada."""
    assert re.search(r"function kbPerguntarMotivo\(id, status, lista, quandoOk, quandoDesiste, url\)", JL)
    assert JL.count("function kbPerguntarMotivo(") == 1
    assert "kbPerguntarMotivo(apId,'perdida',lista" in JL
    assert "/painel/renovacoes/apolice/'+apId+'/situacao" in JL


def test_a_tela_injeta_a_janela_e_o_balao_e_as_listas():
    assert "{{ janela_css }}" in PA and "{{ janela_js }}" in PA
    assert "{{ balao_css }}" in PA and "{{ balao_js }}" in PA
    assert "_KB_MOTIVOS" in PA and "_KB_DECISOES" in PA


def test_css_e_js_da_janela_vao_EMBRULHADOS():
    """As globais são strings cruas; o <style>/<script> é da tela. Soltas, o CSS vira
    um muro de texto no topo da página — foi o que a única olhada antes de publicar
    mostrou em 18/09. O funil e o Follow-up embrulham; aqui também."""
    assert re.search(r"<style>[^<]*\{\{ balao_css \}\}[^<]*\{\{ janela_css \}\}[^<]*</style>", PA)
    assert re.search(r"<script>\s*\{\{ balao_js \}\}\s*</script>", PA)
    assert re.search(r"<script>\s*\{\{ janela_js \}\}\s*</script>", PA)
    # e nunca soltas
    assert not re.search(r"(^|>)\s*\{\{ (balao|janela)_(css|js) \}\}\s*(<(?!/)|$)", PA, re.M)


def test_o_resumo_do_cliente_fica_sob_renovacoes_que_e_o_prefixo_do_corretor():
    """/painel/clientes é do dono; o corretor (vendedor) só alcança /painel/renovacoes.
    O JSON da janela mora onde quem clica consegue chegar."""
    assert '@router.get("/painel/renovacoes/cliente/{cliente_id}/resumo")' in PA
    assert "'/painel/renovacoes/cliente/'+id+'/resumo'" in JL
