"""Custo do WhatsApp por campanha: tarifa (finance/wa_precos) e leitura do pricing
real do webhook de status da Cloud API (finance/meta_msg.parse_status_whatsapp)."""
from finance import meta_msg, wa_precos


def test_custo_marketing_brl():
    assert abs(wa_precos.custo_brl("marketing", True) - 0.3217) < 1e-9


def test_custo_utility_brl():
    assert abs(wa_precos.custo_brl("utility", True) - 0.0350) < 1e-9


def test_custo_gratis_quando_nao_cobravel():
    # FEP / dentro da janela: billable=false → custo zero, seja qual for a categoria
    assert wa_precos.custo_brl("marketing", False) == 0.0
    assert wa_precos.custo_brl("utility", False) == 0.0


def test_custo_categoria_desconhecida_cai_em_marketing():
    # conservador: não subestima o custo
    assert wa_precos.custo_brl("", True) == wa_precos.custo_brl("marketing", True)
    assert wa_precos.custo_brl(None, True) == wa_precos.custo_brl("marketing", True)


def _payload(statuses):
    return {"object": "whatsapp_business_account",
            "entry": [{"changes": [{"value": {"statuses": statuses}}]}]}


def test_parse_status_cobravel():
    out = meta_msg.parse_status_whatsapp(_payload([
        {"id": "wamid.ABC", "status": "sent",
         "pricing": {"billable": True, "category": "marketing",
                     "pricing_model": "PMP", "type": "regular"}}]))
    assert len(out) == 1
    assert out[0]["sid"] == "wamid.ABC"
    assert out[0]["categoria"] == "marketing"
    assert out[0]["cobravel"] is True


def test_parse_status_gratis():
    out = meta_msg.parse_status_whatsapp(_payload([
        {"id": "wamid.X", "status": "delivered",
         "pricing": {"billable": False, "category": "marketing",
                     "type": "free_customer_service"}}]))
    assert out[0]["cobravel"] is False


def test_parse_status_sem_pricing_ignora():
    # status sem objeto pricing não diz nada de custo → não entra na lista
    assert meta_msg.parse_status_whatsapp(_payload([
        {"id": "wamid.Y", "status": "read"}])) == []


def test_parse_status_ignora_outro_objeto():
    assert meta_msg.parse_status_whatsapp({"object": "instagram", "entry": []}) == []
