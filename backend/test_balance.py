import asyncio
from brokers.base_adapter import get_broker_adapter
from db.database import AsyncSessionLocal
from db.models import BrokerAccount
from sqlalchemy import select

async def test():
    async with AsyncSessionLocal() as db:
        r = await db.execute(select(BrokerAccount).limit(1))
        account = r.scalar_one_or_none()
        adapter = get_broker_adapter(account)
        await adapter.connect()
        info = await adapter.get_account_info()
        print(f'balance={info.balance} equity={info.equity} currency={info.currency}')
        # Raw API response
        async with adapter.session.get(
            f"{adapter.BASE_URL}/accounts",
            headers=adapter._get_headers()
        ) as resp:
            data = await resp.json()
            print(f'raw={data}')

asyncio.run(test())
