from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker
import datetime
import logging
logger = logging.getLogger(__name__)

engine = create_engine('sqlite:///trades.db', echo=False)
Base = declarative_base()

class TradeRecord(Base):
    __tablename__ = 'trades_history'

    id = Column(Integer, primary_key=True)
    ticker = Column(String, nullable=False)
    figi = Column(String, nullable=False)
    direction = Column(String, nullable=False)
    status = Column(String, default='OPEN')
    
    entry_price = Column(Float, nullable=False)
    quantity = Column(Integer, nullable=False)

    stop_price = Column(Float, nullable=True)
    take_price = Column(Float, nullable=True)

    opened_at = Column(DateTime, default=datetime.datetime.utcnow)
    close_price = Column(Float, nullable=True)
    pnl_percent = Column(Float, nullable=True)
    close_reason = Column(String, nullable=True)
    closed_at = Column(DateTime, nullable=True)

Base.metadata.create_all(engine)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def add_trade(ticker, figi, direction, entry_price, quantity, stop_price=None, take_price=None):
    db = SessionLocal()
    new_trade = TradeRecord(
        ticker=ticker,
        figi=figi,
        direction=direction,
        entry_price=entry_price,
        quantity=quantity,
        stop_price=stop_price,
        take_price=take_price,
        opened_at=datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None),
        status='OPEN'
    )
    db.add(new_trade)
    db.commit()
    db.close()
    logger.info(f"БД | Сделка {ticker} сохранена. Вход: {entry_price:.2f}, Стоп: {stop_price:.2f}, Тейк: {take_price:.2f}")

def get_open_trades():
    db = SessionLocal()
    open_trades = db.query(TradeRecord).filter(TradeRecord.status == 'OPEN').all()
    db.expunge_all() 
    db.close()
    return open_trades

def close_trade(trade_id, close_price, reason):
    db = SessionLocal()
    trade = db.query(TradeRecord).filter(TradeRecord.id == trade_id).first()
    
    if trade and trade.status == 'OPEN':
        trade.status = 'CLOSED'
        trade.close_price = close_price
        trade.closed_at = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        
        if close_price > 0:
            if trade.direction == 'SHORT':
                trade.pnl_percent = round(((trade.entry_price - close_price) / trade.entry_price) * 100, 2)
            else: 
                trade.pnl_percent = round(((close_price - trade.entry_price) / trade.entry_price) * 100, 2)
        else:
            trade.pnl_percent = 0.0
        
        if reason == 'SL_OR_TP':
            if trade.direction == 'SHORT':
                if close_price >= trade.entry_price:
                    trade.close_reason = 'STOP_LOSS'
                else:
                    trade.close_reason = 'TAKE_PROFIT'
            else:
                if close_price <= trade.entry_price:
                    trade.close_reason = 'STOP_LOSS'
                else:
                    trade.close_reason = 'TAKE_PROFIT'
        else:
            trade.close_reason = reason
        
        db.commit()
        logger.info(f"БД | Сделка {trade.ticker} закрыта. Причина: {trade.close_reason}. PnL: {trade.pnl_percent}%")
    db.close()