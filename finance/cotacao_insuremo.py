"""O conector da InsureMO — a Calculate API (Policy Rating API), lida em 21/09/2026.

O QUE ESTA DOCUMENTADO E ESTÁ IMPLEMENTADO AQUI. A página "Policy Rating API" da
InsureMO descreve a chamada que dá preço, e é ela que este arquivo fala:

    POST {server}/quotation/core/quotation/v1/calculate   (sem persistir cotação)
    POST {server}/proposal/core/proposal/v1/calculateEx   (sem persistir proposta)

O pedido é um objeto de apólice — `ProductCode`, vigência, moeda, `OrgCode`,
`AgentCode` — com o risco dentro de `PolicyLobList[].PolicyRiskList[]` e as
coberturas em `PolicyCoverageList[]`. A resposta é o MESMO objeto, com os campos
de prêmio preenchidos em cada nível (cobertura, risco, ramo e apólice).

════════════════════════════════════════════════════════════════════════════
O ACHADO QUE IMPORTA: A INSUREMO SEPARA O IMPOSTO — e a comissão vem pronta.

No exemplo da própria doc:

    "BeforeVatPremium": 10,      ← o LÍQUIDO (é sobre ele que a comissão incide)
    "Vat": 0.8,                  ← o imposto
    "DuePremium": 10.8,          ← o que o cliente paga
    "Commission": 1,             ← a comissão, em dinheiro
    "CommissionRate": 0.1        ← e em percentual

Isso encaixa exatamente no que `finance/cotacao.Oferta` guarda, e quer dizer que
a comissão desta oferta NÃO cai no caso "sem IOF separado" — ela sai certa, do
líquido, sem estimativa nenhuma.

⚠️ E O CAMPO QUE PARECE O TOTAL E NÃO É. `GrossPremium` muda de significado entre
os dois exemplos da mesma página:

* no exemplo de proposta: `GrossPremium` 10.8 = COM imposto (BeforeVat 10 + Vat 0.8)
* no exemplo de endosso:  `GrossPremium` 300  = SEM imposto (Vat 24, DuePremium 324)

Ler o total de `GrossPremium` daria, no segundo caso, um prêmio 8% menor e uma
comissão 8% maior — errado nos dois sentidos, e silencioso. Por isso este
conector lê o total de `DuePremium`, e só cai em `BeforeVatPremium + Vat` quando
ele não vier. `GrossPremium` fica guardado no bruto e não manda em nada.

`TotalPremium` também não serve de total: no exemplo de endosso ele é 340.2 —
`DuePremium` 324 mais 16.2 de JUROS de parcelamento. Juro de financiamento não é
prêmio, e somá-lo inflaria a comissão do mesmo jeito.

════════════════════════════════════════════════════════════════════════════
A AUTENTICAÇÃO (página "Typical Policy Lifecycle API Hands-on"):

    POST {server}/cas/ebao/v2/json/tickets   ← usuário e senha, devolve o token

"The token is necessary to be appended to call any APIs via insureMO API
gateway." É CAS do eBao: usuário e senha, não client_id/secret — por isso o
conector tem caminho próprio e não usa o OAuth2 do `ProvedorHTTP`.

⚠️ O QUE A PÁGINA NÃO DIZ: o NOME do cabeçalho que leva o token ("appended" não
é nome de header). O padrão do gateway é `Authorization: Bearer <token>`, e é o
que este conector usa por padrão — mas está em variável de ambiente
(`COTACAO_INSUREMO_HEADER_TOKEN` e `_PREFIXO_TOKEN`), então confirmar isso na
coleção do Postman é trocar uma variável, não mexer em código. A coleção que a
doc manda pedir ao time da InsureMO mostra o cabeçalho de verdade.

O CICLO DA NOVA APÓLICE, na ordem da doc:

    /proposal/core/proposal/v1/createEx        monta a proposta pela config do produto
    /proposal/core/proposal/v1/updateEx        SALVA — e o primeiro save gera o NÚMERO
    /proposal/core/proposal/v1/validate        roda as regras de validação
    /proposal/core/proposal/v1/calculateEx     calcula prêmio, SI, imposto e taxas
    /proposal/core/proposal/v1/autoUnderWriting roda as regras de aceitação
    /proposal/core/proposal/v1/issuePolicyEx   EMITE a apólice, com versionamento
    /proposal/core/proposal/v1/load            carrega por número de proposta/apólice

A EMISSÃO DAQUI PARA NA PROPOSTA — e é decisão do dono (21/09/2026), não limite
técnico: "só proposta, com fallback". `emitir` faz validate → createEx → updateEx
e devolve o `ProposalNo`; **`issuePolicyEx` NÃO é chamado**. Emitir apólice em
nome da corretora é ato com responsabilidade e prêmio no meio, e o dono decidiu
que essa porta abre depois, de propósito. A linha está a um método de distância,
e é assim que deve ficar até ele dizer o contrário.

A VALIDAÇÃO VEM PRIMEIRO, e agora dá pra confiar nela: a doc da Quotation diz que
`validate` devolve **204 sem corpo** quando passa e **422 com as mensagens**
quando não — legíveis, campo a campo ("field EffectiveDate is mandatory"). Como
o `_post` levanta em status >= 300 com o corpo na mensagem, uma proposta recusada
chega na tela do corretor dizendo O QUE falta, e `enviar_para_emissao` cai no
roteiro pro portal. Era a única dúvida que restava do conector anterior.

POR QUE `calculate` (sem persistir) NA COTAÇÃO. A página Quotation dá três
caminhos: `calculate` (só preço), `create` (só salva) e `calculateWithPersistence`
(preço + salva + converte pra apólice). A cotação da tela usa o PRIMEIRO: quem
guarda a cotação é esta base, e "converter pra apólice" a cada preço consultado
criaria lixo no sistema da seguradora toda vez que o corretor compara opções.

════════════════════════════════════════════════════════════════════════════
O QUE AINDA FALTA DA DOC — e por que o conector recusa em vez de fingir:

1. **Os códigos do produto.** `ProductCode`, `ProductElementCode` do risco e das
   coberturas (no exemplo: TBTI, R10007, C100692) são de um produto de VIAGEM de
   um tenant específico. A doc é explícita: o modelo JSON sai do Data Dictionary
   e é criado por produto ("you must create your own policy model in data
   dictionary and add new fields to the models"). Para auto no Brasil esses
   códigos vêm da configuração da seguradora — não há campo de placa, chassi ou
   FIPE no schema genérico, e é por isso que `campos_veiculo` é um mapa.
2. **O que o `validate` devolve quando REPROVA.** O conector trata erro de HTTP
   como falha e guarda o corpo da resposta; uma reprovação que volte 200 com as
   mensagens dentro passaria batido. Fechar isso é ler "Policy Rule API".

UM PONTO DE ARQUITETURA QUE MUDA A TELA, e vale dizer alto: **esta API dá o preço
de UM produto de UM tenant.** Ela não é multicálculo — não existe uma chamada que
devolva Porto, Allianz e HDI lado a lado. O comparativo sai de N chamadas, uma
por produto configurado, e é por isso que `COTACAO_INSUREMO_PRODUTOS` é uma
LISTA: cada item vira uma `Oferta`, e a tela compara como sempre comparou.

A doc também avisa que existe um motor novo, "SPOCK", recomendado para apólice de
grupo. Quando for o caso, muda o caminho — não o mapeamento de campos daqui.

CONFIGURAÇÃO (ver `docs/API_COTACAO.md`):

    COTACAO_PROVEDOR=insuremo
    COTACAO_INSUREMO_BASE_URL=https://...
    COTACAO_INSUREMO_USUARIO=...            # CAS: usuário e senha da conta InsureMO
    COTACAO_INSUREMO_SENHA=...
    COTACAO_INSUREMO_API_KEY=...            # (alternativa) token fixo, pula o CAS
    COTACAO_INSUREMO_ORG=10002
    COTACAO_INSUREMO_AGENTE=XXXX00XX        # AgentCode da corretora
    COTACAO_INSUREMO_MOEDA=BRL
    COTACAO_INSUREMO_HEADER_TOKEN=Authorization   # o padrão; confirmar no Postman
    COTACAO_INSUREMO_PREFIXO_TOKEN='Bearer '      # vazio se o gateway não usar
    COTACAO_INSUREMO_PRODUTOS=[{...}]       # a lista, descrita em PRODUTO_EXEMPLO
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import date, datetime, timedelta

from .cotacao import Oferta
from .cotacao_provedores import ProvedorErro, ProvedorHTTP, registrar

_log = logging.getLogger("openclaw.cotacao.insuremo")

#: O CAS do eBao: usuário e senha, não client_id/secret.
CAMINHO_TICKET = "/cas/ebao/v2/json/tickets"

#: O ciclo da nova apólice, na ordem da doc. `issuePolicyEx` está aqui como
#: documentação do que existe — e NÃO é chamado (ver o cabeçalho).
CAMINHO_VALIDAR = "/proposal/core/proposal/v1/validate"
CAMINHO_CRIAR = "/proposal/core/proposal/v1/createEx"
CAMINHO_SALVAR = "/proposal/core/proposal/v1/updateEx"
CAMINHO_EMITIR_APOLICE = "/proposal/core/proposal/v1/issuePolicyEx"   # não usado

#: O formato de cada item de `COTACAO_INSUREMO_PRODUTOS`. Existe como constante
#: porque é o que a mensagem de erro mostra quando a configuração falta — dizer
#: "configure o produto" sem dizer COMO só adia a pergunta.
PRODUTO_EXEMPLO = {
    "codigo": "AUTO_BR",             # ProductCode
    "versao": "1.0",                 # ProductVersion
    "seguradora": "Nome da seguradora",   # é o que aparece na tela
    "risco": "R10007",               # ProductElementCode do risco
    "coberturas": [{"codigo": "C100692", "soma_segurada": 100000}],
    # o mapa do veículo: campo nosso -> nome do campo no produto do tenant.
    # Fica vazio por padrão de propósito: cada produto nomeia isto do seu jeito,
    # e chutar "LicensePlateNo" mandaria a placa pra um campo que não existe.
    "campos_veiculo": {},
}


def _produtos() -> list[dict]:
    bruto = (os.environ.get("COTACAO_INSUREMO_PRODUTOS") or "").strip()
    if not bruto:
        return []
    try:
        lista = json.loads(bruto)
    except ValueError as e:
        raise ProvedorErro(f"COTACAO_INSUREMO_PRODUTOS não é JSON válido: {e}")
    if not isinstance(lista, list):
        raise ProvedorErro("COTACAO_INSUREMO_PRODUTOS tem que ser uma LISTA de produtos")
    return lista


def _cent(v) -> int | None:
    """Reais (decimal) → centavos. A InsureMO manda número, não string.

    `None` continua `None`: é a diferença entre "o campo não veio" e "veio zero",
    e é ela que decide se a comissão pode ser estimada lá em `cotacao.Oferta`.
    """
    if v is None or v == "":
        return None
    try:
        from decimal import Decimal, ROUND_HALF_UP
        return int((Decimal(str(v)) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    except Exception:  # noqa: BLE001
        return None


class ProvedorInsureMO(ProvedorHTTP):
    """A Calculate API da InsureMO, um produto configurado por vez."""

    chave = "insuremo"
    nome = "InsureMO"
    #: Vai até a PROPOSTA (validate → createEx → updateEx), nunca até a apólice.
    suporta_emissao = True
    caminho_cotacao = "/quotation/core/quotation/v1/calculate"

    # ---------------------------------------------------------- autenticação
    def _pedir_ticket(self) -> tuple[str, float]:
        """Troca usuário e senha por token no CAS. Devolve (token, segundos de vida).

        Método próprio e não o OAuth2 do `ProvedorHTTP` porque o CAS do eBao pede
        usuário e SENHA — não client_id/secret. Fica isolado também pra ser o
        único ponto que os testes precisam trocar.
        """
        import httpx
        url = self._env("BASE_URL", obrigatoria=True).rstrip("/") + CAMINHO_TICKET
        corpo = {"username": self._env("USUARIO", obrigatoria=True),
                 "password": self._env("SENHA", obrigatoria=True)}
        with httpx.Client(timeout=self.timeout) as c:
            r = c.post(url, json=corpo, headers={"Content-Type": "application/json"})
        if r.status_code >= 300:
            raise ProvedorErro(f"InsureMO: CAS {r.status_code} — {r.text[:200]}")
        try:
            j = r.json()
        except ValueError:
            # alguns gateways devolvem o ticket cru, sem JSON à volta
            j = {}
            texto = (r.text or "").strip()
            if texto:
                return texto, 3600.0
        # a doc não fixa o nome do campo; estes são os que o gateway usa na prática
        for k in ("access_token", "token", "ticket", "tk", "Token", "TicketId"):
            if j.get(k):
                vida = float(j.get("expires_in") or j.get("expiresIn") or 3600)
                return str(j[k]), vida
        raise ProvedorErro(f"InsureMO: resposta do CAS sem token — {str(j)[:200]}")

    def _cabecalhos(self) -> dict:
        """Chave fixa manda; senão, CAS. O NOME do cabeçalho é configurável de
        propósito: a doc diz que o token é "appended", e isso não é nome de header
        (ver o cabeçalho do módulo). Confirmar na coleção do Postman é trocar uma
        variável de ambiente, não mexer em código."""
        h = {"Content-Type": "application/json", "Accept": "application/json"}
        nome = self._env("HEADER_TOKEN") or "Authorization"
        prefixo = os.environ.get("COTACAO_INSUREMO_PREFIXO_TOKEN")
        prefixo = "Bearer " if prefixo is None else prefixo
        chave = self._env("API_KEY")
        if not chave:
            agora = time.time()
            if not self._token or agora >= self._token_expira - 60:
                token, vida = self._pedir_ticket()
                self._token, self._token_expira = token, agora + vida
            chave = self._token
        h[nome] = f"{prefixo}{chave}"
        tenant = self._env("TENANT")
        if tenant:
            h["X-Tenant"] = tenant
        return h

    # ------------------------------------------------------------- o pedido
    def _envelope(self, risco: dict, produto: dict) -> dict:
        seg = risco.get("segurado") or {}
        cob = risco.get("cobertura") or {}
        moeda = self._env("MOEDA") or "BRL"
        inicio = cob.get("vigencia_inicio") or date.today().isoformat()
        try:
            d0 = datetime.strptime(inicio, "%Y-%m-%d").date()
        except ValueError:
            d0 = date.today()
        # 12 meses menos um dia: apólice de auto é anual, e o fim da vigência é a
        # véspera do aniversário — não o próprio dia, que daria 366 de cobertura.
        fim = (d0.replace(year=d0.year + 1) - timedelta(days=1)).isoformat()
        return {
            "ProductCode": produto["codigo"],
            "ProductVersion": produto.get("versao") or "1.0",
            "ProposalDate": date.today().isoformat(),
            "EffectiveDate": d0.isoformat(),
            "ExpiryDate": fim,
            "AgentCode": self._env("AGENTE"),
            "OrgCode": self._env("ORG"),
            "BookCurrencyCode": moeda,
            "PremiumCurrencyCode": moeda,
            "LocalCurrencyCode": moeda,
            "PremiumBookExchangeRate": 1,
            "PremiumLocalExchangeRate": 1,
            "PolicyLobList": [{
                "ProductCode": produto["codigo"],
                "TotalInsuredCount": 1,
                "PolicyRiskList": [self._risco(risco, produto, seg)],
            }],
        }

    def _risco(self, risco: dict, produto: dict, seg: dict) -> dict:
        """O objeto de risco. Os campos genéricos vêm do schema documentado; os do
        VEÍCULO entram pelo mapa do produto, porque o schema genérico não tem
        placa, chassi nem FIPE — quem os nomeia é a configuração do tenant."""
        r = {
            "CustomerName": seg.get("nome") or "",
            "IdNo": seg.get("cpf") or "",
            "IdType": produto.get("tipo_documento") or "1",
            "DateOfBirth": seg.get("nascimento") or "",
            "ProductElementCode": produto["risco"],
            "RiskName": seg.get("nome") or "Segurado",
            "PolicyCoverageList": [
                {"ProductElementCode": c["codigo"], "SumInsured": c.get("soma_segurada")}
                for c in (produto.get("coberturas") or [])
            ],
        }
        vei = risco.get("veiculo") or {}
        mapa = produto.get("campos_veiculo") or {}
        if not mapa:
            # sem o mapa, o preço volta pro risco BASE — sem veículo. Um preço de
            # auto calculado sem o carro é um número errado com cara de certo, e
            # é pior que erro nenhum: o corretor passa ele pro cliente.
            _log.warning("InsureMO: produto %s sem `campos_veiculo` — o veículo NÃO "
                         "vai no pedido e o prêmio não vale pra auto", produto["codigo"])
        for nosso, deles in mapa.items():
            valor = vei.get(nosso)
            if valor is not None and valor != "":
                r[deles] = valor
        return r

    def _payload(self, risco: dict) -> dict:
        # exigido pela interface do ProvedorHTTP; aqui a cotação é por produto,
        # então quem monta de verdade é `cotar`, que chama uma vez por produto
        produtos = _produtos()
        if not produtos:
            raise ProvedorErro(self._falta_config())
        return self._envelope(risco, produtos[0])

    def _falta_config(self) -> str:
        return ("InsureMO: falta COTACAO_INSUREMO_PRODUTOS. É uma LISTA JSON, um item "
                "por produto/seguradora — os códigos saem da configuração do tenant "
                "(a doc diz que campos e valores vêm da DataTable do projeto). "
                "Exemplo de um item: " + json.dumps(PRODUTO_EXEMPLO, ensure_ascii=False))

    # ------------------------------------------------------------ a resposta
    def _oferta_de(self, resposta: dict, produto: dict) -> Oferta | None:
        """Lê UMA resposta de `calculate` e devolve a oferta — ou None se a
        InsureMO disse que não calculou.

        `IsPremiumCalcSuccess != "Y"` é recusa, não zero: devolver uma oferta de
        R$ 0,00 poria "o mais barato" no topo do comparativo pra sempre.
        """
        if str(resposta.get("IsPremiumCalcSuccess") or "Y").upper() != "Y":
            _log.info("InsureMO: produto %s não calculou (IsPremiumCalcSuccess=%r)",
                      produto.get("codigo"), resposta.get("IsPremiumCalcSuccess"))
            return None
        liquido = _cent(resposta.get("BeforeVatPremium"))
        imposto = _cent(resposta.get("Vat"))
        # O TOTAL: `DuePremium` manda. Ver o cabeçalho — `GrossPremium` troca de
        # significado entre os exemplos da própria doc, e `TotalPremium` carrega
        # juro de parcelamento, que não é prêmio.
        total = _cent(resposta.get("DuePremium"))
        if total is None and liquido is not None and imposto is not None:
            total = liquido + imposto
        if total is None:
            total = _cent(resposta.get("GrossPremium"))
        if not total:
            return None
        # a InsureMO manda a comissão PRONTA: percentual (0.1 = 10%) e valor.
        pct = resposta.get("CommissionRate")
        try:
            pct = float(pct) * 100 if pct is not None else None
        except (TypeError, ValueError):
            pct = None
        coberturas = []
        for lob in resposta.get("PolicyLobList") or []:
            for r in lob.get("PolicyRiskList") or []:
                for c in r.get("PolicyCoverageList") or []:
                    coberturas.append({
                        "nome": c.get("CoverageName") or c.get("ProductElementCode"),
                        "soma_segurada": _cent(c.get("SumInsured")),
                        "premio_centavos": _cent(c.get("DuePremium")),
                    })
        return Oferta(
            seguradora=(produto.get("seguradora") or produto.get("codigo") or "InsureMO"),
            produto=str(resposta.get("ProductCode") or produto.get("codigo") or ""),
            premio_total_centavos=total,
            premio_liquido_centavos=liquido,
            iof_centavos=imposto,
            comissao_pct=pct,
            coberturas=coberturas,
            # o número da proposta é o que a emissão vai precisar mandar de volta
            ref_externa=(str(resposta.get("ProposalNo")) if resposta.get("ProposalNo") else None),
            bruto=resposta,
        )

    def _ofertas(self, resposta: dict) -> list[Oferta]:
        produtos = _produtos()
        o = self._oferta_de(resposta, produtos[0] if produtos else {})
        return [o] if o else []

    # ---------------------------------------------------------------- cotar
    def cotar(self, risco: dict) -> list[Oferta]:
        """Uma chamada POR PRODUTO — é assim que o comparativo nasce.

        A Calculate API precifica UM produto de UM tenant; não existe chamada que
        devolva várias seguradoras. Falha de um produto não derruba os outros: o
        corretor prefere três preços e um erro a nenhum preço.
        """
        produtos = _produtos()
        if not produtos:
            raise ProvedorErro(self._falta_config())
        achadas, erros = [], []
        for p in produtos:
            if not p.get("codigo") or not p.get("risco"):
                erros.append(f"produto sem 'codigo' ou 'risco': {p}")
                continue
            try:
                resposta = self._post(self.caminho_cotacao, self._envelope(risco, p))
                o = self._oferta_de(resposta, p)
                if o is not None:
                    achadas.append(o)
            except Exception as e:  # noqa: BLE001 — um produto não derruba os outros
                _log.warning("InsureMO: produto %s falhou: %s: %s",
                             p.get("codigo"), type(e).__name__, e)
                erros.append(f"{p.get('seguradora') or p.get('codigo')}: {e}")
        if not achadas and erros:
            raise ProvedorErro("; ".join(erros)[:400])
        return achadas

    # -------------------------------------------------------------- emissão
    def _produto_da_oferta(self, oferta: dict) -> dict:
        """Qual produto gerou a oferta escolhida. Casa pelo ProductCode e cai no
        nome da seguradora — a oferta guarda os dois, e errar o produto aqui
        mandaria a proposta pro plano errado da seguradora certa."""
        produtos = _produtos()
        if not produtos:
            raise ProvedorErro(self._falta_config())
        alvo_prod = (oferta.get("produto") or "").strip().lower()
        alvo_seg = (oferta.get("seguradora") or "").strip().lower()
        for p in produtos:
            if alvo_prod and (p.get("codigo") or "").strip().lower() == alvo_prod:
                return p
        for p in produtos:
            if alvo_seg and (p.get("seguradora") or "").strip().lower() == alvo_seg:
                return p
        raise ProvedorErro(
            f"InsureMO: a oferta escolhida ({oferta.get('seguradora') or '?'}) não "
            "casa com nenhum produto em COTACAO_INSUREMO_PRODUTOS")

    def emitir(self, risco: dict, oferta: dict) -> dict:
        """Manda os dados e devolve o NÚMERO DA PROPOSTA. Três passos, nesta ordem:

            validate  → recusa legível antes de criar lixo no sistema da seguradora
            createEx  → monta a proposta pela configuração do produto
            updateEx  → SALVA (é o que fixa a proposta e sobe a versão)

        E PARA AQUI. `issuePolicyEx` existe e não é chamado: emitir a apólice é
        decisão que o dono ainda não tomou (ver o cabeçalho do módulo).

        Qualquer falha sobe como `ProvedorErro` — e `cotacao.enviar_para_emissao`
        cai no roteiro pro portal, que é o caminho que sempre funciona. É por isso
        que este método pode ser rígido: o corretor nunca fica sem saída.
        """
        produto = self._produto_da_oferta(oferta)
        corpo = self._envelope(risco, produto)
        # 204 = passou; 422 traz as mensagens campo a campo, e o `_post` as põe na
        # exceção — que é o texto que o corretor lê na tela.
        self._post(CAMINHO_VALIDAR, corpo)
        criada = self._post(CAMINHO_CRIAR, corpo)
        numero = str(criada.get("ProposalNo") or "").strip()
        # o save devolve a proposta com a versão nova; o número é o mesmo, e é dele
        # que a carteira precisa (`apolices.numero_proposta`)
        salva = self._post(CAMINHO_SALVAR, criada) or {}
        numero = str(salva.get("ProposalNo") or numero or "").strip()
        if not numero:
            raise ProvedorErro("InsureMO: a proposta foi criada mas voltou sem "
                               "ProposalNo — não dá pra achá-la depois")
        _log.info("InsureMO: proposta %s criada para o produto %s",
                  numero, produto.get("codigo"))
        return {"numero_proposta": numero,
                "policy_id": salva.get("PolicyId") or criada.get("PolicyId"),
                "proposal_status": salva.get("ProposalStatus") or criada.get("ProposalStatus"),
                "bruto": salva or criada}


registrar(ProvedorInsureMO())
