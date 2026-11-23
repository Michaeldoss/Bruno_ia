import io
import zipfile
from unittest.mock import patch
import pytest
from fastapi import HTTPException
from starlette.requests import Request
from app.api.clube_fiscal import authorize, pilot_documents, NFE

def request(key):
    return Request({"type":"http","headers":[(b"x-clube-fiscal",key.encode())]})

def xml(env,issuer):
    ns=NFE[1:-1]
    return f'<nfeProc xmlns="{ns}"><NFe><infNFe><ide><tpAmb>{env}</tpAmb></ide><emit><CNPJ>{issuer}</CNPJ></emit><dest/></infNFe></NFe></nfeProc>'.encode()

def test_pilot_scope_and_private_credential():
    with patch.dict("os.environ",{"DOSS_CLUBE_FISCAL_KEY":"a"*48}),patch("app.api.clube_fiscal.is_pull_request_preview",return_value=False):
        with pytest.raises(HTTPException): authorize(request("incorrect"))
        authorize(request("a"*48))
    archive=io.BytesIO()
    with zipfile.ZipFile(archive,"w") as out:
        out.writestr("production.xml",xml(1,"45024640000170"))
        out.writestr("other.xml",xml(2,"11222333000181"))
        out.writestr("example.xml",xml(2,"45024640000170"))
    with zipfile.ZipFile(io.BytesIO(pilot_documents(archive.getvalue()))) as result:
        assert len(result.namelist())==1
        assert result.read(result.namelist()[0])==xml(2,"45024640000170")
