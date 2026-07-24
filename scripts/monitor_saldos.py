"""Monitor PROATIVO de saldo/gasto dos provedores pagos (roda como cron diario).

Avisa o ADMIN (e-mail + Telegram, via finance.notificar.avisar_admin) ANTES de
uma integracao paga parar:

1. Anthropic (IA): a API NAO expoe saldo restante — so' Usage/Cost Report. Aqui
   consultamos o Cost Report da Admin API e alertamos quando o GASTO DE UM DIA
   estoura um teto (pega pico/vazamento de custo). Precisa de ANTHROPIC_ADMIN_KEY.
2. Twilio (WhatsApp): tem saldo REAL — GET .../Balance.json. Alerta abaixo do minimo.
3. Asaas (pagamentos): tem saldo REAL — GET /finance/balance. Alerta abaixo do minimo.

Tudo tolerante a falha: sem env/rede, apenas loga e pula (nunca quebra).

Variaveis de ambiente (todas opcionais — cada checagem so' roda se configurada):
- ANTHROPIC_ADMIN_KEY        chave admin (sk-ant-admin...) do Cost Report
- ANTHROPIC_GASTO_DIA_MAX_USD  teto de gasto/dia da IA (ex "20"); sem isso, pula
- TWILIO_ACCOUNT_SID/TWILIO_AUTH_TOKEN (ja' existem) + TWILIO_SALDO_MIN_USD (ex "5")
- ASAAS_API_KEY (ja' existe) + ASAAS_SALDO_MIN (ex "50")

Uso (cron): python -m scripts.monitor_saldos
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

_log = logging.getLogger("openclaw.monitor_saldos")


# ---------------------------------------------------------------------------
# Logica pura (testavel, sem rede)
# ---------------------------------------------------------------------------

def _to_float(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def somar_gasto_cost_report(payload) -> float:
    """Soma o gasto (USD) do JSON do Cost Report, tolerante ao formato: procura
    dicts que tenham 'currency' + 'amount' (a forma dos resultados de custo) e
    soma os 'amount'. Ignora qualquer outro 'amount' solto pra nao contar 2x."""
    total = 0.0

    def walk(o):
        nonlocal total
        if isinstance(o, dict):
            if "currency" in o and "amount" in o:
                val = _to_float(o.get("amount"))
                if val is not None:
                    total += val
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for it in o:
                walk(it)

    walk(payload)
    return round(total, 4)


def alertar_gasto_alto(gasto: float | None, teto: float | None) -> bool:
    """True se o gasto do dia estourou o teto (ambos definidos)."""
    return gasto is not None and teto is not None and gasto > teto


def alertar_saldo_baixo(saldo: float | None, minimo: float | None) -> bool:
    """True se o saldo caiu abaixo (ou igual) do minimo (ambos definidos)."""
    return saldo is not None and minimo is not None and saldo <= minimo


# ---------------------------------------------------------------------------
# Consultas (rede) — cada uma devolve None se sem config/erro
# ---------------------------------------------------------------------------

def gasto_anthropic_ontem(admin_key: str | None, timeout: float = 20.0) -> float | None:
    """Gasto (USD) da IA no dia de ONTEM (UTC), via Cost Report da Admin API."""
    if not admin_key:
        return None
    hoje = datetime.now(timezone.utc).date()
    inicio = datetime.combine(hoje - timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
    fim = datetime.combine(hoje, datetime.min.time(), tzinfo=timezone.utc)
    import httpx
    try:
        with httpx.Client(timeout=timeout) as c:
            r = c.get(
                "https://api.anthropic.com/v1/organizations/cost_report",
                params={
                    "starting_at": inicio.isoformat().replace("+00:00", "Z"),
                    "ending_at": fim.isoformat().replace("+00:00", "Z"),
                },
                headers={"x-api-key": admin_key, "anthropic-version": "2023-06-01"},
            )
        if r.status_code != 200:
            _log.warning("cost_report HTTP %s: %s", r.status_code, r.text[:200])
            return None
        return somar_gasto_cost_report(r.json())
    except Exception as e:  # noqa: BLE001
        _log.warning("cost_report falhou: %s", e)
        return None


def saldo_twilio(sid: str | None, token: str | None, timeout: float = 15.0) -> float | None:
    """Saldo REAL (USD) da conta Twilio."""
    if not sid or not token:
        return None
    import httpx
    try:
        with httpx.Client(timeout=timeout) as c:
            r = c.get(
                f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Balance.json",
                auth=(sid, token),
            )
        if r.status_code != 200:
            _log.warning("twilio balance HTTP %s: %s", r.status_code, r.text[:200])
            return None
        return _to_float(r.json().get("balance"))
    except Exception as e:  # noqa: BLE001
        _log.warning("twilio balance falhou: %s", e)
        return None


def saldo_asaas(timeout: float = 15.0) -> float | None:
    """Saldo REAL (R$) da conta Asaas (reusa finance.asaas._get)."""
    if not os.environ.get("ASAAS_API_KEY"):
        return None
    try:
        from finance import asaas
        j = asaas._get("/finance/balance", timeout=timeout)
        return _to_float(j.get("balance"))
    except Exception as e:  # noqa: BLE001
        _log.warning("asaas balance falhou: %s", e)
        return None


# ---------------------------------------------------------------------------
# Orquestracao
# ---------------------------------------------------------------------------

def _avisar(assunto: str, mensagem: str) -> None:
    try:
        from finance.notificar import avisar_admin
        avisar_admin(assunto, mensagem)
    except Exception as e:  # noqa: BLE001
        _log.warning("falha ao avisar admin: %s", e)


def rodar() -> None:
    """Roda as 3 checagens. So' alerta o que estiver configurado e fora do limite."""
    # 1) Anthropic — gasto diario anomalo
    teto_ia = _to_float(os.environ.get("ANTHROPIC_GASTO_DIA_MAX_USD"))
    if teto_ia is not None:
        gasto = gasto_anthropic_ontem(os.environ.get("ANTHROPIC_ADMIN_KEY"))
        _log.info("IA: gasto_ontem=%s teto=%s", gasto, teto_ia)
        if alertar_gasto_alto(gasto, teto_ia):
            _avisar(
                "⚠️ Gasto da IA (Anthropic) acima do normal",
                f"O gasto de ontem foi US$ {gasto:.2f}, acima do teto de "
                f"US$ {teto_ia:.2f}. Confira o consumo no console da Anthropic "
                "(Usage & Cost) — pode ser pico de uso ou vazamento de custo.",
            )

    # 2) Twilio — saldo real baixo
    min_tw = _to_float(os.environ.get("TWILIO_SALDO_MIN_USD"))
    if min_tw is not None:
        saldo = saldo_twilio(os.environ.get("TWILIO_ACCOUNT_SID"),
                             os.environ.get("TWILIO_AUTH_TOKEN"))
        _log.info("Twilio: saldo=%s min=%s", saldo, min_tw)
        if alertar_saldo_baixo(saldo, min_tw):
            _avisar(
                "⚠️ Saldo do WhatsApp (Twilio) baixo",
                f"O saldo da Twilio esta em US$ {saldo:.2f}, abaixo do minimo de "
                f"US$ {min_tw:.2f}. Recarregue pra nao parar de responder no WhatsApp.",
            )

    # 3) Asaas — saldo real baixo
    min_as = _to_float(os.environ.get("ASAAS_SALDO_MIN"))
    if min_as is not None:
        saldo = saldo_asaas()
        _log.info("Asaas: saldo=%s min=%s", saldo, min_as)
        if alertar_saldo_baixo(saldo, min_as):
            _avisar(
                "⚠️ Saldo do Asaas baixo",
                f"O saldo do Asaas esta em R$ {saldo:.2f}, abaixo do minimo de "
                f"R$ {min_as:.2f}.",
            )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    rodar()
