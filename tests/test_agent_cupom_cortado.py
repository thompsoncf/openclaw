"""Resposta CORTADA por max_tokens no meio de um tool_use (cupom gigante).

Quando o cupom e' tao grande que o tool_use de registrar_itens_cupom nao cabe no
teto de tokens, a resposta volta com stop_reason='max_tokens' e um tool_use
INCOMPLETO. O agente NAO pode salvar isso (JSON quebrado) nem empilhar na memoria
(tool_use sem tool_result corromperia a conversa). Ele deve: (a) nao mexer na
memoria, (b) devolver a orientacao pra salvar em PARTES.

Nao toca banco (Brain e livro mockados)."""
from core.agent import Agente
from core.memory import MemoriaConversa


class _Bloco:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _RespCortada:
    """Simula a resposta da SDK cortada no meio do tool_use."""
    stop_reason = "max_tokens"
    usage = None

    def __init__(self):
        self.content = [_Bloco(type="tool_use", id="t1",
                               name="registrar_itens_cupom", input={})]


class _BrainStub:
    def __init__(self, resp):
        self._resp = resp
        self.chamadas = 0

    def chamar(self, system, mensagens, ferramentas=None, model=None):
        self.chamadas += 1
        return self._resp


def _agente():
    return Agente("fin", "persona de teste", ferramentas=[],
                  brain=_BrainStub(_RespCortada()), memoria=MemoriaConversa())


def test_cupom_cortado_orienta_salvar_em_partes():
    ag = _agente()
    resp = ag.responder("salva os itens do cupom")
    assert "PARTES" in resp
    assert "sem duplicar" in resp
    # nao promete o velho "METADE" (fluxo antigo que nem funcionava)
    assert "METADE" not in resp


def test_cupom_cortado_nao_corrompe_a_memoria():
    ag = _agente()
    ag.responder("salva os itens do cupom")
    msgs = ag.memoria.mensagens()
    # so' a mensagem do usuario ficou; o assistant cortado (tool_use orfao) NAO
    # foi empilhado, senao a proxima chamada quebraria com erro 400.
    assert len(msgs) == 1
    assert msgs[0]["role"] == "user"
