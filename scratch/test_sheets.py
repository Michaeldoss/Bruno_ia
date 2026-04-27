import asyncio
import os
import sys
from dotenv import load_dotenv
import json

load_dotenv()
sys.path.append(os.getcwd())

from app.services.sheets_client import sheets_service

async def test_sheets():
    print("Iniciando busca no Sheets...")
    machines = await sheets_service.get_machines()
    print("Total de maquinas encontradas:", len(machines))
    if machines:
        print("Primeira maquina:", json.dumps(machines[0], indent=2))

asyncio.run(test_sheets())
