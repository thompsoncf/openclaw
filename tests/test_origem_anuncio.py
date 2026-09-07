"""O leitor do código de anúncio: o que ele aceita, o que recusa e o que preserva.

Função pura, sem banco — é o único pedaço do carimbo de origem que dá pra fixar
inteiro sem subir Postgres, então ele carrega as regras que mais custam se
quebrarem: não apagar mensagem de cliente e não perder entrada.
"""
from finance import origem_anuncio as oa


class TestAcha:
    def test_codigo_no_fim_da_frase(self):
        cod, txt = oa.extrair("Olá! Quero saber sobre o espaço. [#A3]")
        assert cod == "A3"
        assert txt == "Olá! Quero saber sobre o espaço."

    def test_codigo_no_meio_porque_o_cliente_escreve_por_cima(self):
        # a pessoa digita antes e depois da mensagem pronta do anúncio; se só
        # casasse no fim, esse lead viraria "sem origem" sem motivo nenhum
        cod, txt = oa.extrair("oi, vi o anuncio [#FORM-SET-01] qual o valor?")
        assert cod == "FORM-SET-01"
        assert txt == "oi, vi o anuncio qual o valor?"

    def test_codigo_no_comeco(self):
        cod, txt = oa.extrair("[#BIO] vim pelo instagram")
        assert (cod, txt) == ("BIO", "vim pelo instagram")

    def test_maiusculas_para_nao_virar_duas_linhas_no_painel(self):
        assert oa.extrair("oi [#a3]")[0] == "A3"
        assert oa.extrair("oi [#A3]")[0] == "A3"

    def test_ponto_hifen_e_sublinhado_passam(self):
        for cod in ("FORM-SET-01", "v2.video", "estatico_03"):
            assert oa.extrair(f"oi [#{cod}]")[0] == cod.upper()

    def test_so_o_primeiro_conta(self):
        cod, txt = oa.extrair("oi [#A3] tudo bem [#B4]")
        assert cod == "A3"
        # o segundo continua no texto: só saiu o que foi consumido
        assert "[#B4]" in txt


class TestNaoAcha:
    def test_sem_codigo_devolve_o_texto_intacto(self):
        t = "Boa tarde, queria um orçamento para 120 pessoas"
        assert oa.extrair(t) == (None, t)

    def test_colchete_sem_cerquilha_e_mensagem_de_gente(self):
        # ESTA é a razão de a cerquilha existir: sem ela, o Zaq apagaria da
        # mensagem do cliente coisas que ele escreveu
        for t in ("achei engraçado [risos]", "mandei a [foto] ontem", "[audio]"):
            assert oa.extrair(t) == (None, t)

    def test_colchete_vazio_nao_casa(self):
        assert oa.extrair("oi [#] tudo bem")[0] is None

    def test_codigo_grande_demais_nao_casa(self):
        grande = "X" * (oa.TAMANHO_MAX + 1)
        assert oa.extrair(f"oi [#{grande}]")[0] is None

    def test_no_limite_ainda_casa(self):
        no_limite = "X" * oa.TAMANHO_MAX
        assert oa.extrair(f"oi [#{no_limite}]")[0] == no_limite

    def test_espaco_dentro_nao_casa(self):
        assert oa.extrair("oi [#A 3]")[0] is None

    def test_vazio_e_none(self):
        assert oa.extrair("") == (None, "")
        assert oa.extrair(None) == (None, "")


class TestNaoPerdeMensagem:
    """Mensagem sem texto é descartada no wa-qr. Se limpar esvaziasse o corpo, a
    entrada do cliente sumiria — regra 0. O código é lido, o texto volta como veio."""

    def test_mensagem_so_com_o_codigo_mantem_o_texto(self):
        cod, txt = oa.extrair("[#A3]")
        assert cod == "A3"
        assert txt == "[#A3]"

    def test_codigo_cercado_de_espaco_tambem_mantem(self):
        cod, txt = oa.extrair("   [#A3]   ")
        assert cod == "A3"
        assert txt.strip() != ""

    def test_nunca_devolve_texto_vazio_quando_havia_texto(self):
        for t in ("[#A3]", " [#A3] ", "[#A3]\n"):
            _, txt = oa.extrair(t)
            assert txt.strip(), f"esvaziou {t!r}"


class TestEspacos:
    def test_espaco_duplo_deixado_pela_remocao_e_colapsado(self):
        _, txt = oa.extrair("oi [#A3] tudo")
        assert "  " not in txt

    def test_nao_mexe_em_quebra_de_linha_do_cliente(self):
        _, txt = oa.extrair("bom dia [#A3]\nquero um orçamento")
        assert "\n" in txt
