"""A hora do evento: o campo que faltava no app, e a hora que ninguém conferia.

Em 01/09/2026 o vendedor da Prime montou o orçamento de um casamento (15/01/2027,
115 convidados) pelo app e não achou onde pôr a hora de encerramento — o
formulário do cockpit só tinha "Início". Ele improvisou no campo do lado, três
vezes, e os três orçamentos ficaram no banco assim:

    #44  inicio "18:00"       fim ""
    #45  inicio "18:00 e en"  fim ""     <- "18:00 e encerramento…", cortado em 10
    #46  inicio "18:00/23:4"  fim ""     <- "18:00/23:40", cortado em 10

O corte é do `_sanear_evento` (10 caracteres), e é o que aparecia na proposta do
cliente: "INÍCIO 18:00/23:4 · ENCERRAMENTO —".

**E o estrago não era cosmético.** `agenda.janela_evento("15/01/2027",
"18:00/23:4", "")` devolve `(None, None)`: hora que o servidor não entende vale o
mesmo que campo vazio, e a data não fica segurada na agenda. Só que em branco a
tela avisa, e escrita errado ela não avisava nada — parece preenchido.

O que este teste protege:

  * **o app manda o `fim` que o vendedor digitou.** Antes o `coletarEvento` tinha
    `fim:""` fixo no código: o app não conseguia mandar encerramento nenhum, e o
    campo nem existia;
  * **reabrir no app não apaga o encerramento feito no desktop.** O `repor` não
    carregava `ev.fim`, então abrir e salvar mandava vazio por cima de um valor
    que existia — perda de informação do cliente, regra 0;
  * **as duas telas avisam quando a hora não é entendida**, e não só quando está
    vazia. O desktop tinha a mesma cegueira;
  * **a régua do JS bate com a do Python.** São dois `horaOk` em JavaScript (app e
    desktop) espelhando `finance/agenda._minutos`. Se divergirem, a tela aprova o
    que o servidor descarta — que é exatamente como a data se perde calada.
"""
import json
import re
import shutil
import subprocess

import pytest

from finance.agenda import _minutos, janela_evento
from web import painel_cockpit as ck
from web import painel_servicos as sv


# ── o caso, como ele está no banco ───────────────────────────────────────────
@pytest.mark.parametrize("escrito", ["18:00/23:4", "18:00/23:40", "18:00 e en",
                                     "18:00 e encerramento 23:40", "18-23", "18 as 23"])
def test_a_hora_improvisada_nao_segura_a_data(escrito):
    """É o dano concreto: a proposta mostra algo, e a agenda não recebe nada."""
    assert _minutos(escrito) is None
    assert janela_evento("15/01/2027", escrito, "") == (None, None)


def test_a_hora_certa_segura_a_data():
    ini, fim = janela_evento("15/01/2027", "18:00", "23:40")
    assert ini is not None and fim is not None
    assert (ini.hour, ini.minute) == (18, 0)
    assert (fim.hour, fim.minute) == (23, 40)


def test_o_corte_de_dez_caracteres_e_o_que_deformou():
    """Não é o corte que é o bug — hora de verdade cabe em 10 ("22h30" tem 5). O
    corte só transformou um erro de digitação em texto ilegível na proposta."""
    from finance.cockpit import _sanear_evento
    ev = _sanear_evento({"data": "15/01/2027", "inicio": "18:00/23:40", "fim": "",
                         "tipo": "Casamento", "convidados": 115})
    assert ev["inicio"] == "18:00/23:4"
    assert len("22h30") <= 10 and len("24:00") <= 10


# ── o app: o campo, o envio e a reabertura ───────────────────────────────────
def test_o_app_tem_o_campo_de_encerramento():
    """O pedido literal do dono: "vendedor não viu a possibilidade de colocar a
    hora do encerramento"."""
    fonte = open(ck.__file__, encoding="utf-8").read()
    assert "id=evfim placeholder='Encerramento" in fonte, \
        "sem o campo, o vendedor improvisa no campo do lado — foi o que aconteceu"


def test_o_placeholder_ensina_o_formato():
    """"Início (19:00)" em vez de "Início": o exemplo no campo é o que impede o
    vendedor de escrever um intervalo ali."""
    fonte = open(ck.__file__, encoding="utf-8").read()
    assert "Início (19:00)" in fonte
    assert "Encerramento (24:00)" in fonte


def test_o_app_manda_o_fim_digitado():
    """Antes: `fim:""` fixo no código. O app não conseguia mandar encerramento."""
    fonte = open(ck.__file__, encoding="utf-8").read()
    assert 'inicio:v("evini"),fim:v("evfim")' in fonte
    assert 'fim:""' not in fonte, "o fim vazio fixo é o bug"


def test_reabrir_no_app_nao_apaga_o_encerramento():
    """O `repor` não carregava `ev.fim`: abrir no app um orçamento que o desktop
    gravou com encerramento e salvar mandava vazio por cima. Regra 0."""
    fonte = open(ck.__file__, encoding="utf-8").read()
    assert 'p("evfim",ev.fim)' in fonte


def test_o_app_avisa_e_nao_bloqueia():
    """Mesma régua do desktop: às vezes se fecha a proposta com a hora a combinar,
    e travar o botão travaria a venda."""
    fonte = open(ck.__file__, encoding="utf-8").read()
    corpo = fonte[fonte.index("function avisoHora()"):]
    corpo = corpo[:corpo.index("function coletarEvento")]
    assert "disabled" not in corpo, "o aviso não pode travar o botão de gerar"
    assert "não entra na agenda" in corpo
    assert "Não entendi o horário de início" in corpo


# ── o desktop tinha a mesma cegueira ─────────────────────────────────────────
def test_o_desktop_avisa_de_hora_ilegivel_e_nao_so_de_vazia():
    fonte = open(sv.__file__, encoding="utf-8").read()
    corpo = fonte[fonte.index("function pintarSemHora()"):]
    corpo = corpo[:corpo.index("if(SERVICO_AVULSO)")]
    assert "horaOk(ini)" in corpo, "só checar se está vazio deixa passar o caso real"
    assert "horaOk(fim)" in corpo
    assert "'ev-data','ev-ini','ev-fim'" in fonte, \
        "o encerramento também tem que disparar o aviso"


# ── as duas réguas de JS x a do Python ───────────────────────────────────────
_CORPUS = [
    # o que a produção tem hoje, e tem que continuar valendo
    "19", "19:00", "19h", "19h30", "24:00", "24", "22", "01h", "22h30", "17", "20h",
    "02h", "23h", "00:30", "0", "00:00",
    # o improviso do vendedor e vizinhos
    "18:00/23:40", "18:00/23:4", "18:00 e en", "18 as 23", "18-23", "18:00 às 23:40",
    # bordas
    "", "   ", "25", "24:01", "19:60", "-1", "abc", "19:", "19::", ":30", "19:ab",
    "1 9", "19,00", "19.30", "19:00:30", "  19:00  ",
]


def _horaok_js(nome_arquivo, marcador):
    """Extrai o horaOk daquele arquivo e roda no Node contra o corpus."""
    fonte = open(nome_arquivo, encoding="utf-8").read()
    i = fonte.index(marcador)
    corpo = fonte[i:]
    # recorta a função por chaves balanceadas
    ini = corpo.index("{")
    n, fim = 0, None
    for k in range(ini, len(corpo)):
        if corpo[k] == "{":
            n += 1
        elif corpo[k] == "}":
            n -= 1
            if n == 0:
                fim = k + 1
                break
    assert fim, f"não achei o corpo de horaOk em {nome_arquivo}"
    js = corpo[:fim] + "\nconsole.log(JSON.stringify(" + \
        json.dumps(_CORPUS) + ".map(horaOk)));"
    saida = subprocess.run(["node", "-e", js], capture_output=True, text=True)
    assert saida.returncode == 0, saida.stderr
    return json.loads(saida.stdout)


@pytest.mark.parametrize("arquivo,marcador", [
    ("cockpit", "function horaOk(h){"),
    ("servicos", "function horaOk(h){"),
])
def test_o_horaok_do_js_bate_com_o_minutos_do_python(arquivo, marcador):
    """Se a tela aprovar o que o servidor descarta, a data se perde calada — que é
    o modo de falha que este trabalho inteiro veio fechar. E se a tela recusar o
    que o servidor aceita, ela avisa à toa e o vendedor aprende a ignorar o aviso."""
    if not shutil.which("node"):
        pytest.skip("sem node no ambiente")
    caminho = ck.__file__ if arquivo == "cockpit" else sv.__file__
    do_js = _horaok_js(caminho, marcador)
    do_py = [_minutos(s) is not None for s in _CORPUS]
    divergem = [(s, j, p) for s, j, p in zip(_CORPUS, do_js, do_py) if j != p]
    assert not divergem, (
        "JS e Python discordam sobre estas horas (texto, js, python):\n"
        + "\n".join(f"  {s!r}: js={j} python={p}" for s, j, p in divergem))


def test_as_duas_telas_usam_a_mesma_regua():
    """Duas cópias do horaOk é o preço de as telas serem dois arquivos. O que não
    pode é uma evoluir sem a outra."""
    if not shutil.which("node"):
        pytest.skip("sem node no ambiente")
    assert _horaok_js(ck.__file__, "function horaOk(h){") == \
        _horaok_js(sv.__file__, "function horaOk(h){")


# ── a ajuda que separa o que o vendedor escreveu junto ───────────────────────
def _partir_js(texto, fim_atual=""):
    fonte = open(ck.__file__, encoding="utf-8").read()
    i = fonte.index("function horaOk(h){")
    j = fonte.index("function partirHoras(){")
    trecho = fonte[i:j]
    js = trecho + f"""
var _a={json.dumps(texto)}, _b={json.dumps(fim_atual)};
var out=[_a,_b];
if(!_b.trim()){{
  var hs=horasNoTexto(_a);
  if(hs.length===2&&!horaOk(_a)){{out=[hs[0],hs[1]];}}
}}
console.log(JSON.stringify(out));"""
    r = subprocess.run(["node", "-e", js], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


@pytest.mark.parametrize("escrito,esperado", [
    ("18:00/23:40", ["18:00", "23:40"]),
    ("18:00 às 23:40", ["18:00", "23:40"]),
    ("18h às 02h", ["18h", "02h"]),
    ("18:00 e encerramento 23:40", ["18:00", "23:40"]),
])
def test_duas_horas_no_mesmo_campo_viram_dois_campos(escrito, esperado):
    """O improviso do vendedor vira o dado certo — e VISÍVEL: os dois campos ficam
    preenchidos na tela pra ele conferir antes de gerar a proposta. Não é
    adivinhação calada."""
    if not shutil.which("node"):
        pytest.skip("sem node no ambiente")
    assert _partir_js(escrito) == esperado


def test_uma_hora_so_nao_e_partida():
    if not shutil.which("node"):
        pytest.skip("sem node no ambiente")
    assert _partir_js("18:00") == ["18:00", ""]


def test_nao_mexe_se_o_encerramento_ja_tem_valor():
    """O que o vendedor já escreveu no Encerramento manda — a ajuda não sobrescreve
    decisão dele."""
    if not shutil.which("node"):
        pytest.skip("sem node no ambiente")
    assert _partir_js("18:00/23:40", "01h") == ["18:00/23:40", "01h"]


def test_o_que_o_servidor_entende_sobrevive_ao_partir():
    """Depois da ajuda, as duas pontas têm que virar compromisso de verdade."""
    if not shutil.which("node"):
        pytest.skip("sem node no ambiente")
    ini, fim = _partir_js("18:00/23:40")
    a, b = janela_evento("15/01/2027", ini, fim)
    assert a is not None and b is not None
    assert (a.hour, b.hour) == (18, 23)
