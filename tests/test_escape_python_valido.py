"""Nenhum .py da base pode ter sequência de escape inválida.

POR QUE ESTE TESTE EXISTE. Em 09/09/2026 o log do `openclaw-web` no Render subia,
a cada worker, com:

    web/portal.py:3799: SyntaxWarning: invalid escape sequence '\\B'

A causa é a mesma armadilha estrutural do `test_painel_js_sintaxe`: os templates
são strings Python COMUNS (sem `r`), e uma regex JavaScript escrita nelas leva
barra invertida — `/\\B(?=(\\d{3})+(?!\\d),)/`, `/\\./`. Como `\\B`, `\\d` e `\\.`
não são escapes que o Python conheça, ele PRESERVA a barra: o JS servido sai
correto e a tela funciona. Por isso passou despercebido — o sintoma é só o aviso.

O que torna isso dívida e não curiosidade: no 3.11 é `DeprecationWarning`, no 3.12
(o que o Render roda) virou `SyntaxWarning`, e a versão seguinte transforma em
`SyntaxError`. No dia em que virar erro, o arquivo inteiro para de importar — o
painel não sobe.

Alguém já sabia: a `brl()` da barra de liberação em lote, no MESMO arquivo, tem o
comentário explicando que as barras vão dobradas de propósito. Saber não bastou —
duas linhas escaparam assim mesmo. Por isso a conferência virou teste.

Não depende de banco nem de `node`: só compila o que já está no repositório.
"""
from __future__ import annotations

import pathlib
import warnings

import pytest

_RAIZ = pathlib.Path(__file__).resolve().parent.parent
# diretórios que não são código nosso (ou nem chegam ao servidor)
_IGNORAR = {".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache"}


def _fontes() -> list[pathlib.Path]:
    return sorted(
        p for p in _RAIZ.rglob("*.py")
        if not _IGNORAR & set(p.relative_to(_RAIZ).parts)
    )


def _escapes_invalidos(caminho: pathlib.Path) -> list[str]:
    """Compila o arquivo e devolve os avisos de escape, com linha e mensagem.

    A categoria mudou de nome entre versões (`DeprecationWarning` no 3.11,
    `SyntaxWarning` no 3.12), então o filtro é pela MENSAGEM — que é a mesma nas
    duas: "invalid escape sequence '\\X'".
    """
    fonte = caminho.read_text(encoding="utf-8")
    with warnings.catch_warnings(record=True) as capturados:
        warnings.simplefilter("always")
        try:
            compile(fonte, str(caminho), "exec")
        except SyntaxError:
            return []          # arquivo que já não compila é problema de outro teste
        return [f"linha {w.lineno}: {w.message}"
                for w in capturados if "invalid escape sequence" in str(w.message)]


def test_nenhum_arquivo_tem_escape_invalido():
    """A varredura inteira, num só assert: a lista de culpados é a mensagem."""
    culpados = []
    for p in _fontes():
        for aviso in _escapes_invalidos(p):
            culpados.append(f"{p.relative_to(_RAIZ)} — {aviso}")
    assert not culpados, (
        "sequência de escape inválida (vira SyntaxError em Python futuro; "
        "dobre a barra, como em portal.py `brl()`):\n  " + "\n  ".join(culpados))


def test_o_teste_pega_o_caso_do_incidente(tmp_path):
    """Guarda o próprio detector: sem isto, um teste que nunca acusa nada passa
    para sempre e ninguém descobre que ele parou de olhar."""
    alvo = tmp_path / "regex_em_string.py"
    alvo.write_text('_TPL = """<script>x.replace(/\\B(?=(\\d{3}))/g, ".")</script>"""\n')
    assert _escapes_invalidos(alvo), "o detector deixou passar o caso do incidente"


def test_a_forma_certa_passa(tmp_path):
    """E o conserto — barra dobrada — não pode ser acusado."""
    alvo = tmp_path / "regex_escapada.py"
    alvo.write_text('_TPL = """<script>x.replace(/\\\\B(?=(\\\\d{3}))/g, ".")</script>"""\n')
    assert _escapes_invalidos(alvo) == []


def test_varredura_alcanca_o_arquivo_do_incidente():
    """A lista de arquivos tem que incluir os grandes — se o rglob ou o _IGNORAR
    mudar e o portal sair da varredura, o teste acima vira decoração."""
    achados = {p.relative_to(_RAIZ).as_posix() for p in _fontes()}
    for esperado in ("web/portal.py", "web/painel_prospeccao.py"):
        assert esperado in achados, f"{esperado} ficou de fora da varredura"


def test_pycache_fica_de_fora():
    """`.pyc` não é fonte, e o rglob de `*.py` não pode arrastar diretório de cache."""
    assert not [p for p in _fontes() if "__pycache__" in p.parts]
