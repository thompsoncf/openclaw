"""Desconectar precisa do celular na mão, e ninguém perguntava isso.

O botão Desconectar já avisava sobre CONSEQUÊNCIA: apaga a credencial e as chaves,
e depois de reparear a conta fica "conectada mas sem receber" enquanto as sessões
do Signal se refazem. O que ele nunca perguntou foi sobre PRONTIDÃO — se a pessoa
do celular está por perto AGORA.

E é essa a pergunta que decide se a empresa fica fora do ar. O primeiro código do
lote dura 60 s (Baileys, Socket/socket.js:464) e os seguintes 20 s. Em 26/08 o
dono da conta 23 pareou em 42 s COM O CELULAR JÁ NA MÃO; quem tiver que ir buscar
o aparelho não passa, e o WhatsApp da empresa fica fora até alguém escanear. Foi
o risco corrido nesse mesmo dia: a conta 23 ficou desconectada às 11:17:41 e só
voltou 11:18:37 porque o dono já estava esperando — não porque a tela ajudou.

O que este teste protege:

  * a segunda pergunta EXISTE, nos dois chips (o cartão do chip 2 é cópia à mão);
  * ela vem DEPOIS da primeira. Curta e no fim é o que ainda se lê; enfiada no
    meio do parágrafo longo, sumiria;
  * **cancelar CANCELA** — este é o caso que importa. Uma segunda pergunta que
    não barra é pior que nenhuma: dá a sensação de proteção sem proteger. O teste
    roda o JS de verdade no Node e confere que o fetch NÃO sai;
  * dizer sim nas duas ainda desconecta (a proteção não pode virar bloqueio);
  * o prazo dito ao cliente ("cerca de 1 minuto") continua batendo com o Baileys
    instalado — se um upgrade mudar o padrão, o aviso vira mentira.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from web import painel_prospeccao as pp

_TPL = pp._COMUNICACAO_TPL
RAIZ = Path(__file__).resolve().parent.parent
SOCKET_JS = (RAIZ / "services" / "wa-qr" / "node_modules" / "@whiskeysockets"
             / "baileys" / "lib" / "Socket" / "socket.js")


def _funcao(nome: str) -> str:
    """O corpo de uma função JS do template, por chaves balanceadas (mesmo recorte
    do test_qr_botoes_pela_credencial)."""
    i = _TPL.index("function %s(" % nome)
    prof, j = 0, i
    while True:
        if _TPL[j] == "{":
            prof += 1
        elif _TPL[j] == "}":
            prof -= 1
            if prof == 0:
                return _TPL[i:j + 1]
        j += 1


_DOM = """
var _perguntas = [], _fetches = 0;
// respostas[] diz o que o usuário clica em cada confirm, na ordem
var _respostas = %s, _i = 0;
global.confirm = function(txt){ _perguntas.push(txt); return _respostas[_i++]; };
global.fetch = function(){ _fetches++; return Promise.resolve({json:function(){return Promise.resolve({ok:true});}}); };
var _els = {};
["qr-msg","c2-msg","qr-box","qr-img","qr-sair","qr-btn","c2-box","c2-img","c2-sair","c2-btn","c2-card"]
  .forEach(function(id){ _els[id]={id:id,style:{},classList:{toggle:function(){},remove:function(){}},
                                   dataset:{chip:'36'},textContent:'',src:'',title:'',disabled:false}; });
global.document = { getElementById:function(id){ return _els[id]||null; } };
global.clearInterval=function(){}; global.setInterval=function(){return 0;};
global.clearTimeout=function(){}; global.setTimeout=function(){return 0;};
var _qrTimer=null,_c2Timer=null,_qrEspera=null;
function qrShow(){} function c2Show(){} function c2Chip(){return '36';}
function FormData(){ this.append=function(){}; }
%s
%s();
console.log(JSON.stringify({perguntas:_perguntas, fetches:_fetches}));
"""


def _rodar(fn: str, respostas: list[bool]) -> dict:
    js = _DOM % (json.dumps(respostas), _funcao(fn), fn)
    r = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr[:900]
    return json.loads(r.stdout.strip().splitlines()[-1])


pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="sem node no ambiente")


# ── a pergunta existe e está no lugar certo ──────────────────────────────────
@pytest.mark.parametrize("fn", ["qrSair", "c2Sair"])
def test_pergunta_do_celular_existe(fn):
    r = _rodar(fn, [True, True])
    assert len(r["perguntas"]) == 2, f"{fn}: esperava duas perguntas, veio {len(r['perguntas'])}"
    assert "celular" in r["perguntas"][1].lower()


@pytest.mark.parametrize("fn", ["qrSair", "c2Sair"])
def test_a_primeira_pergunta_e_a_da_consequencia(fn):
    """A ordem importa: a longa (o que se perde) primeiro, a curta (prontidão)
    depois. Curta no fim ainda é lida; no meio da longa, some."""
    r = _rodar(fn, [True, True])
    assert "NÃO é só desconectar" in r["perguntas"][0]
    assert len(r["perguntas"][1]) < len(r["perguntas"][0]), \
        "a segunda pergunta tem que ser mais curta que a primeira"


@pytest.mark.parametrize("fn", ["qrSair", "c2Sair"])
def test_a_pergunta_diz_o_prazo_e_o_caminho(fn):
    """Sem o prazo é só mais um 'tem certeza?'. Com ele, a pessoa decide."""
    p = _rodar(fn, [True, True])["perguntas"][1]
    assert "1 minuto" in p, "o prazo do QR tem que estar na pergunta"
    assert "Aparelhos conectados" in p, "o caminho no celular tem que estar ali"


# ── o caso que importa: cancelar CANCELA ─────────────────────────────────────
@pytest.mark.parametrize("fn", ["qrSair", "c2Sair"])
def test_dizer_nao_no_celular_nao_desconecta(fn):
    """Uma segunda pergunta que não barra é pior que nenhuma — dá sensação de
    proteção sem proteger."""
    r = _rodar(fn, [True, False])
    assert r["fetches"] == 0, f"{fn}: desconectou mesmo com o usuário dizendo NÃO"
    assert len(r["perguntas"]) == 2


@pytest.mark.parametrize("fn", ["qrSair", "c2Sair"])
def test_dizer_nao_na_primeira_nem_chega_a_perguntar_do_celular(fn):
    r = _rodar(fn, [False, True])
    assert r["fetches"] == 0
    assert len(r["perguntas"]) == 1, "não faz sentido perguntar do celular depois de desistir"


@pytest.mark.parametrize("fn", ["qrSair", "c2Sair"])
def test_dizer_sim_nas_duas_desconecta(fn):
    """A proteção não pode virar bloqueio: quem confirmou as duas tem que
    conseguir trocar o número."""
    r = _rodar(fn, [True, True])
    assert r["fetches"] == 1, f"{fn}: confirmou as duas e não desconectou"


# ── o prazo dito ao cliente tem que ser verdade ──────────────────────────────
def test_o_prazo_da_pergunta_bate_com_o_baileys():
    """'cerca de 1 minuto' vem dos 60s do primeiro código. Se um upgrade do
    Baileys mudar isso, o aviso vira mentira e o teste avisa antes do cliente."""
    if not SOCKET_JS.exists():
        pytest.skip("baileys não instalado neste ambiente")
    assert "qrTimeout || 60000" in SOCKET_JS.read_text(encoding="utf-8"), \
        "o primeiro QR não dura mais 60s — o texto 'cerca de 1 minuto' precisa mudar"
