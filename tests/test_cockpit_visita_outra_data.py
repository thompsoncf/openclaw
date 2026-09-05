"""A pílula "Outra data" na tela de Agendar visita (Cockpit).

Reclamação do vendedor em 05/09/2026, com print em mãos: a tela só mostra 14
dias fixos (`for i in range(14)` em `cockpit_visita_marcar`) — hoje + 13. Se o
cliente pede uma visita daqui a três semanas, não existe pílula, campo ou
qualquer jeito de marcar essa data pelo celular.

O motor (`finance.cockpit.agendar_visita`) NUNCA teve limite de data — o teto
de 14 dias sempre foi só da TELA, que oferecia menos do que o backend aceita.
Por isso o conserto é 100% de front: uma pílula a mais, que abre um
`<input type=date>` sem `max`, no lugar dos 14 dias fixos que continuam do
jeito que estavam.
"""
import datetime
import json
import re
import shutil
import subprocess

import pytest

from web import painel_cockpit as pc

_DIA_SEM_JS = ["Dom", "Seg", "Ter", "Qua", "Qui", "Sex", "Sáb"]


def _rotulo_js(d: datetime.date) -> str:
    """O mesmo cálculo que `fmtDia` faz no navegador, em Python — pra comparar
    sem hardcodar qual dia da semana cai em qual data."""
    js_idx = (d.weekday() + 1) % 7   # Python: Seg=0..Dom=6 · JS Date.getDay(): Dom=0..Sáb=6
    return f"{_DIA_SEM_JS[js_idx]} {d.day}/{d.month}"


def _script() -> str:
    return re.findall(r"<script>(.*?)</script>", pc._VISITA_JS, re.S)[0]


_HARNESS = r"""
globalThis.window = globalThis;
var REG = {};
function criaEl(id){
  return {id:id, style:{}, innerHTML:"", textContent:"", value:"", disabled:false, min:"",
    classList:{_s:{},
      add:function(c){this._s[c]=1;},
      remove:function(c){delete this._s[c];},
      toggle:function(c){if(this._s[c]){delete this._s[c];return false;}this._s[c]=1;return true;},
      contains:function(c){return !!this._s[c];}},
    querySelector:function(){return null;}, focus:function(){},
    setAttribute:function(){}, getAttribute:function(){return null;}};
}
function el(id){ if(!REG[id]) REG[id]=criaEl(id); return REG[id]; }
globalThis.document = {
  getElementById: el,
  querySelector: function(){return null;},
  createElement: function(){return criaEl("_tmp");},
  addEventListener: function(){},
};
window.VISITA = __VISITA__;

__SCRIPT__

var saida = {};
saida.antes = document.getElementById("dias").innerHTML;
saida.antesMin = document.getElementById("dia-outro").min;
window.__visita.st.dia = __NOVA__;
window.__visita.pinta();
saida.depois = document.getElementById("dias").innerHTML;
saida.rotulo = window.__visita.fmtDia(__NOVA__);
console.log(JSON.stringify(saida));
"""

_VISITA = {"leadId": 1,
           "dias": [{"iso": "2026-09-05", "lab": "Hoje"},
                    {"iso": "2026-09-06", "lab": "Amanhã"}],
           "horas": ["10:00"], "nome": "Prime Eventos", "quem": "Elsinha", "base": ""}


def _rodar(nova_iso: str) -> dict:
    script = (_HARNESS.replace("__SCRIPT__", _script())
              .replace("__VISITA__", json.dumps(_VISITA))
              .replace("__NOVA__", json.dumps(nova_iso)))
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "visita.mjs"
        f.write_text(script, encoding="utf-8")
        p = subprocess.run(["node", str(f)], capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stdout + p.stderr
    return json.loads(p.stdout.strip().splitlines()[-1])


@pytest.mark.skipif(not shutil.which("node"), reason="node não instalado")
def test_a_pilula_generica_aparece_quando_a_data_esta_nos_14_dias():
    r = _rodar("2026-09-05")   # o próprio primeiro dia da lista fixa
    assert '📅 Outra data' in r["antes"]
    assert 'class="esc outra">' in r["antes"], "não pode nascer marcada (' on')"


@pytest.mark.skipif(not shutil.which("node"), reason="node não instalado")
def test_escolher_uma_data_fora_dos_14_dias_marca_a_pilula_com_o_dia():
    alvo = datetime.date(2026, 10, 6)
    r = _rodar(alvo.isoformat())
    rotulo = _rotulo_js(alvo)
    assert 'class="esc outra on">📅 ' + rotulo + '<' in r["depois"]
    # e as pílulas fixas de sempre continuam lá, nenhuma sumiu
    assert "Hoje" in r["depois"] and "Amanhã" in r["depois"]


@pytest.mark.skipif(not shutil.which("node"), reason="node não instalado")
def test_fmtdia_bate_com_o_calculo_em_python():
    """O ponto mais fácil de errar: `new Date("2026-10-06")` sem hora vira meia-noite
    UTC, e em fuso negativo (BRT) o dia LOCAL ainda seria 05/10 — teria mostrado a
    data errada pro vendedor. `fmtDia` força "T00:00:00" pra evitar isso."""
    for alvo in (datetime.date(2026, 10, 6), datetime.date(2026, 1, 1),
                 datetime.date(2026, 12, 31)):
        r = _rodar(alvo.isoformat())
        assert r["rotulo"] == _rotulo_js(alvo), alvo


@pytest.mark.skipif(not shutil.which("node"), reason="node não instalado")
def test_o_campo_de_data_nao_deixa_marcar_no_passado():
    r = _rodar("2026-09-05")
    assert r["antesMin"] == "2026-09-05", "sem o mínimo, dava pra marcar visita ontem"


def test_a_tela_ainda_oferece_catorze_dias_fixos():
    """Guarda de não-regressão: a pílula nova é um ACRÉSCIMO — os 14 dias de
    sempre (hoje + 13) continuam sendo gerados do mesmo jeito."""
    fonte = open(pc.__file__, encoding="utf-8").read()
    i = fonte.index('def cockpit_visita_marcar')
    corpo = fonte[i:i + 1500]
    assert "for i in range(14)" in corpo


def test_o_campo_de_data_esta_mesmo_na_pagina():
    """Guarda do teste de sintaxe acima: se o input sumir do render, os testes de
    node ficam verdes sem ter olhado o que já quebrou."""
    fonte = open(pc.__file__, encoding="utf-8").read()
    i = fonte.index('def cockpit_visita_marcar')
    corpo = fonte[i:i + 3000]
    assert 'id=dia-outro' in corpo
