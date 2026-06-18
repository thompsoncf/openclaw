# Funil do Zaq — Queries pro Supabase SQL Editor

Salve estas como "queries salvas" no Supabase SQL Editor. Funcionam após migração 023_leads.sql.

O ciclo de pagamento usa `contas.status`:
- trial → ativa (pagou) → inadimplente → suspensa → cancelada
- "virou pagante" = `status='ativa'`

---

## 1) Funil completo (métrica principal)
Visitante → testou → cadastrou → pagou

```sql
with f as (
  select
    count(*)                                              as visitantes,
    count(*) filter (where l.gastos_usados > 0)           as testaram,
    count(*) filter (where l.virou_conta)                 as cadastraram,
    count(*) filter (where c.status = 'ativa')            as pagaram
  from leads l
  left join contas c on c.id = l.conta_id
)
select
  visitantes,
  testaram,
  cadastraram,
  pagaram,
  round(100.0 * testaram   / nullif(visitantes,0), 1) as pct_testou,
  round(100.0 * cadastraram/ nullif(visitantes,0), 1) as pct_cadastrou,
  round(100.0 * pagaram    / nullif(visitantes,0), 1) as pct_pagou
from f;
```

---

## 2) Funil por canal (WhatsApp vs Telegram)

```sql
select
  l.canal,
  count(*)                                       as visitantes,
  count(*) filter (where l.gastos_usados > 0)    as testaram,
  count(*) filter (where l.virou_conta)          as cadastraram,
  count(*) filter (where c.status = 'ativa')     as pagaram
from leads l
left join contas c on c.id = l.conta_id
group by l.canal
order by visitantes desc;
```

---

## 3) Leads recentes (acompanhar dia a dia)

```sql
select id, canal, identificador, gastos_usados, virou_conta,
       conta_id, criado_em, ultimo_em
from leads
order by criado_em desc
limit 100;
```

---

## 4) Saúde das contas (trial vs pagante vs perdido)
Independe de leads — vê estado de TODAS as contas reais.

```sql
select status, count(*)
from contas
where nome <> '__degustacao_visitantes__'     -- ignora a conta-degustação
group by status
order by count(*) desc;
```

---

## 5) Trials vencendo nos próximos 3 dias
Pra agir antes de perder.

```sql
select id, nome, plano, vencimento
from contas
where status = 'trial'
  and vencimento between current_date and current_date + 3
  and nome <> '__degustacao_visitantes__'
order by vencimento;
```

---

## Notas

**Degustação:** Conta isolada `__degustacao_visitantes__` (status 'degustacao') não conta como cliente real.

**Leads viros pagantes:** Quando o Asaas entrar (futuro), o webhook de pagamento chamará:
```python
contas.ativar(pool, conta_id, dias=30, plano=...)
# → conta vira status='ativa'
```
A query 1 já contará automaticamente. Único cuidado: garantir que `virou_conta=true` e `conta_id` preenchido no lead no momento do cadastro no portal.

**Histórico:** Se precisar de análise temporal (conversões por semana, etc), adicione `date_trunc('week', l.criado_em)` ao group by.
