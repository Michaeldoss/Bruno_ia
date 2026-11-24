import asyncio
from unittest.mock import AsyncMock, MagicMock, Mock, patch

from app.services import clube_resgate as service

CNPJ = "12345678000195"
VOUCHER = dict(cnpj=CNPJ, code="DOSS-RES-0123456789ABCDEF", mode="send",
               company="Empresa de teste", title="Tinta", sku="SKU1", color="Ciano",
               unit="UN", quantity=1, points=10)


def test_lookup_validates_identity_and_uses_crm_number():
    erp = Mock()
    erp.json.return_value = [{"cnpjCpf": "12.345.678/0001-95", "codigoVendedor": 17, "celular": "11111111111"}]
    client = Mock(get=AsyncMock(return_value=erp))
    with patch.object(service.uniplus_client, "_headers", AsyncMock(return_value={})), patch.object(
        service, "crm", AsyncMock(side_effect=[[{"seller_map": {"17": "user"}}],
            [{"id": "user", "name": "Teste"}], [{"phone_number": "(47) 90000-0000"}]])
    ):
        result = asyncio.run(service.resolve(client, CNPJ))
        assert result == {"state": "resolved", "seller": "Teste", "phone": "5547900000000"}
        assert asyncio.run(service.resolve(client, "22345678000195"))["reason"] == "customer"


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
            assert phone == "5547900000000" and VOUCHER["code"] in text and CNPJ in text
