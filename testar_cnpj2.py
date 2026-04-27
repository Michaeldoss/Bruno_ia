import asyncio, sys, json
sys.path.insert(0, '.')
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.services.serasa_client import consultar_cnpj, format_serasa_summary
    data = await consultar_cnpj('45024640000170')
    print(json.dumps(data, ensure_ascii=False, indent=2)[:800])
    print()
    print(format_serasa_summary(data))

asyncio.run(main())
