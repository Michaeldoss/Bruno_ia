import asyncio
from unittest.mock import AsyncMock, MagicMock, Mock, patch

from app.services import clube_resgate as service

CNPJ = "12345678000195"
VOUCHER = dict(cnpj=CNPJ, code="DOSS-RES-0123456789ABCDEF", mode="send",
               company="Empresa de teste", title="Tinta", description="Tinta sublimática Premium de 1 litro.", sku="008136", color="Ciano",
               unit="UN", quantity=1, points=10)


def resolve_case(code, contacts, *, cnpj=CNPJ, active=None):
    michael, david = service.RECEIVERS
    erp = Mock()
    erp.json.return_value = [{"cnpjCpf": "12.345.678/0001-95", "codigoVendedor": code}]
    client = Mock(get=AsyncMock(return_value=erp))

    async def read_crm(_client, table, fields, filters):
        if table == "crm_uniplus_sync":
            return [{"seller_map": {"315": michael, "1743": david, "205": "other"}}]
        allowed = "in.(" + ",".join(service.RECEIVERS) + ")"
        if table == "profiles":
            assert filters == {"id": allowed, "is_active": "eq.true"}
            names = {michael: "Michaelsin", david: "David"}
            return [{"id": a, "name": names[a]} for a in (service.RECEIVERS if active is None else active)]
        assert table == "whatsapp_instances" and filters == {"agent_id": allowed}
        return [{"agent_id": a, "phone_number": number} for a, number in contacts]
    with patch.object(service.uniplus_client, "_headers", AsyncMock(return_value={})), patch.object(service, "crm", read_crm):
        return asyncio.run(service.resolve(client, cnpj))


def test_restricts_delivery_to_two_profiles_and_keeps_customer_validation():
    michael, david = service.RECEIVERS
    contacts = [(michael, "(47) 90000-0000"), (david, "(47) 90000-0001"), ("other", "(47) 90000-0002")]
    assert resolve_case(315, contacts) == {"state": "resolved", "seller": "Michaelsin", "phone": "5547900000000"}
    assert resolve_case(1743, contacts) == {"state": "resolved", "seller": "David", "phone": "5547900000001"}
    for code in (205, 9017, None):
        route = resolve_case(code, contacts)
        assert route["seller"] == "Michaelsin" and route["handoff"]
        if code:
            assert str(code) in route["handoff"]
    assert resolve_case(1743, contacts[:1])["seller"] == "Michaelsin"
    assert resolve_case(315, contacts[1:])["seller"] == "David"
    assert resolve_case(205, contacts, active=[david])["seller"] == "David"
    assert resolve_case(205, contacts, active=[])["reason"] == "mapping"
    assert resolve_case(205, contacts[2:])["reason"] == "phone"
    assert resolve_case(315, contacts + [(michael, "(47) 90000-0003")])["seller"] == "David"
    assert resolve_case(315, contacts, cnpj="22345678000195")["reason"] == "customer"


def test_missing_route_never_sends_and_acceptance_is_not_delivery():
    sender = AsyncMock(return_value="SM" + "a" * 32)
    with patch.object(service.httpx, "AsyncClient", MagicMock()), patch.object(service.twilio_service, "send_whatsapp_message", sender), patch.object(service.twilio_service, "client", Mock()):
        with patch.object(service, "resolve", AsyncMock(return_value=service.pending("phone"))):
            assert asyncio.run(service.handle_voucher(VOUCHER))["reason"] == "phone"
            sender.assert_not_called()
        with patch.object(service, "resolve", AsyncMock(return_value={"state":"resolved", "seller":"Teste", "phone":"5547900000000"})):
            result = asyncio.run(service.handle_voucher(VOUCHER))
            assert result["state"] == "accepted"
            phone, text = sender.await_args.args
            assert VOUCHER["description"] in text and "Código do produto (ERP): 008136" in text
            assert phone == "5547900000000" and VOUCHER["code"] in text and CNPJ in text


def test_template_keeps_full_product_and_separate_codes():
    template_send = AsyncMock(return_value="SM" + "b" * 32)
    route = {"state":"resolved", "seller":"Teste", "phone":"5547900000000"}
    with patch.dict("os.environ", {"DOSS_CLUBE_RESGATE_TEMPLATE_SID": "HX-test"}), patch.object(
        service.httpx, "AsyncClient", MagicMock()
    ), patch.object(service, "resolve", AsyncMock(return_value=route)), patch.object(
        service.twilio_service, "client", Mock()
    ), patch.object(service.twilio_service, "send_whatsapp_template_message", template_send):
        assert asyncio.run(service.handle_voucher(VOUCHER))["state"] == "accepted"
        fields = template_send.await_args.args[2]
        assert fields["3"] == VOUCHER["code"] and fields["5"] == VOUCHER["sku"]
        assert fields["4"] == VOUCHER["title"] + " — " + VOUCHER["description"]
