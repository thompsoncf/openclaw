"""A v2 da mensagem — e a atribuição sem a qual testar mensagem não significa nada.

O diagnóstico que gerou a v2 (conta 3, ago/2026): das 166 entregues, 80 foram lidas
e 26 pessoas apertaram um botão — 21 em "Agora não" contra 5 positivas. A mensagem
é lida e RECUSADA 4 pra 1, com taxa de leitura parecida em todos os segmentos. Não é
audiência nem atenção: é o pedido. A v1 pede reunião; a v2 pede uma palavra.

E antes disso vem o que impedia qualquer aprendizado: as duas rotas que criam
campanha semeavam `_PASSOS_PADRAO` sem gravar `modelo_codigo`, então 7 das 8
campanhas não sabiam dizer que mensagem estavam usando.
"""
import pytest

from web import painel_prospeccao as pp

_V2 = [m for m in pp._MODELOS_BASE if m["codigo"].endswith("_v2")]


def test_existe_v2_pros_nichos_que_a_conta_usa():
    """Salão/estética somam 83 leads nesta base e não tinham modelo nenhum —
    caíam no genérico."""
    cods = {m["codigo"] for m in _V2}
    assert {"generico_v2", "beleza_v2", "clinica_v2", "petshop_v2"} <= cods


def test_a_v1_continua_existindo():
    """A v1 é o grupo de controle. Apagar ela apaga a base de comparação."""
    cods = {m["codigo"] for m in pp._MODELOS_BASE}
    assert {"generico", "petshop", "clinica"} <= cods


@pytest.mark.parametrize("m", _V2, ids=lambda m: m["codigo"])
def test_d0_da_v2_tem_texto_fixo(m):
    """A v1 usa IA no D0, que escreve diferente pra cada lead. Mensagem que muda a
    cada envio não é testável — não se mede o que não se repete."""
    d0 = m["passos"][0]
    assert d0["ia"] is False
    assert len(d0["corpo"]) > 120


@pytest.mark.parametrize("m", _V2, ids=lambda m: m["codigo"])
def test_v2_pede_uma_palavra_e_nao_uma_reuniao(m):
    """O que a v2 muda é o PEDIDO. Se voltar a pedir reunião no primeiro toque, ela
    virou a v1 com outra roupa e o teste não compara nada."""
    d0 = m["passos"][0]["corpo"].lower()
    assert "já resolvi" in d0                    # resposta de uma palavra
    assert "?" in d0                             # é uma pergunta, não um pitch
    assert "reunião" not in d0
    assert "2 minutinhos" not in d0              # o pedido da v1


@pytest.mark.parametrize("m", _V2, ids=lambda m: m["codigo"])
def test_v2_da_saida_explicita(m):
    """Permissão de recusar é o ponto: parte dos 21 'Agora não' pode estar recusando
    o compromisso, não o produto."""
    assert "paro por aqui" in m["passos"][0]["corpo"]
    assert "pode ignorar" in m["passos"][1]["corpo"]


@pytest.mark.parametrize("m", _V2, ids=lambda m: m["codigo"])
def test_v2_mantem_a_cadencia_de_tres(m):
    assert [p["dias"] for p in m["passos"]] == [0, 3, 7]


@pytest.mark.parametrize("m", _V2, ids=lambda m: m["codigo"])
def test_placeholder_de_empresa_e_o_que_o_motor_preenche(m):
    """`_fmt` do motor troca {empresa}; um {Empresa} ou {nome} sairia literal na
    mensagem que o lead recebe."""
    from finance.campanhas_motor import _fmt
    for p in m["passos"]:
        texto = p["assunto"] + p["corpo"]
        rendido = _fmt(texto, {"empresa": "Salão X", "cidade": "Teresina"})
        assert "{" not in rendido, p["assunto"]


def test_codigos_nao_colidem():
    cods = [m["codigo"] for m in pp._MODELOS_BASE]
    assert len(cods) == len(set(cods))
    assert pp._MODELOS_BASE_COD == set(cods)


# ------------------------------------------------- a atribuição que faltava

def test_passos_padrao_sao_o_modelo_generico():
    """A migração 170 e a marcação na criação dependem disso: se _PASSOS_PADRAO
    deixar de ser o 'generico', marcar a campanha como 'generico' vira mentira."""
    generico = next(m for m in pp._MODELOS_BASE if m["codigo"] == "generico")
    assert [p["assunto"] for p in generico["passos"]] == [a for (_, _, a, _, _) in pp._PASSOS_PADRAO]
    assert [p["ia"] for p in generico["passos"]] == [ia for (_, _, _, _, ia) in pp._PASSOS_PADRAO]


def test_as_duas_rotas_que_criam_campanha_gravam_o_modelo():
    """Sem `modelo_codigo` na criação, o resultado da campanha não tem causa — foi
    o que deixou 7 das 8 campanhas desta base órfãs. Lê o arquivo porque o defeito
    era exatamente uma linha de INSERT que não citava a coluna."""
    from pathlib import Path
    fonte = (Path(__file__).resolve().parent.parent / "web" / "painel_prospeccao.py").read_text("utf-8")
    inserts = [t for t in fonte.split("insert into campanhas (")[1:]]
    assert inserts, "não achei nenhum insert em campanhas"
    for trecho in inserts:
        cabeca = trecho[:120]
        assert "modelo_codigo" in cabeca, f"insert sem modelo_codigo: {cabeca[:70]}"
