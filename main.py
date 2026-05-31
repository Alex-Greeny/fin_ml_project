import asyncio
import uuid
import re
import joblib
import torch
import datetime
import logging
from transformers import AutoTokenizer, AutoModelForSequenceClassification
from scipy.sparse import csr_matrix, hstack
from t_tech.invest import InstrumentStatus, OrderDirection, OrderType, Quotation, CandleInterval
from t_tech.invest.sandbox.async_client import AsyncSandboxClient
from telethon import TelegramClient, events
import python_socks
import os
from dotenv import load_dotenv
from database import add_trade, get_open_trades, close_trade

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.FileHandler("bot_activity.log", encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telethon").setLevel(logging.WARNING)
logging.getLogger("t_tech").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

load_dotenv()

TOKEN = os.getenv("TINKOFF_TOKEN")
ACCOUNT_ID = os.getenv("ACCOUNT_ID")
TG_API_ID = int(os.getenv("TG_API_ID"))
TG_API_HASH = os.getenv("TG_API_HASH")
TARGET_CHANNEL = "testbotforml"

proxy_dict = {
    'proxy_type': python_socks.ProxyType.SOCKS5,
    'addr': os.getenv('PROXY_IP'),
    'port': int(os.getenv('PROXY_PORT')),
    'username': os.getenv('PROXY_USER'),
    'password': os.getenv('PROXY_PASS')
}

tg_client = TelegramClient('fin_ml_session', TG_API_ID, TG_API_HASH, proxy=proxy_dict)

TICKER_TO_FIGI = {}

# Инициализация моделей
logger.info('----------FINMLBOT: Запуск----------')
try:
    vectorizer = joblib.load('models/tfidf_vectorizer.pkl')
    gb_model = joblib.load('models/gradient_boosting_model.pkl')
    SHORT_CLASS_INDEX = list(gb_model.classes_).index(-1)
    logger.info("Модели успешно загружены")
except FileNotFoundError:
    logger.error("Ошибка: Файлы моделей не найдены")
    exit(1)

# Подключение Seara
device = torch.device("cpu")
model_name = "seara/rubert-base-cased-russian-sentiment"
logger.info("Загрузка токенизатора и seara")
tokenizer = AutoTokenizer.from_pretrained(model_name)
model = AutoModelForSequenceClassification.from_pretrained(model_name).to(device)
model.eval()

async def build_figi_dict(client: AsyncSandboxClient):
    logger.info("Загрузка актуального списка акций")
    try:
        response = await client.instruments.shares(
            instrument_status=InstrumentStatus.INSTRUMENT_STATUS_BASE
        )
        for instrument in response.instruments:
            if instrument.class_code == 'TQBR':
                TICKER_TO_FIGI[instrument.ticker] = {
                    'figi': instrument.figi, 
                    'lot': instrument.lot
                }
        logger.info(f"Загружено {len(TICKER_TO_FIGI)} акций Мосбиржи")
    except Exception as e:
        logger.error(f"Ошибка при загрузке инструментов: {e}")

def convert_money(value):
    if value is None:
        return 0.0
    if hasattr(value, 'units') and hasattr(value, 'nano'):
        return float(value.units + value.nano / 10**9)
    elif type(value) is float:
        units = int(value)
        nano = int((value - units) * 10**9)
        return Quotation(units=units, nano=nano)
    return 0.0
        

def get_tickers(text):
    found_tickers = re.findall(r'#([A-Za-z0-9]+)', text)
    found_tickers = [t.upper() for t in found_tickers if t.lower() not in ['дивиденд', 'новости']]
    return list(set(found_tickers))

# Информация по портфелю
async def portfolio_info(client: AsyncSandboxClient, ticker=None):
    if ticker == 'RUB':
        ticker = 'RUB000UTSTOM' # Для удобства

    try:
        portfolio = await client.operations.get_portfolio(account_id=ACCOUNT_ID)
        withdraw_limits = await client.operations.get_withdraw_limits(account_id=ACCOUNT_ID)
    except Exception as e:
        logger.error(f"Ошибка при запросе портфеля из API: {e}")
        return None if ticker else {'total_amount': 0, 'cur_balance': 0, 'poses': []}
    total_amount = convert_money(portfolio.total_amount_portfolio)
    raw_balance = 0.0
    if withdraw_limits.money:
        raw_balance = convert_money(withdraw_limits.money[0])
    poses = [{'ticker': pos.ticker,
                'quantity': pos.quantity.units + (pos.quantity.nano / 10**9),
                'current_price': convert_money(pos.current_price),
                'figi': pos.figi
                } for pos in portfolio.positions]

    short_poses_amount = sum(abs(pos['quantity'] * pos['current_price']) 
                        for pos in poses if pos['quantity'] < 0)
    cur_balance = raw_balance - short_poses_amount

    if ticker:
        for pos in poses:
            if pos['ticker'] == ticker:
                return pos
        return None
    else:
        return {'total_amount': total_amount, 'cur_balance': cur_balance, 'poses': poses}

# Определение объёма сделки
async def determine_quantity(client: AsyncSandboxClient, ticker, shares_quantity):
    core = 0.1
    dep_share = 0.1 / shares_quantity # Доля депозита на одну сделку
    instrument_info = TICKER_TO_FIGI.get(ticker)
    figi = instrument_info['figi']
    lot_size = instrument_info['lot']

    last_price_req = await client.market_data.get_last_prices(figi=[figi])
    cur_price = convert_money(last_price_req.last_prices[0].price)
    if cur_price == 0: 
        return 0

    p_info = await portfolio_info(client)
    total_amount = p_info['total_amount']
    cur_balance = p_info['cur_balance']
    pos_quantity = (total_amount * dep_share) // cur_price
    pos_amount = pos_quantity * cur_price

    if cur_balance - total_amount * core < pos_amount:
        pos_quantity = (cur_balance - total_amount * core) // cur_price
        pos_amount = pos_quantity * cur_price

    if pos_quantity <= 0:
        log_msg = (
            f'На счёте недостаточно свободных средств:\n'
            f'Текущая сделка: {ticker} * {cur_price} * {pos_quantity}\n'
            f'Объём сделки: {pos_amount}\n'
            f'Текущий баланс: {cur_balance}'
        )
        logger.info(log_msg)

    lots_quantity = int(pos_quantity // lot_size)
    
    if lots_quantity <= 0:
        logger.info(f"Недостаточно средств для покупки {ticker}")
        return 0
        
    return lots_quantity

async def calculate_atr(client: AsyncSandboxClient, figi, period=14):
    now = datetime.datetime.now(datetime.timezone.utc)
    past = now - datetime.timedelta(days=5)
    
    try:
        response =await client.market_data.get_candles(
            figi=figi,
            from_=past,
            to=now,
            interval=CandleInterval.CANDLE_INTERVAL_HOUR
        )
        candles = response.candles
        
        if not candles or len(candles) < period + 1:
            return None
            
        ranges = []
        target_candles = candles[-(period + 1):]
        
        for i in range(1, len(target_candles)):
            current = target_candles[i]
            previous = target_candles[i-1]
            
            c_high = convert_money(current.high)
            c_low = convert_money(current.low)
            p_close = convert_money(previous.close)
            
            tr1 = c_high - c_low
            tr2 = abs(c_high - p_close)
            tr3 = abs(c_low - p_close)
            
            ranges.append(max(tr1, tr2, tr3))
            
        atr = sum(ranges) / len(ranges)
        return atr
        
    except Exception as e:
        logger.error(f"Ошибка расчета ATR: {e}")
        return None

async def execute_trade(client: AsyncSandboxClient, tickers):
    direction = OrderDirection.ORDER_DIRECTION_SELL
    for ticker in tickers:
        pos_quantity = await determine_quantity(client, ticker, len(tickers))
        if pos_quantity == 0:
            return

        figi = TICKER_TO_FIGI.get(ticker)['figi']
        if not figi:
            continue

        try:
            order_response = await client.orders.post_order(
                figi=figi,
                quantity=pos_quantity,
                account_id=ACCOUNT_ID,
                direction=direction,
                order_type=OrderType.ORDER_TYPE_MARKET,
                order_id=str(uuid.uuid4())
            )
            logger.info(f"[SELL] Ордер по {ticker} исполнен. Статус: {order_response.execution_report_status.name}")

            total_executed = convert_money(order_response.executed_order_price)
            lot_size = TICKER_TO_FIGI.get(ticker)['lot']
            total_shares = pos_quantity * lot_size

            if total_executed > 0:
                executed_price = total_executed / total_shares
            else:
                lp = await client.market_data.get_last_prices(figi=[figi])
                executed_price = convert_money(lp.last_prices[0].price)
            
            atr = await calculate_atr(client, figi)
            if atr:
                stop_price = executed_price + (atr * 2)
                take_price = executed_price - (atr * 4)
                logger.info(f"Волатильность (ATR) {ticker}: {atr:.2f} руб.")
            else:
                stop_price = executed_price * 1.02
                take_price = executed_price * 0.96
                logger.info(f"Ошибка ATR. Используем фиксированные 2% и 4% для {ticker}")
            
            add_trade(ticker, figi, 'SHORT', executed_price, pos_quantity, stop_price, take_price)
        except Exception as e:
            logger.error(f"Ошибка операции по {ticker}: {e}")

def get_seara_probs(text):
    try:
        inputs = tokenizer(text, max_length=512, padding=True, truncation=True, return_tensors='pt').to(device)
        with torch.no_grad():
            outputs = model(**inputs)
            
        probabilities = torch.nn.functional.softmax(outputs.logits, dim=-1)[0].tolist()
        labels = model.config.id2label
        
        result = {labels[i].lower(): prob for i, prob in enumerate(probabilities)}
        
        return result.get('positive', 0.0), result.get('neutral', 0.0), result.get('negative', 0.0)
    except Exception:
        return 0.0, 1.0, 0.0

def make_prediction(news_text):
    prob_pos, prob_neu, prob_neg = get_seara_probs(news_text)
    x_tfidf = vectorizer.transform([news_text])
    x_sent = csr_matrix([[prob_pos, prob_neu, prob_neg]])

    vector = hstack([x_tfidf, x_sent])
    
    probabilities = gb_model.predict_proba(vector)[0]
    conf_short = probabilities[0]
    
    logger.info(f"Уверенность в падении: {conf_short*100:.1f}%")
    if conf_short >= 0.38:
        return -1
    return 0

#МОниторинг открытых сделок
async def position_monitor():
    logger.info("Запущен фоновый монитор сделок и виртуальных стопов")
    async with AsyncSandboxClient(TOKEN) as client:
        while True:
            await asyncio.sleep(30)
            open_trades = get_open_trades()
            if not open_trades:
                continue
            try:
                now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)

                for trade in open_trades:
                    lp = await client.market_data.get_last_prices(figi=[trade.figi])
                    current_price = convert_money(lp.last_prices[0].price)
                    if current_price == 0:
                        continue

                    close_reason = None

                    if trade.direction == 'SHORT':
                        if trade.stop_price and current_price >= trade.stop_price:
                            close_reason = 'STOP_LOSS'
                        elif trade.take_price and current_price <= trade.take_price:
                            close_reason = 'TAKE_PROFIT'

                    if not close_reason:
                        age_seconds = (now - trade.opened_at).total_seconds()
                        if age_seconds >= 3600:
                            close_reason = 'TIME'

                    if close_reason:
                        logger.info(f"Триггер {close_reason} для {trade.ticker}. Отправка ордера на закрытие")
                        try:
                            order = await client.orders.post_order(
                                figi=trade.figi, 
                                quantity=trade.quantity, 
                                account_id=ACCOUNT_ID,
                                direction=OrderDirection.ORDER_DIRECTION_BUY,
                                order_type=OrderType.ORDER_TYPE_MARKET, 
                                order_id=str(uuid.uuid4())
                            )
                            
                            total_executed = convert_money(order.executed_order_price)
                            lot_size = TICKER_TO_FIGI.get(trade.ticker)['lot']
                            total_shares = trade.quantity * lot_size
                            if total_executed > 0:
                                close_price = total_executed / total_shares
                            else:
                                close_price = current_price
                                
                            close_trade(trade.id, close_price, close_reason)
                            
                        except Exception as close_error:
                            if "70001" in str(close_error):
                                logger.warning(f"Песочница неликвидна (70001) при закрытии {trade.ticker}. Повтор через 30 сек")
                            else:
                                logger.error(f"Ошибка закрытия сделки {trade.ticker}: {close_error}")

            except Exception as e:
                logger.error(f"Ошибка в фоновом мониторе: {e}")

# Парсер + исполнение

processed_messages = set()
@tg_client.on(events.NewMessage(chats=TARGET_CHANNEL))
async def new_message_parser(event):
    msg_id = event.message.id
    if msg_id in processed_messages:
        return
    processed_messages.add(msg_id)
    news_text = event.message.message
    tickers = get_tickers(news_text)
    valid_tickers = [t for t in tickers if t in TICKER_TO_FIGI]
    if not valid_tickers:
        return
    log_msg = (
        f"Новая новость\n"
        f"Тикеры: {valid_tickers}\n"
        f"Текст: {news_text[:150]}..."
    )
    logger.info(log_msg)
    
    prediction = await asyncio.to_thread(make_prediction, news_text)
    
    if prediction != -1:
        logger.info('Шорт сигнал отменен отменен')
        return
    
    logger.info('Сигнал корректен, открытие сделки')
    async with AsyncSandboxClient(TOKEN) as client:
        await execute_trade(client, valid_tickers)


async def main():
    async with AsyncSandboxClient(TOKEN) as client:
        await build_figi_dict(client)
    
    await tg_client.start()

    asyncio.create_task(position_monitor())

    logger.info(f"Слушаем канал: @{TARGET_CHANNEL}")
    logger.info("Бот в режиме ожидания")
        
    await tg_client.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())