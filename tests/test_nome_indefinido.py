"""Nenhum arquivo de produção usa nome que não existe.

O Python não reclama disso ao importar: `conv_id` usado vinte linhas antes de ser
atribuído passa por qualquer revisão, roda a suíte inteira e só explode quando
aquela linha é executada — em produção, no caminho raro.

Foi exatamente o que aconteceu em 23/08, no RSVP de convite (webhook do Twilio):

    _wout.enviar(c, conta_id, remetente, _cv.confirmacao_texto(_conv),
                 chip_id=_wout.chip_da_conversa(c, conta_id, conv_id))
                                                          ^^^^^^^
    conv_id só é atribuído mais abaixo, no inbox.

O convidado tocou "Confirmar Presença". O status foi gravado certo no banco, e aí
esta linha levantou UnboundLocalError: 500 pro Twilio, que reentregou a mensagem —
e na reentrega o convite já não estava mais 'pendente', então o fluxo caiu no
inbox e QUEM RESPONDEU FOI A IA: "Confirmar presença em quê? Não tenho nada
registrado aqui sobre um evento". Para quem confirmou, a empresa pareceu não saber
da própria reunião.

Uma linha de `pyflakes` pega isso em menos de um segundo. Este teste é essa linha,
amarrada na suíte pra valer em todo PR.

Só `undefined name`: importação não usada e companhia são estilo, e travar estilo
num teste vira ruído que alguém desliga.
"""
import subprocess
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent

#: o código que roda pra cliente. `tests/` fica de fora de propósito: lá um nome
#: indefinido quebra o próprio teste, na hora, e ninguém mais paga por isso.
PASTAS = ("web", "finance", "db", "contas")


def _pyflakes(alvos: list[str]) -> list[str]:
    r = subprocess.run([sys.executable, "-m", "pyflakes", *alvos],
                       capture_output=True, text=True, cwd=RAIZ)
    return [l for l in r.stdout.splitlines() if "undefined name" in l]


def test_producao_nao_tem_nome_indefinido():
    pytest.importorskip("pyflakes")
    achados = _pyflakes([str(RAIZ / p) for p in PASTAS])
    assert not achados, (
        "nome usado sem existir — isto só apareceria em produção:\n  "
        + "\n  ".join(achados))


def test_o_teste_pega_o_bug_que_motivou_ele(tmp_path):
    """Não-vacuidade: reproduz o caso do RSVP num arquivo de mentira e exige que o
    pyflakes acuse. Sem isto, o teste acima passaria verde mesmo se a ferramenta
    parasse de funcionar (versão nova, flag trocada, saída em outro formato)."""
    pytest.importorskip("pyflakes")
    falso = tmp_path / "rota.py"
    falso.write_text(
        "def webhook():\n"
        "    if condicao():\n"
        "        enviar(chip_id=conv_id)\n"      # usado aqui...
        "        return\n"
        "    conv_id, nova = inbox()\n"          # ...atribuído só depois
        "    return conv_id, nova\n",
        encoding="utf-8")
    achados = _pyflakes([str(falso)])
    assert any("conv_id" in a for a in achados), \
        f"o pyflakes deixou de acusar o caso que motivou este teste: {achados}"
