"""Cerebro dos agentes: conexao com o Claude API (Anthropic).

Toda a "inteligencia" passa por aqui. Trocar de modelo, ajustar limites
ou mudar de provedor no futuro e' so' mexer neste arquivo.

CUSTO: usa prompt caching (cache_control ephemeral) no system prompt e nos
schemas das ferramentas. Eles sao IGUAIS a cada chamada do loop de ferramentas,
entao cachear faz as chamadas seguintes lerem o prefixo a ~10% do preco (em vez
de pagar 100% toda vez). Reduz muito o custo de entrada em conversas com varias
iteracoes de ferramenta. O cache e' por prefixo (tools -> system -> mensagens);
como persona e tools sao estaveis, o cache "esquenta" e renova a cada uso (TTL 5m).
"""
import os


class Brain:
    def __init__(self, model: str = "claude-sonnet-4-6", api_key: str | None = None,
                 max_tokens: int = 4096, usar_cache: bool = True):
        # Import tardio pra biblioteca nao ser obrigatoria so' pra rodar os testes do livro-caixa.
        from anthropic import Anthropic
        self.client = Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))
        self.model = model
        self.max_tokens = max_tokens
        self.usar_cache = usar_cache
        self.ultimo_uso = None   # guarda o usage da ultima chamada (pra medir economia)

    def chamar(self, system: str, mensagens: list, ferramentas: list | None = None):
        """Faz uma chamada ao modelo. Retorna o objeto de resposta da SDK."""
        ferramentas = ferramentas or []
        if self.usar_cache:
            # system vira lista de bloco(s) com cache_control no ultimo (marca o
            # fim do prefixo estavel: persona). As ferramentas, sendo parte do
            # prefixo (vem antes do system na ordem de cache), tambem entram no
            # cache automaticamente ate' esse ponto. Marcamos tambem o ultimo
            # schema de ferramenta pra garantir o breakpoint do bloco de tools.
            system_blocos = [{
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},
            }]
            tools_cache = self._tools_com_cache(ferramentas)
            resp = self.client.messages.create(
                model=self.model,
                system=system_blocos,
                messages=mensagens,
                tools=tools_cache,
                max_tokens=self.max_tokens,
            )
        else:
            resp = self.client.messages.create(
                model=self.model,
                system=system,
                messages=mensagens,
                tools=ferramentas,
                max_tokens=self.max_tokens,
            )
        # guarda o usage pra quem quiser medir (input, cache_read, cache_creation, output)
        self.ultimo_uso = getattr(resp, "usage", None)
        return resp

    @staticmethod
    def _tools_com_cache(ferramentas: list) -> list:
        """Coloca cache_control no ULTIMO schema de ferramenta (marca o fim do
        bloco de tools no prefixo). Nao muda nada se a lista estiver vazia."""
        if not ferramentas:
            return ferramentas
        tools = [dict(t) for t in ferramentas]    # copia rasa, nao muta o original
        tools[-1] = {**tools[-1], "cache_control": {"type": "ephemeral"}}
        return tools
