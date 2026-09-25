"""As regras puras do "voltar a chamar" (finance/voltar_a_chamar.py) — sem banco.

O relógio dos toques, o texto, o validador de saúde e as três expressões (preço
de consulta, "marcou", "sair"). O motor com banco está em test_voltar_a_chamar.py.
"""
from datetime import datetime, time, timedelta, timezone

from finance import raio_x_perfil as rxp
from finance import voltar_a_chamar as vac

# a janela padrão do perfil clínica: seg–sex, 08–19
JANELA = {"janela_dias": "1,2,3,4,5", "janela_abre": time(8, 0), "janela_fecha": time(19, 0)}
TOQUES = rxp.voltar_padrao("clinica")["toques_min"]


def _br(dia, h, m=0):
    """Hora de Brasília (-3) → UTC. Setembro de 2026: 21 e 28 são segunda, 25 é sexta."""
    return datetime(2026, 9, dia, h, m, tzinfo=timezone.utc) + timedelta(hours=3)


def _loc(quando):
    return (quando - timedelta(hours=3)).replace(tzinfo=None)


def test_sequencia_de_3h_1_dia_3_dias_7_dias():
    ag = vac.agenda_de_toques(_br(21, 10), JANELA, TOQUES)   # segunda 10h
    assert [(t, _loc(d)) for t, d in ag] == [
        (1, datetime(2026, 9, 21, 13, 0)),
        (2, datetime(2026, 9, 22, 10, 0)),
        (3, datetime(2026, 9, 24, 10, 0)),
        (4, datetime(2026, 9, 28, 10, 0)),
    ]


def test_o_de_3h_depois_das_19h_e_pulado():
    ag = vac.agenda_de_toques(_br(21, 17), JANELA, TOQUES)   # 17h + 3h = 20h
    assert [t for t, _ in ag] == [2, 3, 4]


def test_sabado_e_domingo_passam_pra_segunda_as_8h():
    ag = dict(vac.agenda_de_toques(_br(25, 10), JANELA, TOQUES))   # sexta 10h
    assert _loc(ag[1]) == datetime(2026, 9, 25, 13, 0)
    assert _loc(ag[2]) == datetime(2026, 9, 28, 8, 0)     # sábado → segunda 08h


def test_preco_no_sabado_nao_tem_o_de_3h():
    ag = vac.agenda_de_toques(_br(26, 10), JANELA, TOQUES)
    assert 1 not in dict(ag)


def test_nunca_dois_toques_no_mesmo_dia():
    for dia in (21, 25, 26, 27):
        for h in (0, 7, 10, 18, 23):
            ag = vac.agenda_de_toques(_br(dia, h), JANELA, TOQUES)
            dias = [_loc(d).date() for _, d in ag]
            assert len(dias) == len(set(dias)), (dia, h, ag)
            for _, d in ag:
                loc = _loc(d)
                assert loc.isoweekday() <= 5 and time(8) <= loc.time() < time(19)


def test_texto_usa_so_o_primeiro_nome():
    modelo = rxp.voltar_padrao("clinica")["textos"]["1"]
    assert vac.texto_do_toque(modelo, "maria clara souza").startswith("Oi, Maria!")
    assert vac.texto_do_toque(modelo, "~Ana 🌸").startswith("Oi, Ana!")
    # número no lugar do nome: melhor sem nome do que "Oi, 5586...!"
    assert vac.texto_do_toque(modelo, "5586999990000").startswith("Oi! ")
    assert vac.texto_do_toque(modelo, None).startswith("Oi! ")


def test_validador_de_saude_recusa():
    for frase in ("Oi! Ainda quer tratar a acne?", "Temos laser amanhã",
                  "Oi, quer marcar o botox?", "Sua mancha tem jeito", "O câncer de pele..."):
        assert vac.tem_palavra_de_saude(frase), frase
    assert vac.validar_texto("Oi, {nome}! Ainda quer tratar a acne?")
    assert vac.validar_texto("Oi, {nome}! A consulta custa R$ 500")     # preço não
    assert vac.validar_texto("") is not None
    assert vac.validar_texto("Oi, {nome}! Quer que eu veja um horário?") is None


def test_os_textos_padrao_passam_no_proprio_validador():
    for k, t in rxp.voltar_padrao("clinica")["textos"].items():
        assert vac.validar_texto(t) is None, k
        # e nenhum deles vira fato de preço se voltar pela conversa
        assert not vac.eh_preco_de_consulta(t, r"(consulta|avalia)"), k


def test_o_fato_de_preco_de_consulta():
    ctx = rxp.voltar_padrao("clinica")["re_contexto"]
    assert vac.eh_preco_de_consulta("O investimento de R$ 500,00 da consulta com o Dr.", ctx)
    assert vac.eh_preco_de_consulta("A avaliação fica 500 reais", ctx)
    assert not vac.eh_preco_de_consulta("Segue o boleto de R$ 1.200", ctx)
    assert not vac.eh_preco_de_consulta("Sua consulta está confirmada para amanhã", ctx)


def test_marcou_e_sair():
    assert vac.marcou("Prontinho!! Seu agendamento está feito")
    assert vac.marcou("O seu agendamento foi realizado com sucesso")
    assert not vac.marcou("Quer que eu veja um horário?")
    assert vac.quer_sair("SAIR")
    assert vac.quer_sair("  não quero, obrigada")
    assert vac.quer_sair("Parar")
    assert not vac.quer_sair("quero sair da lista de espera e marcar")  # não começa com sair
    assert not vac.quer_sair("pareceu caro")
    # a mensagem inteira tem que ser o pedido de saída (revisão do #843)
    assert not vac.quer_sair("Não quero esperar muito, tem horário essa semana?")
    assert not vac.quer_sair("Sair do trabalho às 18h, tem 19h?")
    assert vac.quer_sair("Não quero mais.")


def test_so_a_clinica_tem_o_padrao():
    assert rxp.voltar_padrao("clinica")
    for outro in ("eventos", "recorrente", "seguros", "produto", "xpto"):
        assert rxp.voltar_padrao(outro) is None
