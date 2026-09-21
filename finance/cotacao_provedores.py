"""Os conectores de cotação — um arquivo por provedor, e o banco não muda com eles.

A DECISÃO DO DONO (21/09/2026): "camada agnóstica primeiro". Nada em
`finance/cotacao.py` nem na migração 304 conhece InsureMO, Segfy, Quiver, TEx ou
seguradora nenhuma. Quem conhece é este arquivo — e plugar um provedor novo é
escrever uma classe com dois métodos.

POR QUE ASSIM, E NÃO DIRETO NO PROVEDOR ESCOLHIDO. Três razões concretas, nesta
ordem:

1. **O contrato ainda não existe.** Cotação de verdade, no Brasil, sai por
   multicálculo (Segfy, Quiver, TEx/Teleport) ou por acordo direto — e todos
   exigem contrato, homologação e credencial. Esperar isso pra começar seria
   esperar semanas com a tela vazia.
2. **A InsureMO é vendida pra SEGURADORA, não pra corretora.** Ela é o middleware
   do eBaoTech: APIs atômicas de produto, rating, emissão, endosso e sinistro,
   orquestradas pelo iComposer. Pra corretora usar, alguém tem que ter os produtos
   configurados lá dentro. É um caminho possível; não é o único, e amarrar a base
   nele antes de confirmar seria construir sobre suposição.
3. **Provedor troca.** Corretora que muda de multicálculo é rotina do mercado. Com
   a camada agnóstica, trocar é trocar uma variável de ambiente.

⚠️ O QUE FALTA PRA PLUGAR A INSUREMO — e por que o conector dela não está aqui.
Em 21/09/2026 `docs.insuremo.com` e `insuremo.com` estão BLOQUEADOS pela política
de egresso desta máquina (o proxy devolve EGRESS_BLOCKED), então a documentação
não pôde ser lida. Escrever o mapa de campos de memória seria inventar um contrato
de API — o tipo de código que passa no teste e falha na primeira chamada real. As
quatro coisas que a doc precisa responder, e que fecham o conector num arquivo:

    1. Autenticação: URL do token, se é OAuth2 client_credentials, e quais
       cabeçalhos de tenant/produto acompanham cada chamada.
    2. Endpoint de cotação: caminho, e o JSON do risco (como o veículo é
       identificado — FIPE? placa? código interno?).
    3. Formato da resposta: onde está o prêmio, e SE ele vem com o IOF separado
       (isto decide se a comissão pode ser estimada — ver `cotacao.Oferta`).
    4. Emissão: se existe endpoint de proposta e o que ele devolve.

Com isso, o conector é uma subclasse de `ProvedorHTTP` com `_payload` e `_ofertas`.

O PADRÃO É 'manual', E ISSO NÃO É PLACEHOLDER. Sem provedor contratado, o
corretor digita na tela as ofertas que ele mesmo levantou e o comparativo, o
histórico e a conversão em proposta funcionam igual. É o que faz a corretora usar
o recurso hoje em vez de esperar contrato de terceiro.
"""
from __future__ import annotations

import logging
import os
import time

from .cotacao import Oferta

_log = logging.getLogger("openclaw.cotacao.provedores")


class ProvedorErro(Exception):
    """Erro vindo do provedor. A mensagem vai pra linha da cotação (`erro`)."""


class Provedor:
    """O contrato que todo conector cumpre. Dois métodos, e nada mais.

    `cotar` recebe o risco normalizado de `cotacao.normalizar_risco` e devolve
    `Oferta`s. `emitir` recebe o risco e a oferta escolhida e devolve o que a
    seguradora respondeu — só é chamado quando `suporta_emissao` é True.
    """
    chave = "base"
    nome = "Provedor"
    suporta_emissao = False

    def cotar(self, risco: dict) -> list[Oferta]:
        raise NotImplementedError

    def emitir(self, risco: dict, oferta: dict) -> dict:
        raise NotImplementedError


class ProvedorManual(Provedor):
    """Sem API: quem cota é o corretor, e o sistema guarda o resultado.

    `cotar` devolve lista vazia — e vazio aqui NÃO é falha. É o estado honesto de
    "ninguém foi consultado automaticamente"; as ofertas entram pela tela, e daí
    pra frente (comparativo, escolha, proposta, histórico) tudo é idêntico ao
    caminho automático. A emissão cai no roteiro pro portal da seguradora
    (`cotacao.roteiro_do_portal`), que é como se emite no Brasil hoje.
    """
    chave = "manual"
    nome = "Manual (o corretor digita as ofertas)"
    suporta_emissao = False

    def cotar(self, risco: dict) -> list[Oferta]:
        return []


class ProvedorHTTP(Provedor):
    """A mecânica comum de um provedor REST — o que NÃO muda de um pra outro.

    Cuida de: base URL, autenticação (chave estática no cabeçalho OU OAuth2
    client_credentials com o token guardado até expirar), tempo limite, e
    transformar status != 2xx em `ProvedorErro` com o corpo truncado na mensagem.

    A subclasse implementa só o que é específico: `_payload(risco)` monta o corpo
    da cotação e `_ofertas(resposta)` lê a resposta. É a fronteira certa — é
    exatamente essa parte que a documentação de cada provedor descreve.

    CONFIGURAÇÃO POR AMBIENTE, com o prefixo da chave do provedor. Pra uma chave
    'segfy', o conector lê:

        COTACAO_SEGFY_BASE_URL       (obrigatória)
        COTACAO_SEGFY_API_KEY        chave estática, se for esse o modelo
        COTACAO_SEGFY_TOKEN_URL      OAuth2: onde pedir o token
        COTACAO_SEGFY_CLIENT_ID      OAuth2
        COTACAO_SEGFY_CLIENT_SECRET  OAuth2
        COTACAO_SEGFY_TENANT         cabeçalho de tenant, quando o provedor exigir

    Nunca em banco e nunca no código: credencial de terceiro em coluna é
    credencial que sai num dump.
    """
    chave = "http"
    nome = "HTTP"
    suporta_emissao = False
    caminho_cotacao = "/quote"
    caminho_emissao = "/policy"
    timeout = 30.0

    def __init__(self):
        self._token = None
        self._token_expira = 0.0

    # ---------------------------------------------------------- configuração
    def _env(self, sufixo: str, obrigatoria: bool = False) -> str:
        v = (os.environ.get(f"COTACAO_{self.chave.upper()}_{sufixo}") or "").strip()
        if obrigatoria and not v:
            raise ProvedorErro(
                f"{self.nome}: falta a variável COTACAO_{self.chave.upper()}_{sufixo}")
        return v

    @property
    def configurado(self) -> bool:
        return bool(self._env("BASE_URL"))

    def _cabecalhos(self) -> dict:
        h = {"Content-Type": "application/json", "Accept": "application/json"}
        chave = self._env("API_KEY")
        if chave:
            h["Authorization"] = f"Bearer {chave}"
        elif self._env("TOKEN_URL"):
            h["Authorization"] = f"Bearer {self._oauth_token()}"
        tenant = self._env("TENANT")
        if tenant:
            h["X-Tenant"] = tenant
        return h

    def _oauth_token(self) -> str:
        """OAuth2 client_credentials, com o token guardado em memória até expirar.

        Guardado POR PROCESSO e não em banco: o token vive minutos, e persistir um
        segredo de vida curta cria um lugar a mais de onde ele pode vazar. A
        margem de 60s evita o caso em que o token expira no voo da requisição.
        """
        agora = time.time()
        if self._token and agora < self._token_expira - 60:
            return self._token
        import httpx
        url = self._env("TOKEN_URL", obrigatoria=True)
        dados = {"grant_type": "client_credentials",
                 "client_id": self._env("CLIENT_ID", obrigatoria=True),
                 "client_secret": self._env("CLIENT_SECRET", obrigatoria=True)}
        with httpx.Client(timeout=self.timeout) as c:
            r = c.post(url, data=dados)
        if r.status_code >= 300:
            raise ProvedorErro(f"{self.nome}: token {r.status_code} — {r.text[:200]}")
        j = r.json()
        self._token = j.get("access_token") or j.get("token") or ""
        if not self._token:
            raise ProvedorErro(f"{self.nome}: resposta de token sem access_token")
        self._token_expira = agora + float(j.get("expires_in") or 300)
        return self._token

    # ------------------------------------------------------------- transporte
    def _post(self, caminho: str, payload: dict) -> dict:
        import httpx
        url = self._env("BASE_URL", obrigatoria=True).rstrip("/") + caminho
        with httpx.Client(timeout=self.timeout) as c:
            r = c.post(url, json=payload, headers=self._cabecalhos())
        if r.status_code >= 300:
            raise ProvedorErro(f"{self.nome} {r.status_code} em {caminho}: {r.text[:300]}")
        try:
            return r.json()
        except ValueError:
            raise ProvedorErro(f"{self.nome}: resposta não é JSON — {r.text[:200]}")

    # ------------------------------------------------- o que a subclasse faz
    def _payload(self, risco: dict) -> dict:
        raise NotImplementedError("o conector precisa montar o corpo da cotação")

    def _ofertas(self, resposta: dict) -> list[Oferta]:
        raise NotImplementedError("o conector precisa ler as ofertas da resposta")

    def _payload_emissao(self, risco: dict, oferta: dict) -> dict:
        raise NotImplementedError("o conector precisa montar o corpo da emissão")

    def cotar(self, risco: dict) -> list[Oferta]:
        return self._ofertas(self._post(self.caminho_cotacao, self._payload(risco)))

    def emitir(self, risco: dict, oferta: dict) -> dict:
        return self._post(self.caminho_emissao, self._payload_emissao(risco, oferta))


# ────────────────────────────────────────────────────────────── o registro

_REGISTRO: dict[str, Provedor] = {}


def registrar(p: Provedor) -> Provedor:
    """Põe o provedor no registro. Chamado no import do conector."""
    _REGISTRO[p.chave] = p
    return p


def registrados() -> list[Provedor]:
    return list(_REGISTRO.values())


registrar(ProvedorManual())


def provedor_ativo(pool=None, conta_id: int | None = None) -> Provedor:
    """Qual conector usar. Hoje é global (variável `COTACAO_PROVEDOR`).

    `pool` e `conta_id` entram na assinatura desde já de propósito: no dia em que
    duas corretoras da base usarem multicálculos diferentes, a escolha vira uma
    coluna e NENHUM chamador muda. A alternativa — acrescentar os parâmetros
    depois — obrigaria a mexer nas três portas de entrada ao mesmo tempo.

    Chave desconhecida cai no manual e LOGA: melhor a corretora digitar as ofertas
    do que a tela quebrar por causa de uma variável de ambiente escrita errada.
    """
    chave = (os.environ.get("COTACAO_PROVEDOR") or "manual").strip().lower()
    p = _REGISTRO.get(chave)
    if p is None:
        _log.warning("COTACAO_PROVEDOR=%r não está registrado — usando o manual", chave)
        return _REGISTRO["manual"]
    return p
