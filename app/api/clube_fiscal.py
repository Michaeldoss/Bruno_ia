"""Read fiscal exports from the Doss tenant for the private loyalty pilot."""
import hashlib
import hmac
import io
import json
import os
import re
import zipfile
from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from app.services import uniplus_client
from app.services.runtime_guard import is_pull_request_preview

router = APIRouter(prefix="/api/clube-fiscal")
BASE = "https://vzan-getcard01.getcard.uniplusweb.com"
LIST = BASE + "/public-api/v1/arquivos/buscar"
NFE = "{http://www.portalfiscal.inf.br/nfe}"
NO_STORE = {"Cache-Control": "private, no-store"}


def authorize(request):
    secret = os.environ.get("DOSS_CLUBE_FISCAL_KEY", "")
    given = request.headers.get("x-clube-fiscal", "")
    if is_pull_request_preview() or len(secret) < 32 or not hmac.compare_digest(given.encode(), secret.encode()):
        raise HTTPException(403, "Acesso fiscal restrito.")


async def read_bytes(client, url, headers, limit, params=None):
    async with client.stream("GET", url, headers=headers, params=params) as response:
        if response.status_code != 200:
            raise HTTPException(502, "Uniplus fiscal HTTP " + str(response.status_code))
        data = bytearray()
        async for block in response.aiter_bytes():
            if len(data) + len(block) > limit:
                raise HTTPException(422, "Exportação fiscal acima do limite.")
            data.extend(block)
        return bytes(data)


async def manifest(client):
    if uniplus_client.UNIPLUS_BASE_URL.rstrip("/") != BASE:
        raise HTTPException(503, "Filial da integração não confirmada.")
    headers = await uniplus_client._headers()
    raw = await read_bytes(client, LIST, headers, 2_000_000, {"tipo": "DOCUMENTO_FISCAL"})
    rows = json.loads(raw)
    if not isinstance(rows, list):
        raise HTTPException(502, "Formato da exportação fiscal não reconhecido.")
    files = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("invalid_manifest")
        name = row.get("nome") or row.get("nomeArquivo") or row.get("name")
        date = row.get("data") or row.get("dataArquivo") or row.get("date")
        link = row.get("url") or row.get("urlDownload")
        if not all(isinstance(x,str) and x for x in (name,date,link)):
            raise HTTPException(502, "Campos da exportação fiscal não reconhecidos.")
        url = urljoin(BASE + "/", link)
        parts = urlsplit(url)
        allowed = {BASE, *[x.strip() for x in os.getenv("DOSS_CLUBE_FILE_ORIGINS", "").split(",") if x.strip()]}
        if parts.scheme != "https" or parts.username or parts.password or "https://"+parts.netloc not in allowed:
            raise HTTPException(502, "Origem do servidor de arquivos da filial pendente de confirmação.")
        key = hashlib.sha256(json.dumps([name,date,parts.netloc,parts.path]).encode()).hexdigest()
        files[key] = (date,url)
    return files,headers


def pilot_documents(raw):
    """Only homologation documents are allowed to leave the ERP bridge."""
    output = io.BytesIO()
    def include(zout, data, index):
        if len(data)>1_048_576 or re.search(br'<!\s*(?:DOCTYPE|ENTITY)',data,re.I) or b'\x00' in data:
            raise ValueError("invalid_xml")
        root = ElementTree.fromstring(data)
        if root.tag == NFE+"nfeProc":
            info = root.find(NFE+"NFe/"+NFE+"infNFe")
            if info is None or info.findtext(NFE+"ide/"+NFE+"tpAmb")!="2": return
            involved = [info.findtext(k+"/"+NFE+"CNPJ") for k in (NFE+"emit",NFE+"dest")]
            if "45024640000170" not in involved: return
        elif root.tag == NFE+"procEventoNFe":
            info = root.find(NFE+"evento/"+NFE+"infEvento")
            if info is None or info.findtext(NFE+"tpAmb")!="2" or info.findtext(NFE+"tpEvento")!="110111": return
        else: return
        zout.writestr(str(index).zfill(6)+".xml",data)

    with zipfile.ZipFile(output,"w",zipfile.ZIP_DEFLATED) as zout:
        if raw.startswith(b"PK"):
            with zipfile.ZipFile(io.BytesIO(raw)) as zin:
                members = [m for m in zin.infolist() if not m.is_dir()]
                if any(m.filename.lower().endswith(".zip") for m in members): raise ValueError("nested_zip")
                docs = [m for m in members if m.filename.lower().endswith(".xml")]
                if len(docs)>2000 or sum(m.file_size for m in docs)>20_000_000: raise ValueError("archive_too_large")
                for i,m in enumerate(docs):
                    if m.file_size>1_048_576: raise ValueError("xml_too_large")
                    with zin.open(m) as stream: include(zout,stream.read(1_048_577),i)
        else:
            include(zout,raw,0)
    return output.getvalue()


@router.get("/arquivos")
@router.get("/arquivos/{file_id}")
async def fiscal_files(request:Request,file_id:str=None):
    authorize(request)
    try:
        async with httpx.AsyncClient(timeout=15,follow_redirects=False) as client:
            files,headers = await manifest(client)
            if file_id is None:
                return JSONResponse([{"id":k,"date":v[0]} for k,v in files.items()],headers=NO_STORE)
            if file_id not in files: raise HTTPException(404,"Lote indisponível.")
            url=files[file_id][1]
            auth=headers if urlsplit(url).netloc==urlsplit(BASE).netloc else {}
            raw=await read_bytes(client,url,auth,12_582_912)
            return Response(pilot_documents(raw),media_type="application/zip",headers=NO_STORE)
    except HTTPException:
        raise
    except (ValueError,ElementTree.ParseError,zipfile.BadZipFile):
        raise HTTPException(502,"Confira o formato da exportação fiscal da filial.") from None
    except Exception:
        raise HTTPException(503,"Não foi possível consultar a origem fiscal do Uniplus.") from None
