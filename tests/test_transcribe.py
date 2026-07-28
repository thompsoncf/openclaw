"""Transcrição de voz (core/transcribe.py): confere que a dica de vocabulário e
o temperature=0 são enviados, e que há fallback se o provedor não aceitar."""
import types

from core.transcribe import Transcritor, _PROMPT_PADRAO


def _fake_transcritor(create_fn):
    """Monta um Transcritor sem tocar na OpenAI de verdade."""
    t = Transcritor.__new__(Transcritor)
    t.model = "whisper-large-v3"
    t.client = types.SimpleNamespace(
        audio=types.SimpleNamespace(
            transcriptions=types.SimpleNamespace(create=create_fn)))
    return t


def test_manda_dica_e_temperature():
    capt = {}

    def create(**kw):
        capt.update(kw)
        return types.SimpleNamespace(text=" marca reunião no Zaq ")

    t = _fake_transcritor(create)
    out = t.transcrever(b"xxx")
    assert out == "marca reunião no Zaq"                 # faz strip
    assert capt["language"] == "pt"
    assert capt["temperature"] == 0
    assert "Zaq" in capt["prompt"] == (_PROMPT_PADRAO)   # dica padrão com "Zaq"


def test_prompt_customizado_por_argumento():
    capt = {}
    t = _fake_transcritor(lambda **kw: capt.update(kw) or types.SimpleNamespace(text="ok"))
    t.transcrever(b"x", prompt="Zaq, Mailson, Thompson")
    assert capt["prompt"] == "Zaq, Mailson, Thompson"


def test_fallback_quando_provedor_nao_aceita_prompt():
    chamadas = []

    def create(**kw):
        chamadas.append(kw)
        if "prompt" in kw or "temperature" in kw:
            raise TypeError("unexpected keyword")
        return types.SimpleNamespace(text="funcionou")

    t = _fake_transcritor(create)
    assert t.transcrever(b"x") == "funcionou"
    assert len(chamadas) == 2                             # tentou com extras, caiu pro básico
    assert "prompt" not in chamadas[1]
