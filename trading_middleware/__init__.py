import asyncio
import logging
import sys
import os

import pandas as pd
import ta
from binance.um_futures import UMFutures
from binance.error import ClientError

from aiogram import Bot, Dispatcher, html
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, BotCommand
from fastapi import FastAPI, Request
from contextlib import asynccontextmanager
import uvicorn

# Load environment variables (these should be set in your Vercel environment)
api = 'xCMChCj1fAfabaWt7VfQWpKoQBvieNUSEKvzfj48JUnzXLkYBxV5delPBFR5nCBE'
secret = '9l9mK5X2i0PVzSoUbYD7Kcn2bIjA2XTwGgOgJzMaK0s7yLANPQFGJpfvHW22anB9'
TOKEN = "6574734375:AAG7GRm5IpPyu90GoPTe1lzUqZHkSrmPdpE"
chat_ids = ["6068927923", "7205728757"] 

client = UMFutures(key=api, secret=secret)

# Global flag to control the signal handler loop
stop_signal_handler = False
# To ensure the analyze operation only runs once at a time
analyze_task = None

def get_tickers_usdt():
    tickers = []
    resp = client.ticker_price()
    for elem in resp:
        if 'USDT' in elem['symbol']:
            tickers.append(elem['symbol'])
    return tickers

interval = '1h'
limit = 1000

def fetch_and_process_data(symbol, interval, limit):
    try:
        klines = client.klines(symbol, interval, limit=limit)
        df = pd.DataFrame(klines, columns=['open_time', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'quote_asset_volume', 'number_of_trades', 'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'])
        df['open_time'] = pd.to_datetime(df['open_time'], unit='ms')
        df.set_index('open_time', inplace=True)
        df = df.astype(float)
        df = df[['open', 'high', 'low', 'close', 'volume']]
        return df
    except ClientError as error:
        print(f"Found error. status: {error.status_code}, error code: {error.error_code}, error message: {error.error_message}")
        return None

def kj_strategy(symbol, interval, limit):
    # Fetch and process data
    df = fetch_and_process_data(symbol, interval, limit)
    if df is None or df.empty:
        return 'none'

    # Calculate indicators
    close = df['close']
    high = df['high']
    low = df['low']
    open = df['open']

    ema50 = ta.trend.EMAIndicator(close=close, window=50).ema_indicator()
    ema200 = ta.trend.EMAIndicator(close=close, window=200).ema_indicator()
    rsi = ta.momentum.RSIIndicator(close=close, window=14).rsi()
    ichimoku = ta.trend.IchimokuIndicator(high=high, low=low)
    ichimoku_a = ichimoku.ichimoku_a()
    ichimoku_b = ichimoku.ichimoku_b()
    obv = ta.volume.OnBalanceVolumeIndicator(close=close, volume=df['volume']).on_balance_volume()
    macd = ta.trend.MACD(close=close)
    macd_diff = macd.macd_diff()

    # Generate signal
    if (ema50.iloc[-1] > ema200.iloc[-1]
        and rsi.iloc[-1] > 50
        and close.iloc[-1] > max(ichimoku_a.iloc[-1], ichimoku_b.iloc[-1])
        and obv.diff().iloc[-1] > 0
        and macd_diff.iloc[-1] > 0 and macd_diff.iloc[-2] < 0
    ):
        signal = 'up'
    elif (ema50.iloc[-1] < ema200.iloc[-1]
        and rsi.iloc[-1] < 50
        and close.iloc[-1] < min(ichimoku_a.iloc[-1], ichimoku_b.iloc[-1])
        and obv.diff().iloc[-1] < 0
        and macd_diff.iloc[-1] < 0 and macd_diff.iloc[-2] > 0
    ):
        signal = 'down'
    else:
        signal = 'none'

    return signal

symbols = get_tickers_usdt()

# Initialize Bot instance with default properties for API calls
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

@dp.message(CommandStart())
async def command_start_handler(message: Message) -> None:
    await message.answer(f"Hello, {html.bold(message.from_user.full_name)}!")

async def analyze_operation():
    global stop_signal_handler
    stop_signal_handler = False  # Reset the stop signal
    while not stop_signal_handler:
        for symbol in symbols:
            if stop_signal_handler:
                break
            if symbol == 'USDCUSDT':
                continue
            signal = kj_strategy(symbol, interval, limit)
            for chat_id in chat_ids:
                if signal == 'up':
                    await bot.send_message(chat_id, text=f'Found BUY signal for {symbol}')
                elif signal == 'down':
                    await bot.send_message(chat_id, text=f'Found SELL signal for {symbol}')
            await asyncio.sleep(30)
    await bot.send_message(chat_ids[0], "Stopped analyzing signals.")

@dp.message(Command(commands=["analyze"]))
async def analyze_command_handler(message: Message) -> None:
    global analyze_task
    if analyze_task is None or analyze_task.done():
        analyze_task = asyncio.create_task(analyze_operation())
        await message.answer("Started analyzing signals.")
    else:
        await message.answer("Analysis is already running.")

@dp.message(Command(commands=["stop"]))
async def stop_command_handler(message: Message) -> None:
    global stop_signal_handler
    if not stop_signal_handler:
        stop_signal_handler = True
        await message.answer("Stopping the signal handler...")
        await analyze_task  # Ensure that the task completes
    else:
        await message.answer("Signal handler is not running.")

async def on_startup():
    webhook_url = f"https://your-domain.com/bot{TOKEN}"
    await bot.set_webhook(webhook_url)
    await bot.set_my_commands([
        BotCommand(command="start", description="Starts the bot"),
        BotCommand(command="analyze", description="Start analyzing signals"),
        BotCommand(command="stop", description="Stops the bot")
    ])

# Set up FastAPI with the lifespan context
@asynccontextmanager
async def lifespan(app: FastAPI):
    global stop_signal_handler
    # Startup event
    analyze_task = asyncio.create_task(on_startup())
    yield
    # Shutdown event
    stop_signal_handler = True
    if analyze_task is not None:
        await analyze_task

app = FastAPI(lifespan=lifespan)

@app.post(f"/bot{TOKEN}")
async def bot_webhook(request: Request):
    update = await request.json()
    await dp.process_update(update)
    return {"status": "ok"}

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    uvicorn.run(app, host="0.0.0.0", port=8000)
