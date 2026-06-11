"""Memoria de conversa do agente.

Por enquanto guarda o historico da conversa na sessao (em memoria).
A memoria de longo prazo "de verdade" de cada dominio mora nos dados
dele - no caso do financeiro, e' o proprio livro-caixa.
"""


class MemoriaConversa:
    def __init__(self):
        self._mensagens = []

    def adicionar(self, papel: str, conteudo):
        self._mensagens.append({"role": papel, "content": conteudo})

    def mensagens(self) -> list:
        return self._mensagens

    def limpar(self):
        self._mensagens = []
