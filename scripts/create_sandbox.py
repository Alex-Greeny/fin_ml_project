import asyncio
import os
from dotenv import load_dotenv
from t_tech.invest import MoneyValue
from t_tech.invest.sandbox.async_client import AsyncSandboxClient

load_dotenv()
TINKOFF_TOKEN = os.getenv("TINKOFF_TOKEN")

async def main():
    async with AsyncSandboxClient(TINKOFF_TOKEN) as client:
        accounts_resp = await client.sandbox.get_sandbox_accounts()
        accounts = accounts_resp.accounts
        
        print(f"Найдено счетов: {len(accounts)}")
        
        for acc in accounts:
            await client.sandbox.close_sandbox_account(account_id=acc.id)
            print(f"Счет {acc.id} закрыт")

        new_account = await client.sandbox.open_sandbox_account()
        account_id = new_account.account_id
        print(f"Новый ACCOUNT_ID: {account_id}")

        await client.sandbox.sandbox_pay_in(
            account_id=account_id,
            amount=MoneyValue(currency="rub", units=100000, nano=0)
        )
        print("Баланс пополнен на 100 000")

if __name__ == "__main__":
    asyncio.run(main())