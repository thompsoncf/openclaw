#!/usr/bin/env python3
"""Cliente de linha de comando da API do Render (api.render.com/v1).

Serve pra INSPECIONAR e OPERAR os servicos do Render sem sair do terminal:
status de deploy, historico, logs, variaveis de ambiente e disparar deploy.

PRE-REQUISITOS
--------------
1. RENDER_API_KEY no ambiente (Render Dashboard -> Account Settings -> API Keys).
   NUNCA passe a chave por argumento (ela vaza no historico). So' por env:
       export RENDER_API_KEY=rnd_xxx
2. Rede: este helper fala com api.render.com. Em ambientes com egresso restrito
   (ex: "trusted network access"), esse host precisa estar liberado - senao a
   conexao volta 403 no proxy. O script detecta isso e explica.

USO
---
    python -m scripts.render_cli services
    python -m scripts.render_cli status  openclaw-bot
    python -m scripts.render_cli deploys openclaw-bot --limit 5
    python -m scripts.render_cli deploy  openclaw-bot            # dispara deploy
    python -m scripts.render_cli deploy  openclaw-bot --clear-cache
    python -m scripts.render_cli envvars openclaw-bot           # chaves (valores ocultos)
    python -m scripts.render_cli logs    openclaw-bot --limit 100

O <servico> pode ser o nome (openclaw-bot) OU o id (srv-...). Nome e' resolvido
automaticamente pela lista de servicos.

SEM ACESSO A' API? USE O HISTORICO
----------------------------------
Todo comando acima fala com api.render.com. Onde esse host esta' bloqueado
(ex: o ambiente do Claude Code na web, que devolve 403 no CONNECT), eles nao
funcionam - e nao ha' contorno pelo lado do script.

Pra esse caso existe o `historico`, que le os eventos de deploy do NOSSO
Postgres (tabela render_evento, alimentada pelo webhook do Render). Nao precisa
de RENDER_API_KEY nem de rede externa - so' de DATABASE_URL:

    python -m scripts.render_cli historico                      # ultimos 20
    python -m scripts.render_cli historico --servico openclaw-web-bcu3
    python -m scripts.render_cli historico --falhas --limit 5   # so' o que quebrou
    python -m scripts.render_cli historico --falhas --log       # com a cauda do log

Como ligar o webhook: docs/RENDER_OBSERVABILIDADE.md
"""
from __future__ import annotations

import argparse
import json
import os
import sys

API = "https://api.render.com/v1"


def _sessao():
    try:
        import requests
    except Exception:  # noqa: BLE001
        sys.exit("Falta a lib 'requests' (pip install requests).")
    key = os.environ.get("RENDER_API_KEY")
    if not key:
        sys.exit("RENDER_API_KEY nao definida. Faca:\n  export RENDER_API_KEY=rnd_xxx")
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {key}",
                      "Accept": "application/json",
                      "Content-Type": "application/json"})
    # TLS: o proxy do ambiente re-termina TLS com uma CA propria; aponta pra ela.
    ca = os.environ.get("REQUESTS_CA_BUNDLE") or "/root/.ccr/ca-bundle.crt"
    s.verify = ca if os.path.exists(ca) else True
    return s


def _req(s, metodo, caminho, **kw):
    import requests
    url = caminho if caminho.startswith("http") else f"{API}{caminho}"
    try:
        r = s.request(metodo, url, timeout=30, **kw)
    except requests.exceptions.ProxyError as e:
        sys.exit(f"[REDE BLOQUEADA] O proxy recusou a conexao com api.render.com "
                 f"(403 de politica de egresso). Libere esse host na politica de "
                 f"rede do ambiente pra usar a API do Render.\nDetalhe: {e}")
    except requests.exceptions.SSLError as e:
        sys.exit(f"[TLS] Verificacao falhou. Aponte REQUESTS_CA_BUNDLE pra "
                 f"/root/.ccr/ca-bundle.crt.\nDetalhe: {e}")
    except requests.exceptions.RequestException as e:
        sys.exit(f"[REDE] Falha ao falar com o Render: {e}")
    if r.status_code == 401:
        sys.exit("[AUTH] 401 - RENDER_API_KEY invalida ou sem permissao.")
    if r.status_code == 403:
        sys.exit("[POLITICA] 403 - host bloqueado pela politica de rede OU a "
                 "chave nao tem acesso a esse recurso.")
    if r.status_code >= 400:
        sys.exit(f"[HTTP {r.status_code}] {r.text[:500]}")
    return r


def _servicos(s) -> list[dict]:
    # a API pagina; devolve [{service:{...}, cursor:...}]
    itens, cursor = [], None
    while True:
        params = {"limit": 100}
        if cursor:
            params["cursor"] = cursor
        r = _req(s, "GET", "/services", params=params)
        lote = r.json()
        if not lote:
            break
        for it in lote:
            itens.append(it.get("service", it))
        cursor = lote[-1].get("cursor")
        if len(lote) < 100:
            break
    return itens


def _resolver(s, alvo: str) -> dict:
    """Aceita id (srv-...) ou nome; devolve o dict do servico."""
    if alvo.startswith("srv-"):
        return _req(s, "GET", f"/services/{alvo}").json()
    achados = [sv for sv in _servicos(s) if sv.get("name") == alvo]
    if not achados:
        nomes = ", ".join(sv.get("name", "?") for sv in _servicos(s)) or "(nenhum)"
        sys.exit(f"Servico '{alvo}' nao encontrado. Disponiveis: {nomes}")
    if len(achados) > 1:
        sys.exit(f"Mais de um servico chamado '{alvo}'. Use o id (srv-...).")
    return achados[0]


# ---------- comandos ----------

def cmd_services(s, _a):
    for sv in _servicos(s):
        print(f"{sv.get('id'):<24} {sv.get('type',''):<14} {sv.get('name','')}")


def cmd_status(s, a):
    sv = _resolver(s, a.servico)
    sid = sv["id"]
    r = _req(s, "GET", f"/services/{sid}/deploys", params={"limit": 1})
    lote = r.json()
    if not lote:
        print(f"{sv['name']} ({sid}): sem deploys.")
        return
    d = lote[0].get("deploy", lote[0])
    commit = (d.get("commit") or {})
    print(f"Servico : {sv['name']} ({sid})")
    print(f"Deploy  : {d.get('id')}  status={d.get('status')}")
    print(f"Commit  : {(commit.get('id') or '')[:12]}  {commit.get('message','').splitlines()[0] if commit.get('message') else ''}")
    print(f"Criado  : {d.get('createdAt')}   Finalizado: {d.get('finishedAt')}")


def cmd_deploys(s, a):
    sv = _resolver(s, a.servico)
    r = _req(s, "GET", f"/services/{sv['id']}/deploys", params={"limit": a.limit})
    for it in r.json():
        d = it.get("deploy", it)
        c = (d.get("commit") or {})
        msg = (c.get("message") or "").splitlines()[0] if c.get("message") else ""
        print(f"{d.get('id'):<28} {d.get('status',''):<12} {(c.get('id') or '')[:8]:<8} "
              f"{d.get('finishedAt') or d.get('createdAt','')}  {msg}")


def cmd_deploy(s, a):
    sv = _resolver(s, a.servico)
    body = {"clearCache": "clear" if a.clear_cache else "do_not_clear"}
    r = _req(s, "POST", f"/services/{sv['id']}/deploys", data=json.dumps(body))
    d = r.json()
    print(f"Deploy disparado em {sv['name']}: id={d.get('id')} status={d.get('status')}")
    print("Acompanhe com: python -m scripts.render_cli status " + a.servico)


def cmd_envvars(s, a):
    sv = _resolver(s, a.servico)
    r = _req(s, "GET", f"/services/{sv['id']}/env-vars", params={"limit": 100})
    for it in r.json():
        ev = it.get("envVar", it)
        val = ev.get("value")
        # valores sao SEGREDO: mostra so' a chave e um resumo do tamanho.
        resumo = "(vazio)" if not val else f"<{len(val)} chars>"
        print(f"{ev.get('key'):<28} {resumo}")


def cmd_logs(s, a):
    sv = _resolver(s, a.servico)
    owner = sv.get("ownerId") or (sv.get("owner") or {}).get("id")
    if not owner:
        sys.exit("Nao consegui o ownerId do servico pra consultar logs.")
    params = {"ownerId": owner, "resource": sv["id"], "limit": a.limit,
              "direction": "backward"}
    r = _req(s, "GET", "/logs", params=params)
    dados = r.json()
    linhas = dados.get("logs", dados) if isinstance(dados, dict) else dados
    for ln in (linhas or []):
        ts = ln.get("timestamp", "")
        msg = ln.get("message", "")
        print(f"{ts}  {msg}")


def cmd_historico(_s, a):
    """Le do Postgres, NAO da API do Render.

    E' o unico comando que funciona com api.render.com bloqueada, porque quem
    buscou os dados foi o webhook (rodando dentro do Render), nao este script.
    """
    from core.render_eventos import historico
    linhas = historico(servico=a.servico or "", limite=a.limit, so_falhas=a.falhas)
    if not linhas:
        print("Nenhum evento gravado ainda.")
        print("Se o webhook ja' esta' ligado, so' aparece a partir do proximo deploy.")
        print("Pra ligar: docs/RENDER_OBSERVABILIDADE.md")
        return
    for e in linhas:
        # marcador visual: da' pra bater o olho e achar a falha na lista
        marca = {True: "ok  ", False: "FALHA"}.get(e["sucesso"], "-   ")
        quando = e["recebido_em"].strftime("%d/%m %H:%M") if e["recebido_em"] else "?"
        msg = (e["commit_msg"] or "").splitlines()
        msg = msg[0][:60] if msg else ""
        print(f"{quando}  {marca:<6} {(e['servico_nome'] or e['servico_id'] or '?'):<26} "
              f"{(e['status'] or e['tipo']):<16} {(e['commit_id'] or '')[:8]:<8} {msg}")
        if a.log and e.get("log_trecho"):
            print("  " + "\n  ".join(e["log_trecho"].splitlines()))
            print()


def main(argv=None):
    p = argparse.ArgumentParser(description="Cliente da API do Render.")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("services", help="lista os servicos").set_defaults(fn=cmd_services)

    sp = sub.add_parser("status", help="status do ultimo deploy"); sp.add_argument("servico"); sp.set_defaults(fn=cmd_status)
    sp = sub.add_parser("deploys", help="historico de deploys"); sp.add_argument("servico"); sp.add_argument("--limit", type=int, default=10); sp.set_defaults(fn=cmd_deploys)
    sp = sub.add_parser("deploy", help="dispara um deploy"); sp.add_argument("servico"); sp.add_argument("--clear-cache", action="store_true"); sp.set_defaults(fn=cmd_deploy)
    sp = sub.add_parser("envvars", help="lista chaves de env (valores ocultos)"); sp.add_argument("servico"); sp.set_defaults(fn=cmd_envvars)
    sp = sub.add_parser("logs", help="logs recentes"); sp.add_argument("servico"); sp.add_argument("--limit", type=int, default=100); sp.set_defaults(fn=cmd_logs)

    sp = sub.add_parser("historico", help="historico de deploys (le do banco, sem API)")
    sp.add_argument("--servico", default="", help="filtra por nome ou id do servico")
    sp.add_argument("--limit", type=int, default=20)
    sp.add_argument("--falhas", action="store_true", help="so' os deploys que quebraram")
    sp.add_argument("--log", action="store_true", help="mostra a cauda do log de cada falha")
    # api=False: este comando NAO fala com api.render.com, entao nao pode exigir
    # RENDER_API_KEY - e' justamente o comando pra quando a API esta' fora de alcance.
    sp.set_defaults(fn=cmd_historico, api=False)

    a = p.parse_args(argv)
    # So' abre sessao HTTP pra quem realmente vai usar a API.
    sessao = _sessao() if getattr(a, "api", True) else None
    a.fn(sessao, a)


if __name__ == "__main__":
    main()
