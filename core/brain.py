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
                 max_tokens: int | None = None, usar_cache: bool = True):
        # Import tardio pra biblioteca nao ser obrigatoria so' pra rodar os testes do livro-caixa.
        from anthropic import Anthropic
        self.client = Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))
        self.model = model
        # Teto de tokens de SAIDA por chamada. Subimos de 4096 pra 8192 porque
        # cupom grande (dezenas de itens) estourava 4096 no meio do tool_use e a
        # resposta vinha CORTADA (stop_reason=max_tokens) - o cupom "sumia". Com
        # 8192 cabe bem mais item por lote; alem disso a persona agora salva em
        # lotes (anexar=true), entao mesmo cupom gigante nao depende so' do teto.
        # So' paga pelos tokens REALMENTE gerados, entao o teto maior nao encarece
        # as respostas curtas. Configuravel por env.
        if max_tokens is None:
            try:
                max_tokens = int(os.environ.get("MAX_TOKENS_SAIDA", "8192"))
            except (TypeError, ValueError):
                max_tokens = 8192
        self.max_tokens = max_tokens
        self.usar_cache = usar_cache
        self.ultimo_uso = None   # guarda o usage da ultima chamada (pra medir economia)

    def chamar(self, system: str, mensagens: list, ferramentas: list | None = None,
               model: str | None = None):
        """Faz uma chamada ao modelo. Retorna o objeto de resposta da SDK.

        model: se informado, sobrescreve self.model SO' nesta chamada (ex: usar
        Haiku no texto e Sonnet na foto). Se None, usa self.model (padrao).
        """
        ferramentas = ferramentas or []
        _model = model or self.model
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
            mensagens = self._marcar_cache_imagem(mensagens)
            resp = self.client.messages.create(
                model=_model,
                system=system_blocos,
                messages=mensagens,
                tools=tools_cache,
                max_tokens=self.max_tokens,
            )
        else:
            resp = self.client.messages.create(
                model=_model,
                system=system,
                messages=mensagens,
                tools=ferramentas,
                max_tokens=self.max_tokens,
            )
        # guarda o usage pra quem quiser medir (input, cache_read, cache_creation, output)
        self.ultimo_uso = getattr(resp, "usage", None)
        return resp

    @staticmethod
    def _marcar_cache_imagem(mensagens: list) -> list:
        """Poe cache_control no ULTIMO bloco da mensagem que tem imagem/PDF.
        A foto do cupom (token MAIS caro) entra no cache, entao as rodadas
        seguintes do loop de ferramentas leem ela a ~10% do preco em vez de
        reprocessar 100% a cada chamada. Sem imagem, devolve intacto.
        Nao muta a memoria original (trabalha em copias)."""
        idx = None
        for i, m in enumerate(mensagens):
            c = m.get("content")
            if isinstance(c, list) and any(
                isinstance(b, dict) and b.get("type") in ("image", "document")
                for b in c):
                idx = i      # a ULTIMA mensagem com imagem (o cupom atual)
        if idx is None:
            return mensagens
        msgs = list(mensagens)
        m = dict(msgs[idx])
        blocos = [dict(b) if isinstance(b, dict) else b for b in m["content"]]
        if isinstance(blocos[-1], dict):
            blocos[-1] = {**blocos[-1], "cache_control": {"type": "ephemeral"}}
        m["content"] = blocos
        msgs[idx] = m
        return msgs

    @staticmethod
    def _tools_com_cache(ferramentas: list) -> list:
        """Coloca cache_control no ULTIMO schema de ferramenta (marca o fim do
        bloco de tools no prefixo). Nao muda nada se a lista estiver vazia."""
        if not ferramentas:
            return ferramentas
        tools = [dict(t) for t in ferramentas]    # copia rasa, nao muta o original
        tools[-1] = {**tools[-1], "cache_control": {"type": "ephemeral"}}
        return tools
