"""Memoria de conversa do agente.

Duas implementacoes:
- MemoriaConversa: guarda na RAM (simples; usada nos testes e como fallback).
- MemoriaPersistente: guarda no Postgres, por conversa (sobrevive a deploy,
  e' unica entre instancias e pode ser resetada por SQL).

A memoria de longo prazo "de verdade" de cada dominio mora nos dados dele
- no caso do financeiro, e' o proprio livro-caixa.

Os blocos do assistant vem como objetos da SDK do Claude (com .type, .id,
.text, .name, .input...). Pra salvar no banco a gente serializa pra dict
puro (JSON), que e' exatamente o formato que a API tambem aceita de volta.
"""
import json


def _serializar_bloco(b):
    """Converte um bloco (objeto da SDK OU dict) em dict JSON-serializavel."""
    if isinstance(b, dict):
        return b
    tipo = getattr(b, "type", None)
    if tipo == "text":
        return {"type": "text", "text": getattr(b, "text", "")}
    if tipo == "tool_use":
        return {"type": "tool_use", "id": b.id, "name": b.name, "input": b.input}
    if tipo == "tool_result":
        return {"type": "tool_result", "tool_use_id": b.tool_use_id,
                "content": getattr(b, "content", "")}
    # fallback: tenta atributos comuns
    return {"type": tipo or "text", "text": str(getattr(b, "text", b))}


def _serializar_conteudo(conteudo):
    if isinstance(conteudo, list):
        return [_serializar_bloco(b) for b in conteudo]
    return conteudo   # string simples


class MemoriaConversa:
    """Memoria volatil (RAM). Morre quando o processo reinicia."""

    def __init__(self):
        self._mensagens = []

    def adicionar(self, papel: str, conteudo):
        self._mensagens.append({"role": papel, "content": _serializar_conteudo(conteudo)})

    def mensagens(self) -> list:
        return self._mensagens

    def limpar(self):
        self._mensagens = []


class MemoriaPersistente:
    """Memoria gravada no Postgres, identificada por uma chave de conversa.

    Carrega o historico do banco na criacao; cada adicionar() persiste. Assim
    a conversa sobrevive a deploys e e' a mesma vista por qualquer instancia.
    """

    def __init__(self, pool, conversa_id: str, max_msgs: int = 40):
        self.pool = pool
        self.conversa_id = conversa_id
        self.max_msgs = max_msgs
        self._mensagens = self._carregar()

    def _carregar(self) -> list:
        with self.pool.connection() as c:
            row = c.execute(
                "select mensagens from memoria_conversa where conversa_id = %s",
                (self.conversa_id,),
            ).fetchone()
        if not row or not row[0]:
            return []
        dados = row[0]
        return dados if isinstance(dados, list) else json.loads(dados)

    def _salvar(self):
        payload = json.dumps(self._mensagens, ensure_ascii=False)
        with self.pool.connection() as c:
            c.execute(
                """insert into memoria_conversa (conversa_id, mensagens, atualizado_em)
                   values (%s, %s::jsonb, now())
                   on conflict (conversa_id)
                   do update set mensagens = excluded.mensagens, atualizado_em = now()""",
                (self.conversa_id, payload),
            )
            c.commit()

    def adicionar(self, papel: str, conteudo):
        self._mensagens.append({"role": papel, "content": _serializar_conteudo(conteudo)})
        if len(self._mensagens) > self.max_msgs * 2:
            self._mensagens = self._mensagens[-self.max_msgs:]
        self._salvar()

    def mensagens(self) -> list:
        return self._mensagens

    def limpar(self):
        self._mensagens = []
        with self.pool.connection() as c:
            c.execute("delete from memoria_conversa where conversa_id = %s", (self.conversa_id,))
            c.commit()
