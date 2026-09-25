"""A DATA ESTÁ VENDIDA? — a régua que o calendário passou a responder.

Regra do dono, dada em 19/09/2026, sobre a visita ao espaço:

    "a visita não implica no dia que o espaço tá locado ou ocupado, porque é só
    pra mostrar"

CONTEXTO. Até aqui o calendário pintava a bolinha por TIPO (pessoal, empresa,
fornecedor). Visita e casamento são os dois "empresa", então eram a mesma
bolinha azul — e a tela não respondia a única pergunta que o vendedor faz o dia
inteiro no WhatsApp. Medido na Prime (conta 34) em 19/09/2026: setembro tinha 28
marcas no calendário e só OITO ocupavam o espaço.

Metade deste arquivo é sobre o que NÃO se pode adivinhar. Os cinco casos que
sobram na Prime estão aqui como teste, com os títulos reais — inclusive o par
que derruba qualquer heurística de texto: "REUNIÃO COM ENGENHEIRA" não ocupa e
"Reunião Política - Bianca - Pedro" ocupa (teve sinal de R$ 750).
"""
from __future__ import annotations



import pytest

from finance import agenda as ag


def _ev(titulo, **kw):
    base = {"titulo": titulo, "tipo": "empresa", "status": "ativo"}
    base.update(kw)
    return base


# ------------------------------------------------------------- a visita

def test_visita_nao_ocupa_o_espaco():
    """O caso que deu origem a tudo: o cliente vem conhecer e o dia fica à venda."""
    assert ag.estado_da_data(_ev("Visita — Shirley Cristina")) == ag.LIVRE


def test_a_regua_de_ocupar_e_mais_estreita_que_a_de_contar_visita():
    """Até 24/09/2026 este teste fixava que esta régua era a MESMA de
    `web/painel_relatorios._E_VISITA` — "se alguém mudar uma, a outra grite". Ela
    gritou: a de CONTAR visita foi pra `finance.visita` e ficou mais larga (vale
    também o compromisso ligado a um card, sem olhar o título), porque o Raio-X e o
    Relatório davam números diferentes pra mesma semana.

    A de OCUPAR a data ficou estreita de propósito, e o que se fixa agora é a
    relação entre as duas:
      - toda visita pelo título é visita pras duas;
      - o contrário não: uma festa digitada sem tipo e ligada ao card do cliente
        CONTA como visita (é o que a régua de contar decidiu), mas NÃO libera o
        sábado — liberar errado vende a mesma data duas vezes.
    """
    from finance import visita as vis
    from web import painel_relatorios as pr
    assert pr._E_VISITA == vis.sql_e_visita("e", festa=True)
    for titulo, tipo_evento in (("Visita — Shirley", None), ("VISITA TÉCNICA - PEDRO", None),
                                ("Visita — Ana", "Casamento"), ("Aniversário", None),
                                ("Reunião com a engenheira", None)):
        if ag.eh_visita(titulo=titulo, tipo_evento=tipo_evento):
            assert vis.eh_visita(titulo=titulo, tipo_evento=tipo_evento), titulo
            assert vis.eh_visita(titulo=titulo, tipo_evento=tipo_evento, festa=True), titulo
    # quem NÃO vende festa conta o compromisso ligado ao card; quem vende, só pelo
    # título — e a ocupação da data nunca o libera, em nicho nenhum
    assert vis.eh_visita(titulo="ANIVERSÁRIO", prospeccao_id=7)
    assert not vis.eh_visita(titulo="ANIVERSÁRIO", prospeccao_id=7, festa=True)
    assert not ag.eh_visita(titulo="ANIVERSÁRIO")
    assert ag.estado_da_data(_ev("ANIVERSÁRIO", prospeccao_id=7)) != ag.LIVRE


def test_a_equipe_tambem_batiza_visita_na_mao():
    """O Cockpit escreve "Visita — {quem}"; a equipe digita "VISITA TÉCNICA"."""
    assert ag.eh_visita(titulo="VISITA TÉCNICA - PEDRO") is True
    assert ag.eh_visita(titulo="visita da Laís") is True


def test_tipo_de_festa_preenchido_desempata_contra_a_visita():
    """Quando o tipo de festa vem preenchido, o compromisso é a FESTA do cliente
    — mesmo que alguém tenha escrito "Visita" no título."""
    assert ag.eh_visita(titulo="Visita — Ana", tipo_evento="Casamento") is False
    assert ag.estado_da_data(
        _ev("Visita — Ana", tipo_evento="Casamento")) == ag.OCUPA


# ------------------------------------------------------- o que ocupa mesmo

def test_tipo_de_festa_escolhido_ocupa():
    assert ag.estado_da_data(
        _ev("Casamento — Eva da Silva Ferreira", tipo_evento="Casamento")) == ag.OCUPA


def test_orcamento_vinculado_ocupa_mesmo_sem_tipo_de_festa():
    """Os 8 compromissos da Prime que só têm o orçamento. Sem esta linha eles
    virariam oito perguntas no lugar de oito datas vendidas."""
    assert ag.estado_da_data(
        _ev("Locação — Nágera Raquel", orcamento_id=17)) == ag.OCUPA


def test_pre_reserva_e_estado_proprio():
    """Segurado ocupa HOJE e pode cair amanhã — não é um sim nem um não."""
    assert ag.estado_da_data(
        _ev("Casamento — Denise", status=ag.PRE_RESERVADO)) == ag.SEGURADO


def test_pre_reserva_vence_o_tipo_de_festa():
    """A ordem importa: uma festa segurada é SEGURADA, não vendida."""
    assert ag.estado_da_data(
        _ev("Casamento", status=ag.PRE_RESERVADO, tipo_evento="Casamento")) == ag.SEGURADO


# ------------------------------------------------- a agenda interna da casa

@pytest.mark.parametrize("tipo", ["pessoal", "fornecedor"])
def test_agenda_interna_nunca_ocupa(tipo):
    """Reunião com o contador não impede vender o sábado."""
    assert ag.estado_da_data(_ev("Ligar para Edlla", tipo=tipo)) == ag.LIVRE


def test_agenda_interna_vence_ate_o_tipo_de_festa():
    """Aniversário do sócio é agenda, não venda de data."""
    assert ag.estado_da_data(
        _ev("Aniversário do Manoel", tipo="pessoal", tipo_evento="Aniversário")) == ag.LIVRE


# ------------------------------------------- o que NÃO se pode adivinhar

def test_sem_sinal_nenhum_a_tela_pergunta_em_vez_de_chutar():
    """Os cinco da Prime. É o motivo de `A_CONFERIR` existir."""
    for titulo in ("REUNIÃO COM ENGENHEIRA", "RESERVA MANOEL NETO",
                   "aniversario Leda Lopes", "Reunião Política - Bianca - Pedro"):
        assert ag.estado_da_data(_ev(titulo)) == ag.A_CONFERIR, titulo


def test_o_par_que_derruba_qualquer_palpite_por_titulo():
    """A mesma palavra, respostas opostas — medido em produção em 19/09/2026.

    "REUNIÃO COM ENGENHEIRA" não ocupa. "Reunião Política - Bianca - Pedro"
    ocupa: teve sinal de R$ 750. Adivinhar pelo título marcaria a reunião com a
    engenheira como data vendida, e o vendedor perderia aquele dia achando que já
    era de alguém.

    Os dois caem em A_CONFERIR de propósito: o sistema admite que não sabe.
    """
    a = _ev("REUNIÃO COM ENGENHEIRA")
    b = _ev("Reunião Política - Bianca - Pedro")
    assert ag.estado_da_data(a) == ag.estado_da_data(b) == ag.A_CONFERIR


def test_a_resposta_do_dono_vence_a_derivacao():
    """`ocupa_espaco` é a palavra final. A coluna ainda não existe — ela chega com
    a tela de responder —, mas o gancho é testado agora pra que, quando nascer,
    nada nesta função precise mudar."""
    assert ag.estado_da_data(_ev("REUNIÃO COM ENGENHEIRA", ocupa_espaco=False)) == ag.LIVRE
    assert ag.estado_da_data(_ev("REUNIÃO COM ENGENHEIRA", ocupa_espaco=True)) == ag.OCUPA
    # e ela vence até a visita
    assert ag.estado_da_data(_ev("Visita — Ana", ocupa_espaco=True)) == ag.OCUPA


def test_evento_vazio_cai_no_padrao_do_banco_e_nao_explode():
    """Sem `tipo`, vale "pessoal" — o MESMO padrão de `_fmt_evento`, que é quem
    monta o dicionário na vida real. Agenda interna não ocupa, então LIVRE.

    Errar pro lado de livre aqui é de propósito: um dicionário vazio é bug de
    quem chama, e marcar data vendida por causa de bug tiraria um sábado do ar."""
    assert ag.estado_da_data({}) == ag.LIVRE
    assert ag.estado_da_data(None) == ag.LIVRE


# ------------------------------------------------------------ o DIA inteiro

def test_o_pior_estado_do_dia_manda():
    """Um dia com uma festa e três visitas está OCUPADO. É o que decide se dá
    pra vender — e é por isso que o dia 19 da Prime (três visitas de manhã e a
    locação às 20:30) não pode aparecer como livre."""
    dia = [_ev("Visita — Shirley"), _ev("Visita — Andressa"),
           _ev("Locação — Nágera", orcamento_id=17)]
    assert ag.estado_do_dia(dia) == ag.OCUPA


def test_dia_so_de_visita_continua_livre():
    """O caso que o dono descreveu: as visitas não impedem vender o dia."""
    assert ag.estado_do_dia([_ev("Visita — A"), _ev("Visita — B")]) == ag.LIVRE


def test_a_conferir_fica_acima_de_livre():
    """Não dá pra prometer o que não se sabe."""
    assert ag.estado_do_dia([_ev("Visita — A"), _ev("REUNIÃO COM ENGENHEIRA")]) == ag.A_CONFERIR


def test_ocupa_fica_acima_de_a_conferir():
    """O que já é certeza manda sobre a dúvida."""
    dia = [_ev("REUNIÃO COM ENGENHEIRA"), _ev("Casamento", tipo_evento="Casamento")]
    assert ag.estado_do_dia(dia) == ag.OCUPA


def test_dia_sem_compromisso_nenhum_e_livre():
    assert ag.estado_do_dia([]) == ag.LIVRE
    assert ag.estado_do_dia(None) == ag.LIVRE
