#!/usr/bin/env bash
# ============================================================================
# add_observabilidade.sh  — Modulo de Comunicacao, Fase 1 (passo 2: captura)
#  - cria finance/observabilidade.py (writer + faxina de retencao)
#  - faz 5 edicoes cirurgicas em core/agent.py (gancho que loga cada turno)
# Pre-requisito: tabela conversas_log ja criada (migracao 031).
# RODE NA RAIZ DO REPO:  bash add_observabilidade.sh
# Aborta e restaura se qualquer ancora nao bater exatamente 1x ou nao compilar.
# ============================================================================
set -euo pipefail

echo "==> 1/5  Raiz do repo..."
[ -d finance ] && [ -d core ] || { echo "    ERRO: rode na RAIZ (finance/ e core/)." >&2; exit 1; }

echo "==> 2/5  Checando se ja' aplicado..."
if [ -f finance/observabilidade.py ] || grep -q "_registrar_obs" core/agent.py; then
  echo "    JA EXISTE observabilidade. Nada a fazer (evita duplicar)." >&2; exit 0; fi
cp core/agent.py core/agent.py.bak && echo "    backup: core/agent.py.bak"

echo "==> 3/5  Criando finance/observabilidade.py..."
cat > finance/observabilidade.py << 'OBSEOF'
"""finance/observabilidade.py — Modulo de Comunicacao (Fase 1): log de interacoes.

Grava 1 linha por turno em conversas_log, pra entender onde os clientes travam.
Best-effort: NUNCA derruba a resposta ao cliente (igual ao padrao do uso_api).

LGPD:
- Texto do cliente/resposta SO' e' gravado se a env LOG_TEXTO_CONVERSA=1.
  Sem ela, grava metadata (tipo, tools, sucesso, repetiu, custo) e texto NULO.
- Retencao 30 dias via expurgar_antigos() (rodar por cron 1x/dia).
- Leitura e' so' admin (a tela/consulta restringe; restrito NAO le).
"""
from __future__ import annotations

import logging
import os

_log = logging.getLogger("openclaw.observabilidade")


def _quer_texto() -> bool:
    return (os.environ.get("LOG_TEXTO_CONVERSA", "") or "").strip().lower() in (
        "1", "true", "sim", "yes")


def registrar_interacao(pool, conta_id, membro_id=None, canal=None,
                        tipo_midia=None, texto_usuario=None, resposta=None,
                        tools_usadas=None, modelo=None, sucesso=None,
                        repetiu=None, custo_centavos=None) -> bool:
    """Grava um turno em conversas_log. Best-effort (engole erro, devolve False).
    Respeita LOG_TEXTO_CONVERSA: sem ela, texto_usuario/resposta vao NULOS."""
    try:
        if not _quer_texto():
            texto_usuario = None
            resposta = None
        with pool.connection() as c:
            c.execute(
                """insert into conversas_log
                   (conta_id, membro_id, canal, tipo_midia, texto_usuario,
                    resposta, tools_usadas, modelo, sucesso, repetiu, custo_centavos)
                   values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (conta_id, membro_id, canal, tipo_midia, texto_usuario,
                 resposta, tools_usadas, modelo, sucesso, repetiu, custo_centavos),
            )
            c.commit()
        return True
    except Exception as e:  # noqa: BLE001
        _log.info("registrar_interacao falhou (ignorado): %s", e)
        return False


def expurgar_antigos(pool, dias: int = 30) -> int:
    """Faxina de retencao: apaga conversas com mais de `dias`. Delete ESCOPADO
    (where criado_em < now - dias) — nunca a tabela toda. Pra rodar via cron.
    Retorna quantas linhas apagou."""
    try:
        with pool.connection() as c:
            cur = c.execute(
                "delete from conversas_log "
                "where criado_em < now() - (%s || ' days')::interval",
                (str(int(dias)),),
            )
            n = getattr(cur, "rowcount", 0) or 0
            c.commit()
        _log.info("expurgar_antigos: %s linhas removidas (> %s dias)", n, dias)
        return n
    except Exception as e:  # noqa: BLE001
        _log.warning("expurgar_antigos falhou: %s", e)
        return 0
OBSEOF
echo "    finance/observabilidade.py criado."

echo "==> 4/5  Editando core/agent.py (5 ancoras)..."
PYBIN=python3; command -v python3 >/dev/null 2>&1 || PYBIN=python
"$PYBIN" - << 'PYEOF'
import sys
p = "core/agent.py"
src = open(p, encoding="utf-8").read()

edits = []

# R1 — inicializa acumuladores no inicio de _responder
edits.append(("R1-init", r'''                   media_type: str = "image/jpeg") -> str:
        conteudo = []''', r'''                   media_type: str = "image/jpeg") -> str:
        self._obs_tools = set(); self._obs_modelo = None; self._obs_custo = 0
        conteudo = []'''))

# R2 — acumula custo + modelo no bloco de usage
edits.append(("R2-custo", r'''                _brl = _usd * 5.40''', r'''                _brl = _usd * 5.40
                self._obs_modelo = _modelo_turno
                self._obs_custo += int(round(_brl * 100))'''))

# R3 — registra a tool usada
edits.append(("R3-tools", r'''                ferr = self.ferramentas.get(bloco.name)''', r'''                ferr = self.ferramentas.get(bloco.name)
                self._obs_tools.add(bloco.name)'''))

# R4 — captura o resultado e loga (reescreve corpo do responder)
edits.append(("R4-hook", r'''        with self._lock:
            self._sanear_memoria()
            try:
                return self._responder(texto, imagem_b64, media_type)
            except Exception as e:  # noqa: BLE001
                # Rede-de-seguranca: se a memoria estiver irrecuperavel (ex: erro
                # 400 de tool_use orfao que o saneamento nao pegou), ZERA tudo e
                # tenta uma vez do zero. Melhor perder o historico que travar.
                msg = str(e).lower()
                if "tool_use" in msg or "tool_result" in msg or "400" in msg:
                    self.memoria.limpar()
                    return self._responder(texto, imagem_b64, media_type)
                raise''', r'''        with self._lock:
            self._sanear_memoria()
            try:
                resultado = self._responder(texto, imagem_b64, media_type)
            except Exception as e:  # noqa: BLE001
                # Rede-de-seguranca: se a memoria estiver irrecuperavel (ex: erro
                # 400 de tool_use orfao que o saneamento nao pegou), ZERA tudo e
                # tenta uma vez do zero. Melhor perder o historico que travar.
                msg = str(e).lower()
                if "tool_use" in msg or "tool_result" in msg or "400" in msg:
                    self.memoria.limpar()
                    resultado = self._responder(texto, imagem_b64, media_type)
                else:
                    raise
            # OBSERVABILIDADE (Modulo de Comunicacao): loga o turno (best-effort)
            self._registrar_obs(texto, imagem_b64, media_type, resultado)
            return resultado'''))

# R5 — adiciona os 2 metodos novos logo apos _texto
edits.append(("R5-metodos", r'''    @staticmethod
    def _texto(resp) -> str:
        partes = [b.text for b in resp.content if getattr(b, "type", None) == "text"]
        return "\n".join(partes).strip()''', r'''    @staticmethod
    def _texto(resp) -> str:
        partes = [b.text for b in resp.content if getattr(b, "type", None) == "text"]
        return "\n".join(partes).strip()

    def _registrar_obs(self, texto, imagem_b64, media_type, resultado):
        # Grava o turno em conversas_log (best-effort; nunca quebra a resposta).
        try:
            livro = getattr(self, "livro", None)
            pool = getattr(livro, "pool", None)
            conta_id = getattr(livro, "conta_id", None)
            if pool is None or not isinstance(conta_id, int):
                return
            if imagem_b64:
                tipo = "pdf" if media_type == "application/pdf" else "foto"
            else:
                tipo = "texto"
            falhas = ("me embananei", "nao entendi", "Pode repetir")
            sucesso = not any(f in (resultado or "") for f in falhas)
            from finance import observabilidade as _obs
            _obs.registrar_interacao(
                pool,
                conta_id=conta_id,
                membro_id=getattr(livro, "membro_id", None),
                canal=getattr(self, "canal_atual", None),
                tipo_midia=tipo,
                texto_usuario=texto,
                resposta=resultado,
                tools_usadas=",".join(sorted(getattr(self, "_obs_tools", set()))) or None,
                modelo=getattr(self, "_obs_modelo", None),
                sucesso=sucesso,
                repetiu=self._humano_repetiu(texto),
                custo_centavos=getattr(self, "_obs_custo", 0),
            )
        except Exception:  # noqa: BLE001
            pass

    def _humano_repetiu(self, texto_atual) -> bool:
        # True se a msg humana anterior na memoria for ~igual a atual (atrito).
        try:
            atual = " ".join((texto_atual or "").lower().split())
            if not atual:
                return False
            humanos = []
            for m in self.memoria.mensagens():
                if m.get("role") != "user":
                    continue
                c = m.get("content")
                if not isinstance(c, list):
                    continue
                if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in c):
                    continue
                for b in c:
                    if isinstance(b, dict) and b.get("type") == "text":
                        t = " ".join((b.get("text") or "").lower().split())
                        if t:
                            humanos.append(t)
            return len(humanos) >= 2 and humanos[-1] == atual and humanos[-2] == atual
        except Exception:  # noqa: BLE001
            return False'''))

for desc, old, new in edits:
    n = src.count(old)
    if n != 1:
        print(f"    ERRO {desc}: ancora apareceu {n}x (esperava 1). Abortando.", file=sys.stderr)
        sys.exit(2)
    src = src.replace(old, new, 1)

open(p, "w", encoding="utf-8").write(src)
print("    5 edicoes aplicadas no core/agent.py")
PYEOF

echo "==> 5/5  Validando + commit..."
if ! "$PYBIN" -m py_compile finance/observabilidade.py core/agent.py 2>/tmp/_obs_err; then
  echo "    ERRO: nao compila. Restaurando." >&2; cat /tmp/_obs_err >&2
  mv core/agent.py.bak core/agent.py; rm -f finance/observabilidade.py
  exit 1
fi
grep -q "_registrar_obs" core/agent.py && grep -q "def registrar_interacao" finance/observabilidade.py || {
  echo "    ERRO: funcoes nao ficaram. Restaurando." >&2
  mv core/agent.py.bak core/agent.py; rm -f finance/observabilidade.py; exit 1; }
echo "    OK: compila e gancho presente."

git add finance/observabilidade.py core/agent.py
if git diff --cached --quiet; then echo "    Nada a commitar."; else
  git commit -m "feat: modulo de comunicacao fase 1 - captura de interacoes (writer + gancho)"
  git push; echo "    Commit + push OK."; fi
rm -f core/agent.py.bak
echo ""
echo "PRONTO. Ligar env LOG_TEXTO_CONVERSA=1 (web+bot) p/ guardar texto completo."
