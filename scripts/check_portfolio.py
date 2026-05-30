import asyncio
from t_tech.invest.sandbox.async_client import AsyncSandboxClient
import os
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("TINKOFF_TOKEN")
ACCOUNT_ID = os.getenv("ACCOUNT_ID")

def convert_money(value):
    if value is None: 
        return 0.0
    if hasattr(value, 'units') and hasattr(value, 'nano'):
        return float(value.units + value.nano / 10**9)
    return 0.0

async def main():
    async with AsyncSandboxClient(TOKEN) as client:
        try:
            portfolio = await client.operations.get_portfolio(account_id=ACCOUNT_ID)
            withdraw_limits = await client.operations.get_withdraw_limits(account_id=ACCOUNT_ID)
            
            total_amount = convert_money(portfolio.total_amount_portfolio)
            raw_balance = convert_money(withdraw_limits.money[0]) if withdraw_limits.money else 0.0

            poses = []
            for pos in portfolio.positions:
                qty = pos.quantity.units + (pos.quantity.nano / 1e9) if pos.quantity else 0.0
                price = convert_money(pos.current_price)
                poses.append({
                    'ticker': pos.ticker,
                    'figi': pos.figi,
                    'quantity': qty,
                    'current_price': price,
                    'type': pos.instrument_type 
                })

            short_poses_amount = sum(abs(pos['quantity'] * pos['current_price']) 
                        for pos in poses if pos['quantity'] < 0)
            cur_balance = raw_balance - short_poses_amount
            
            print(f"Общий депозит (с позициями): {round(total_amount, 2)} руб")
            print(f"Свободный кэш (без учета шортов): {round(raw_balance, 2)} руб")
            print(f"Свободный кэш: {round(cur_balance, 2)} руб")
            print("Открытые позиции:")

            assets = [p for p in poses if p['type'] != 'currency']
            
            if not assets:
                print("   [Открытых сделок нет]")
            else:
                for p in assets:
                    direction = "SHORT" if p['quantity'] < 0 else "LONG "
                    print(f" {direction} {p['ticker']} (FIGI: {p['figi']}) Кол-во: {p['quantity']} Цена: {p['current_price']} руб")
            
        except Exception as e:
            print(f"Ошибка API: {e}")

if __name__ == "__main__":
    asyncio.run(main())