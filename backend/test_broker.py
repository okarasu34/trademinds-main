import asyncio
from brokers.base_adapter import get_broker_adapter
from db.database import AsyncSessionLocal
from db.models import BrokerAccount
from sqlalchemy import select

async def test():
    async with AsyncSessionLocal() as db:
        r = await db.execute(select(BrokerAccount).where(BrokerAccount.is_active == True).limit(1))
        account = r.scalar_one_or_none()
        if not account:
            print('No broker account')
            return
        print(f'Broker: {account.name} | type: {account.broker_type}')
        adapter = get_broker_adapter(account)
        result = await adapter.connect()
        print(f'Connected: {result}')

asyncio.run(test())
