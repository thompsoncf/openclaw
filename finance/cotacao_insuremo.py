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
O QUE AINDA FALTA DA DOC — e por que o conector recusa em vez de fingir:

1. **Autenticação.** A página usa `{{server}}` e não descreve token nem tenant.
   A mecânica está pronta em `ProvedorHTTP` (chave estática ou OAuth2
   client_credentials); falta saber QUAL das duas e quais cabeçalhos. Enquanto
   isso, `COTACAO_INSUREMO_API_KEY` ou o trio OAuth2 resolvem os dois casos.
2. **Os códigos do produto.** `ProductCode`, `ProductElementCode` do risco e das
   coberturas (no exemplo: TBTI, R10007, C100692) são de um produto de VIAGEM de
   um tenant específico. A própria doc diz, na página de integração, que campos e
   valores "são apenas sugestões de referência" e saem da DataTable do projeto.
   Para auto no Brasil, esses códigos vêm da configuração da seguradora — não há
   campo de placa, chassi ou FIPE no schema genérico.
3. **Emissão.** A página de rating não emite. A proposta persistida e a emissão
   estão em "Policy Persistence and Query API" e "Quotation", que ainda não
   li — por isso `suporta_emissao = False` e a emissão continua no roteiro pro
   portal (`cotacao.roteiro_do_portal`), que funciona hoje.

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
    COTACAO_INSUREMO_API_KEY=...            # ou o trio OAuth2 do ProvedorHTTP
    COTACAO_INSUREMO_ORG=10002
    COTACAO_INSUREMO_AGENTE=XXXX00XX        # AgentCode da corretora
    COTACAO_INSUREMO_MOEDA=BRL
    COTACAO_INSUREMO_PRODUTOS=[{...}]       # a lista, descrita em PRODUTO_EXEMPLO
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timedelta

from .cotacao import Oferta
from .cotacao_provedores import ProvedorErro, ProvedorHTTP, registrar

_log = logging.getLogger("openclaw.cotacao.insuremo")

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
    #: A emissão fica em outra página da doc, ainda não lida (ver o cabeçalho).
    #: False aqui é o que mantém o corretor no roteiro pro portal, que funciona.
    suporta_emissao = False
    caminho_cotacao = "/quotation/core/quotation/v1/calculate"

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


registrar(ProvedorInsureMO())
