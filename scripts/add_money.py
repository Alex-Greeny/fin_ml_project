import asyncio
from t_tech.invest.sandbox.async_client import AsyncSandboxClient
from t_tech.invest import MoneyValue
from main import TOKEN, ACCOUNT_ID

async def main():
    async with AsyncSandboxClient(TOKEN) as client:
        await client.sandbox.sandbox_pay_in(
            account_id=ACCOUNT_ID,
            amount=MoneyValue(units=100000, nano=0, currency='rub')
        )
        print("Баланс пополнен на 100 000 rub")

if __name__ == "__main__":
    asyncio.run(main())