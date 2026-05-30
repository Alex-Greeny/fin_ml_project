import asyncio
import uuid
from t_tech.invest import OrderDirection, OrderType, InstrumentIdType
from t_tech.invest.sandbox.async_client import AsyncSandboxClient
import os
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("TINKOFF_TOKEN")
ACCOUNT_ID = os.getenv("ACCOUNT_ID")

async def main():
    async with AsyncSandboxClient(TOKEN) as client:
        try:
            portfolio = await client.operations.get_portfolio(account_id=ACCOUNT_ID)
            
            target_positions = [
                pos for pos in portfolio.positions 
                if pos.instrument_type != "currency"
            ]

            if not target_positions:
                print("Портфель чист")
                return

            print(f"Найдено позиций для закрытия: {len(target_positions)}\n")

            for pos in target_positions:
                qty_pieces = pos.quantity.units + (pos.quantity.nano / 1e9)
                if qty_pieces == 0:
                    continue

                instrument_req = await client.instruments.get_instrument_by(
                    id_type=InstrumentIdType.INSTRUMENT_ID_TYPE_FIGI,
                    id=pos.figi,
                    class_code=""
                )
                lot_size = instrument_req.instrument.lot if instrument_req.instrument.lot > 0 else 1

                lots_to_close = int(abs(qty_pieces) // lot_size)

                direction = OrderDirection.ORDER_DIRECTION_SELL if qty_pieces > 0 else OrderDirection.ORDER_DIRECTION_BUY
                dir_text = "закрытие лонга" if direction == OrderDirection.ORDER_DIRECTION_SELL else "закрытие шорта)"

                print(f"Закрытие {pos.ticker} Действие: {dir_text} Объем: {lots_to_close}")

                await client.orders.post_order(
                    figi=pos.figi,
                    quantity=lots_to_close,
                    account_id=ACCOUNT_ID,
                    direction=direction,
                    order_type=OrderType.ORDER_TYPE_MARKET,
                    order_id=str(uuid.uuid4())
                )
                print(f"{pos.ticker} закрыт")

            print("Очистка завершена")
            
        except Exception as e:
            print(f"Ошибка API при очистке портфеля: {e}")

if __name__ == "__main__":
    asyncio.run(main())