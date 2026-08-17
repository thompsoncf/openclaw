"""Faxina de retenção: apaga o histórico de WhatsApp de canais desconectados
há mais de 30 dias.

Uso (cron diário):  python -m scripts.faxina_retencao
Simulação (não apaga nada, só lista):  python -m scripts.faxina_retencao --dry-run
Prazo diferente:  python -m scripts.faxina_retencao --dias 60

POR QUE ESTE ARQUIVO EXISTE E TAMBÉM É CHAMADO DE OUTRO LUGAR
Este é o lar CERTO da faxina, mas por si só ele não roda: o `render.yaml` deste
repo é documentação, não Blueprint (está escrito no cabeçalho dele — reconectar
como Blueprint duplica os serviços), então declarar um `type: cron` aqui não cria
cron nenhum no Render. Criar o serviço é passo manual.

Já existe precedente ruim disso no repo: `finance.observabilidade.expurgar_antigos`
documenta "retenção 30 dias, rodar por cron 1x/dia" desde que foi escrita e
NUNCA foi chamada por ninguém — retenção que existe no código e não acontece.

Pra esta não repetir o mesmo destino, `scripts.monitor_saldos` (cron que roda de
verdade, todo dia às 12:00 UTC) chama `rodar()` no fim da passada dele. Quando um
cron dedicado for criado no Render apontando pra cá, é só remover essa chamada.
"""
from __future__ import annotations

import argparse
import logging

_log = logging.getLogger("openclaw.faxina_retencao")


def rodar(dias: int | None = None, dry_run: bool = False) -> dict:
    """Uma passada da faxina. Devolve o que apagou (ou o que apagaria, no dry-run).

    Tolerante a falha: este código roda pendurado num cron que tem outro trabalho
    principal, e um erro aqui não pode derrubar a passada dele."""
    from db.conexao import get_pool
    from finance import retencao
    prazo = retencao.DIAS_RETENCAO if dias is None else int(dias)
    try:
        pool = get_pool()
    except Exception as e:  # noqa: BLE001
        _log.warning("faxina_retencao: sem banco, pulando: %s", e)
        return {"contas": 0, "mensagens": 0, "conversas": 0, "contatos": 0, "erros": 1}
    if dry_run:
        contas = retencao.canais_vencidos(pool, prazo)
        print(f"[dry-run] {len(contas)} conta(s) com WhatsApp desconectado "
              f"há mais de {prazo} dias: {contas or '—'}")
        for cid in contas:
            d = retencao.resumo_historico(pool, cid)
            print(f"  conta {cid}: {d['mensagens']} mensagens, {d['conversas']} "
                  f"conversas, {d['contatos']} contatos"
                  + (f" ({d['de']} a {d['ate']})" if d["de"] else ""))
        return {"contas": len(contas), "mensagens": 0, "conversas": 0,
                "contatos": 0, "erros": 0, "dry_run": True}
    try:
        return retencao.faxina(pool, prazo)
    except Exception as e:  # noqa: BLE001
        _log.warning("faxina_retencao: falhou: %s: %s", type(e).__name__, e)
        return {"contas": 0, "mensagens": 0, "conversas": 0, "contatos": 0, "erros": 1}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dias", type=int, default=None,
                   help="prazo em dias (padrão: finance.retencao.DIAS_RETENCAO)")
    p.add_argument("--dry-run", action="store_true",
                   help="só lista o que apagaria, sem apagar")
    a = p.parse_args()
    r = rodar(dias=a.dias, dry_run=a.dry_run)
    if not a.dry_run:
        print(f"contas={r['contas']} mensagens={r['mensagens']} "
              f"conversas={r['conversas']} contatos={r['contatos']} erros={r['erros']}")
