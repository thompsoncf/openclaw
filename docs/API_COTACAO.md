# Cotação de seguro — a API, o conector e o que falta pra plugar um provedor

Escrito em 21/09/2026, com a entrega das migrações 304/305.

## O que existe

Três portas, um miolo só (`finance/cotacao.py`). Nenhuma delas tem regra própria —
duas implementações seriam dois preços pro mesmo risco.

| Porta | Onde | Pra quem |
|---|---|---|
| Tela | `/painel/cotacoes` (`web/painel_cotacao.py`) | corretor, gestor, dono |
| API | `POST /api/v1/cotacoes` (`web/api_cotacao.py`) | o site/landing/parceiro da corretora |
| WhatsApp | ferramentas `cotar_seguro` / `ver_cotacao` (`finance/cotacao_tools.py`) | o corretor, no fio em que já trabalha |

O caminho é sempre o mesmo: **risco → ofertas → escolher → proposta na carteira**
(`apolices`, migração 278, situação `proposta`).

## A API pública

Autenticação por chave da conta, emitida em **Cotações › Chaves da API** (só
gerência). O banco guarda o `sha256`; o token aparece **uma vez**.

```bash
curl -X POST https://<host>/api/v1/cotacoes \
  -H "Authorization: Bearer zaq_cot_..." \
  -H "Content-Type: application/json" \
  -d '{
    "nome": "Fulano de Tal",
    "cpf": "52998224725",
    "nascimento": "1985-04-12",
    "telefone": "5586999998888",
    "cep": "64000000",
    "placa": "ABC1D23",
    "marca_modelo": "FIAT ARGO 1.0",
    "ano_modelo": 2022,
    "uso": "particular",
    "garagem": "residencia",
    "bonus": 5
  }'
```

**O mínimo**: `cpf`, `nascimento`, `cep` e o veículo por **um** destes: `fipe`,
`placa`, ou `marca_modelo` + `ano_modelo`. Sem isso nenhum provedor brasileiro dá
preço, e a API devolve `422` com a frase do que falta, em português.

Resposta (`201`):

```json
{
  "id": 42, "situacao": "cotada", "ramo": "auto", "erro": null,
  "ofertas": [
    {"seguradora": "HDI", "produto": "Auto compreensiva",
     "premio_total_centavos": 280000, "premio_liquido_centavos": 260500,
     "iof_centavos": 19500, "franquia_centavos": 250000, "parcelas": 10,
     "coberturas": [], "validade": null}
  ]
}
```

Três coisas que o integrador precisa saber:

1. **`201` mesmo quando o provedor falha.** Aí vem `"situacao": "falhou"` e um
   `erro` genérico. A cotação existe na conta da corretora — é um lead, e
   devolver `500` faria o site apagar o que ela acabou de ganhar.
2. **Três coisas ficam dentro do sistema**: a comissão (é quanto a corretora
   ganha, e o segurado não vê isso em lugar nenhum do mercado), o nome do
   provedor (é fornecedor dela) e o texto cru do erro, que costuma nomear o
   multicálculo e devolver pedaço da resposta dele. O corretor vê os três na
   tela; o visitante do site, nenhum.
3. **A API não escolhe oferta nem emite.** Escolher é ato comercial, com comissão
   e responsabilidade — acontece na tela de quem responde por ele.

`GET /api/v1/cotacoes/{id}` lê a mesma cotação (mesma chave). Cotação de outra
conta responde `404`, não `403`: `403` já contaria que ela existe.

## O conector

`finance/cotacao_provedores.py`. O padrão é `manual` — sem API contratada, o
corretor digita as ofertas na tela e o resto do fluxo é idêntico. Trocar é
variável de ambiente:

```
COTACAO_PROVEDOR=segfy
COTACAO_SEGFY_BASE_URL=https://...
COTACAO_SEGFY_API_KEY=...            # ou o trio OAuth2 abaixo
COTACAO_SEGFY_TOKEN_URL=...
COTACAO_SEGFY_CLIENT_ID=...
COTACAO_SEGFY_CLIENT_SECRET=...
COTACAO_SEGFY_TENANT=...             # quando o provedor exigir
```

Um conector novo é uma subclasse de `ProvedorHTTP` com dois métodos —
`_payload(risco)` e `_ofertas(resposta)` — e `registrar(MeuProvedor())`. A
mecânica comum (token, cabeçalhos, tempo limite, erro) já está na base.

## InsureMO — o que já está ligado, e o que falta

Em 21/09/2026 a página **Policy Rating API** foi lida (colada à mão: o domínio
está bloqueado pela política de egresso desta máquina). Com ela, o conector
`finance/cotacao_insuremo.py` existe e cobre a cotação.

### A chamada que dá preço

```
POST {server}/quotation/core/quotation/v1/calculate   (sem persistir cotação)
POST {server}/proposal/core/proposal/v1/calculateEx   (sem persistir proposta)
```

O pedido é um objeto de apólice (`ProductCode`, vigência, moeda, `OrgCode`,
`AgentCode`) com o risco em `PolicyLobList[].PolicyRiskList[]`. A resposta é o
mesmo objeto com os prêmios preenchidos em cada nível.

### O mapeamento do dinheiro — e a armadilha

A InsureMO **separa o imposto**, então a comissão sai exata, sem estimativa:

| Campo da InsureMO | Vira | Observação |
|---|---|---|
| `BeforeVatPremium` | `premio_liquido_centavos` | é sobre ele que a comissão incide |
| `Vat` | `iof_centavos` | o imposto |
| `DuePremium` | `premio_total_centavos` | **o total** |
| `CommissionRate` | `comissao_pct` | `0.1` = 10% |
| `ProposalNo` | `ref_externa` | o que a emissão manda de volta |

⚠️ **Não leia o total de `GrossPremium`.** Na mesma página da doc ele troca de
significado: 10.8 **com** imposto no exemplo de proposta, 300 **sem** imposto no
de endosso (onde `Vat` é 24 e `DuePremium` 324). Lê-lo daria prêmio 8% menor e
comissão 8% maior, sem nenhum sinal na tela. `TotalPremium` também não serve:
traz o **juro de parcelamento** (340.2 = 324 + 16.2), e juro não é prêmio.

`tests/test_cotacao_insuremo.py` prende isso com as amostras da própria doc.

### Não é multicálculo — e isso muda a configuração

A Calculate API precifica **um produto de um tenant**. Não existe chamada que
devolva Porto, Allianz e HDI lado a lado. Por isso
`COTACAO_INSUREMO_PRODUTOS` é uma **lista**: o conector faz uma chamada por
produto e cada uma vira uma oferta; falha de um não derruba os outros.

```
COTACAO_PROVEDOR=insuremo
COTACAO_INSUREMO_BASE_URL=https://...
COTACAO_INSUREMO_API_KEY=...          # ou o trio OAuth2 (TOKEN_URL/CLIENT_ID/CLIENT_SECRET)
COTACAO_INSUREMO_ORG=10002
COTACAO_INSUREMO_AGENTE=...           # AgentCode da corretora
COTACAO_INSUREMO_MOEDA=BRL
COTACAO_INSUREMO_PRODUTOS=[{"codigo":"AUTO_BR","versao":"1.0","seguradora":"...",
  "risco":"R10007","coberturas":[{"codigo":"C100692","soma_segurada":100000}],
  "campos_veiculo":{"placa":"LicensePlateNo","fipe":"FipeCode","ano_modelo":"ModelYear"}}]
```

### O que ainda falta da doc

1. **Autenticação.** A página usa `{{server}}` e não descreve token nem tenant.
   A mecânica dos dois modelos já está em `ProvedorHTTP`; falta saber qual e
   quais cabeçalhos. → páginas **Introduction to Policy API** / Getting Started.
2. **Os códigos do produto de auto.** `ProductCode`, o `ProductElementCode` do
   risco e os das coberturas saem da configuração do tenant — a doc diz
   explicitamente que campos e valores vêm da DataTable do projeto. O schema
   genérico não tem placa, chassi nem FIPE: por isso `campos_veiculo` é um mapa,
   e sem ele o conector **avisa no log** que o prêmio não vale pra auto.
3. **Emissão.** A página de rating não emite. → **Policy Persistence and Query
   API** e **Quotation**. Até lá, `suporta_emissao = False` e a emissão segue no
   roteiro pro portal.

## Emissão: o que o botão faz hoje

Onde o provedor aceita receber os dados, manda e guarda o número da proposta.
Onde não aceita — que é o caso de toda seguradora brasileira pro corretor hoje —
`cotacao.roteiro_do_portal` monta o resumo pronto pra digitar no portal dela, na
ordem em que os portais perguntam. Os dois caminhos terminam na MESMA carteira:
não existe "carteira das automáticas" e outra das manuais.
