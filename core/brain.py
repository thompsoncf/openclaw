"""Cerebro dos agentes: conexao com o Claude API (Anthropic).

Toda a "inteligencia" passa por aqui. Trocar de modelo, ajustar limites
ou mudar de provedor no futuro e' so' mexer neste arquivo.
"""
import os


class Brain:
    def __init__(self, model: str = "claude-sonnet-4-6", api_key: str | None = None,
                 max_tokens: int = 1024):
        # Import tardio pra biblioteca nao ser obrigatoria so' pra rodar os testes do livro-caixa.
        from anthropic import Anthropic
        self.client = Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))
        self.model = model
        self.max_tokens = max_tokens

    def chamar(self, system: str, mensagens: list, ferramentas: list | None = None):
        """Faz uma chamada ao modelo. Retorna o objeto de resposta da SDK."""
        return self.client.messages.create(
            model=self.model,
            system=system,
            messages=mensagens,
            tools=ferramentas or [],
            max_tokens=self.max_tokens,
        )
