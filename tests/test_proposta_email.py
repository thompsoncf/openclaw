"""Mandar a proposta pro cliente por e-mail — pela caixa certa.

O funil sabia gerar o link e abrir o PDF; mandar era por fora, na mão. E ninguém
sabia se já tinha mandado: a pergunta "será que já mandei pra Carla?" só se
respondia abrindo o Gmail — e na dúvida se mandava duas vezes.

A DECISÃO QUE VEM ANTES DO BOTÃO. O Zaq já mandava e-mail em dois casos, com
regras OPOSTAS de propósito:

    convite de equipe   →  remetente do Zaq primeiro. É e-mail de SISTEMA:
                           ninguém responde, só precisa chegar.
    mensagem pra lead   →  caixa da EMPRESA, pra resposta cair no inbox dela.

A proposta é o segundo caso, e com força: o cliente vai apertar Responder. Saindo
do endereço do Zaq, a resposta dele morre numa caixa que a empresa não lê — e o
negócio morre junto, calado.

Aqui prende-se essa ordem, o layout (que é DA EMPRESA, não do Zaq), e o que
acontece quando falha — porque o vendedor tem um cliente esperando.
"""
import pytest

from finance import proposta_email as pe


class _Pool:
    """Pool que não é usado: `remetente()` só consulta o email_inbound, que os
    testes trocam por dublê."""
    def connection(self):
        raise AssertionError("não devia tocar o banco neste caminho")


# ------------------------------------------------------ o texto que abre a tela

def test_a_mensagem_ja_vem_escrita_e_com_o_nome_do_cliente():
    t = pe.texto_padrao("Maria Helena da Silva", "evento")
    assert t.startswith("Oi, Maria!")          # primeiro nome, não o nome inteiro
    assert "orçamento" in t and "responder este e-mail" in t


def test_sem_nome_do_cliente_nao_sai_um_oi_vazio():
    """"Oi, !" é pior que não cumprimentar — e alguém mandaria assim."""
    assert pe.texto_padrao("", "evento").startswith("Oi!")
    assert pe.texto_padrao(None).startswith("Oi!")


@pytest.mark.parametrize("modo,palavra", [("evento", "orçamento"),
                                          ("recorrente", "proposta")])
def test_o_documento_tem_o_nome_do_nicho(modo, palavra):
    """O e-mail vai pro CLIENTE. Chamar de "orçamento" a proposta de mensalidade
    de uma empresa de tecnologia soaria de outro negócio."""
    assert palavra in pe.texto_padrao("Ana", modo)
    assert palavra.capitalize() in pe.assunto_padrao(14, "Prime", modo)


def test_o_assunto_diz_o_numero_e_a_empresa():
    assert pe.assunto_padrao(14, "Prime Eventos", "evento") == "Orçamento nº 14 — Prime Eventos"
    assert pe.assunto_padrao(None, "Prime Eventos", "evento") == "Orçamento — Prime Eventos"


# --------------------------------------------------------------- o e-mail em si

def test_o_email_leva_o_LINK_e_nao_um_anexo():
    """PDF anexo seria cópia morta: o cliente leria e depois teria que achar o link
    de novo pra aprovar. E envelhece — corrigir um valor e reabrir o link mostra o
    valor certo; o PDF de ontem continua dizendo o preço de ontem."""
    html, texto = pe.montar(mensagem="Oi!", link="https://app.zaq-ia.com/proposta/abc123",
                            numero=14, empresa="Prime Eventos", modo="evento")
    assert "https://app.zaq-ia.com/proposta/abc123" in html
    assert "https://app.zaq-ia.com/proposta/abc123" in texto
    assert "Ver o orçamento" in html


def test_o_layout_e_da_empresa_e_nao_do_zaq():
    """O layout de sistema diz "você recebeu porque tem uma conta no Zaq" e põe
    Zaq no topo. O cliente do buffet não tem conta — pra ele quem manda é a Prime.
    Mandar o layout de sistema seria apresentar o fornecedor errado."""
    html, _ = pe.montar(mensagem="Oi", link="http://x/p/a", numero=1,
                        empresa="Prime Eventos", telefone="(86) 9 9999-9999",
                        email_empresa="contato@prime.com", modo="evento")
    assert "Prime Eventos" in html
    assert "conta no Zaq" not in html
    assert ">Zaq<" not in html
    assert "(86) 9 9999-9999" in html and "contato@prime.com" in html


def test_o_resumo_aparece_pra_quem_so_bate_o_olho():
    html, texto = pe.montar(mensagem="Oi", link="http://x/p/a", numero=1, empresa="P",
                            resumo="Casamento · 12/09/2026 · R$ 8.900,00 · gerado em 19/08/2026",
                            modo="evento")
    assert "gerado em 19/08/2026" in html and "gerado em 19/08/2026" in texto


def test_a_mensagem_do_usuario_nao_vira_html():
    """O texto é digitado por gente e vai pra caixa de outra gente. Uma aspa ou um
    < no meio não pode fechar tag nenhuma."""
    html, _ = pe.montar(mensagem='Oi <b>Ana</b> & "cia"', link="http://x/p/a",
                        numero=1, empresa="P")
    assert "<b>Ana</b>" not in html
    assert "&lt;b&gt;Ana&lt;/b&gt;" in html


def test_linha_em_branco_nao_vira_paragrafo_vazio():
    html, _ = pe.montar(mensagem="Um\n\n\nDois", link="http://x/p/a", numero=1, empresa="P")
    assert html.count("<p style=\"margin:0 0 10px\">") == 2


# ------------------------------------------------- por qual caixa o e-mail sai
#
# Os dublês trocam as FUNÇÕES dos módulos reais, não o módulo inteiro em
# sys.modules: `proposta_email` faz `from finance import email_inbound`, que lê o
# atributo do pacote — já resolvido na primeira importação. Trocar o sys.modules
# funcionava rodando o arquivo sozinho e falhava na suíte inteira, que é o pior
# tipo de teste que existe.

def _postar(monkeypatch, *, caixa, ok_empresa=True, ok_zaq=True) -> list:
    """Liga os dublês e devolve a lista do que saiu."""
    from finance import email_inbound as ein
    from finance import email_sender as es
    saiu = []

    def _remetente_conta(pool, conta_id, canal="email"):
        return caixa

    def _enviar_conta(pool, conta_id, destino, assunto, html, texto_alt=None,
                      from_nome=None, **kw):
        if not ok_empresa:
            return False
        saiu.append({"por": "empresa", "from_nome": from_nome})
        return True

    def _enviar_email(destino, assunto, html, texto_alt=None, reply_to=None,
                      from_nome=None, **kw):
        if not ok_zaq:
            return False
        saiu.append({"por": "zaq", "reply_to": reply_to, "from_nome": from_nome})
        return True

    monkeypatch.setattr(ein, "remetente_conta", _remetente_conta)
    monkeypatch.setattr(ein, "enviar_conta", _enviar_conta)
    monkeypatch.setattr(es, "enviar_email", _enviar_email)
    monkeypatch.setattr(es, "remetente_configurado", lambda: "contato@zaq-ia.com")
    return saiu


def test_a_caixa_da_empresa_vem_primeiro(monkeypatch):
    """A regra de fundo. Saindo pelo Zaq, a resposta do cliente morre numa caixa
    que a empresa não lê."""
    saiu = _postar(monkeypatch, caixa="primeeventosthe@gmail.com")
    r = pe.enviar(_Pool(), 34, destino="c@x.com", assunto="A", html="<p>",
                  texto="t", empresa="Prime Eventos", reply_to="dono@prime.com")
    assert r["ok"] and r["remetente"] == "primeeventosthe@gmail.com"
    assert saiu == [{"por": "empresa", "from_nome": "Prime Eventos"}]


def test_sem_caixa_propria_sai_pelo_zaq_com_reply_to_da_empresa(monkeypatch):
    """A reserva. O Reply-To só existe aqui: saindo pela caixa da empresa, a
    resposta já volta pra ela sozinha."""
    saiu = _postar(monkeypatch, caixa=None)
    r = pe.enviar(_Pool(), 34, destino="c@x.com", assunto="A", html="<p>", texto="t",
                  empresa="Prime Eventos", reply_to="dono@prime.com")
    assert r["ok"] and r["remetente"] == "contato@zaq-ia.com"
    assert saiu == [{"por": "zaq", "reply_to": "dono@prime.com",
                     "from_nome": "Prime Eventos"}]


def test_caixa_propria_que_falha_cai_pro_zaq_em_vez_de_desistir(monkeypatch):
    """Senha de app trocada, Gmail fora do ar. O e-mail ainda tem que sair — o
    cliente está esperando o orçamento, não o conserto da caixa."""
    saiu = _postar(monkeypatch, caixa="prime@gmail.com", ok_empresa=False)
    r = pe.enviar(_Pool(), 34, destino="c@x.com", assunto="A", html="<p>", texto="t",
                  empresa="Prime")
    assert r["ok"] and r["remetente"] == "contato@zaq-ia.com"
    assert [x["por"] for x in saiu] == ["zaq"]


def test_falhar_nos_dois_caminhos_devolve_um_motivo_util(monkeypatch):
    _postar(monkeypatch, caixa="prime@gmail.com", ok_empresa=False, ok_zaq=False)
    r = pe.enviar(_Pool(), 34, destino="c@x.com", assunto="A", html="<p>", texto="t",
                  empresa="Prime")
    assert r["ok"] is False
    assert "Canais" in r["erro"]        # diz ONDE consertar, não só "falhou"


def test_o_envio_nunca_levanta(monkeypatch):
    """Best-effort de ponta a ponta: quem chama registra e devolve o link. Uma
    exceção aqui deixaria o vendedor com a tela travada e sem o plano B."""
    from finance import email_inbound as ein
    _postar(monkeypatch, caixa=None)

    def _explode(*a, **kw):
        raise RuntimeError("banco fora")

    monkeypatch.setattr(ein, "remetente_conta", _explode)
    assert pe.enviar(_Pool(), 34, destino="c@x.com", assunto="A", html="<p>",
                     texto="t", empresa="Prime")["ok"] is True


def test_a_tela_sabe_por_onde_vai_sair_ANTES_de_mandar(monkeypatch):
    """O mesmo botão se comporta diferente em duas empresas. Sem dizer, o vendedor
    descobriria pelo cliente reclamando que respondeu e ninguém viu."""
    _postar(monkeypatch, caixa="prime@gmail.com")
    assert pe.remetente(_Pool(), 34, "dono@prime.com") == {
        "caixa": "própria", "endereco": "prime@gmail.com", "reply_to": ""}

    _postar(monkeypatch, caixa=None)
    assert pe.remetente(_Pool(), 34, "dono@prime.com") == {
        "caixa": "zaq", "endereco": "contato@zaq-ia.com", "reply_to": "dono@prime.com"}
