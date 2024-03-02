import os
import urllib
import hashlib
import hmac
import time
from copy import copy
from datetime import datetime, timedelta
from enum import Enum
from threading import Lock
import pytz
from typing import Any, Dict, List
from vnpy.trader.utility import round_to

from requests.exceptions import SSLError
from vnpy.trader.constant import (
    Direction,
    Exchange,
    Product,
    Status,
    OrderType,
    Interval
)
from vnpy.trader.gateway import BaseGateway
from vnpy.trader.object import (
    TickData,
    OrderData,
    TradeData,
    AccountData,
    ContractData,
    BarData,
    OrderRequest,
    CancelRequest,
    SubscribeRequest,
    HistoryRequest
)
from vnpy.trader.event import EVENT_TIMER
from vnpy.event import Event, EventEngine

import time
from binance.lib.utils import config_logging
from binance.websocket.spot.websocket_api import SpotWebsocketAPIClient
from binance.websocket.spot.websocket_stream import SpotWebsocketStreamClient
from binance.spot import Spot as SpotAPIClient

# 中国时区
CHINA_TZ = pytz.timezone("Asia/Shanghai")

# 实盘REST API地址
REST_HOST: str = "https://api.binance.com"

# 实盘Websocket API地址
WEBSOCKET_TRADE_HOST: str = "wss://stream.binance.com:9443/ws/"
WEBSOCKET_DATA_HOST: str = "wss://stream.binance.com:9443/stream"

# 模拟盘REST API地址
TESTNET_REST_HOST: str = "https://testnet.binance.vision"

# 模拟盘Websocket API地址
# TESTNET_WEBSOCKET_TRADE_HOST: str = "wss://testnet.binance.vision/ws/"
# TESTNET_WEBSOCKET_DATA_HOST: str = "wss://testnet.binance.vision/stream"

TESTNET_WEBSOCKET_TRADE_HOST: str = "wss://testnet.binance.vision/ws-api/v3"
TESTNET_WEBSOCKET_DATA_HOST: str = "wss://testnet.binance.vision"

# 委托状态映射
STATUS_BINANCE2VT: Dict[str, Status] = {
    "NEW": Status.NOTTRADED,
    "PARTIALLY_FILLED": Status.PARTTRADED,
    "FILLED": Status.ALLTRADED,
    "CANCELED": Status.CANCELLED,
    "REJECTED": Status.REJECTED,
    "EXPIRED": Status.CANCELLED
}

# 委托类型映射
ORDERTYPE_VT2BINANCE: Dict[OrderType, str] = {
    OrderType.LIMIT: "LIMIT",
    OrderType.MARKET: "MARKET"
}
ORDERTYPE_BINANCE2VT: Dict[str, OrderType] = {v: k for k, v in ORDERTYPE_VT2BINANCE.items()}

# 买卖方向映射
DIRECTION_VT2BINANCE: Dict[Direction, str] = {
    Direction.LONG: "BUY",
    Direction.SHORT: "SELL"
}
DIRECTION_BINANCE2VT: Dict[str, Direction] = {v: k for k, v in DIRECTION_VT2BINANCE.items()}

# 数据频率映射
INTERVAL_VT2BINANCE: Dict[Interval, str] = {
    Interval.MINUTE: "1m",
    Interval.HOUR: "1h",
    Interval.DAILY: "1d",
}

# 时间间隔映射
TIMEDELTA_MAP: Dict[Interval, timedelta] = {
    Interval.MINUTE: timedelta(minutes=1),
    Interval.HOUR: timedelta(hours=1),
    Interval.DAILY: timedelta(days=1),
}

# 合约数据全局缓存字典
symbol_contract_map: Dict[str, ContractData] = {}


# 鉴权类型
class Security(Enum):
    NONE = 0
    SIGNED = 1
    API_KEY = 2


class BinanceSpotGatewayPro(BaseGateway):
    """
    vn.py用于对接币安现货账户的交易接口。
    """

    default_name: str = "BINANCE_SPOT"

    default_setting: Dict[str, Any] = {
        "key": "DEVTEST_BINANCE_ED25519_APIKEY",
        "secret": "DEVTEST_BINANCE_ED25519_PEM",
        "服务器": ["REAL", "TESTNET"],
        "代理地址": "",
        "代理端口": 0
    }

    exchanges: Exchange = [Exchange.BINANCE]

    def __init__(self, event_engine: EventEngine, gateway_name: str) -> None:
        """构造函数"""
        super().__init__(event_engine, gateway_name)

        self.orders: Dict[str, OrderData] = {}
        self.ws_api_client:SpotWebsocketAPIClient = None
        self.ws_stream_client:SpotWebsocketStreamClient = None
        self.rest_api:SpotAPIClient = None
        
        self.ticks: Dict[str, TickData] = {}
        self.reqid: int = 0

    def connect(self, setting: dict):
        """连接交易接口"""
        key: str = setting["key"]
        secret: str = setting["secret"]
        proxy_host: str = setting["代理地址"]
        proxy_port: int = setting["代理端口"]
        server: str = setting["服务器"]
        key = os.getenv(key)
        secret = os.getenv(secret)
        #read apikey from file
        try:
            key = os.getenv(key)
            secret = os.getenv(secret)
            #read apikey from file
            with open(key, 'r') as f:
                key = f.read().strip()
            with open(secret, 'r') as f:
                secret = f.read().strip()
        except Exception as e:
            self.write_log(f"读取apikey失败:{str(e)},using as string")
            key = setting["key"]
            secret = setting["secret"]
            

        # make a connection to the websocket api
        self.ws_api_client = SpotWebsocketAPIClient(
            stream_url="wss://testnet.binance.vision/ws-api/v3",
            api_key=key,
            api_secret=secret,
            on_message=self.on_ws_api_message_handler,
            on_close=self.on_ws_api_close,
            on_error=self.on_ws_api_error,
            on_open=self.on_ws_api_open,
            on_ping=self.on_ws_api_ping,
            on_pong=self.on_ws_api_pong,
            timeout=10,
            proxies=None,
            
        )

        # make a connection to the websocket stream
        self.ws_stream_client = SpotWebsocketStreamClient(
            stream_url="wss://testnet.binance.vision",
            on_message=self.on_ws_stream_message_handler,
            on_close=self.on_ws_stream_close,
            on_error=self.on_ws_stream_error,
            on_open=self.on_ws_stream_open,
            on_ping=self.on_ws_stream_ping,
            on_pong=self.on_ws_stream_pong,
            timeout=10,
            proxies=None
            
        )

        # spot api client to call all restful api endpoints
        self.spot_api_client = SpotAPIClient(key, base_url="https://testnet.binance.vision")
        response = self.spot_api_client.new_listen_key()

        # You can subscribe to the user data stream from websocket stream, it will broadcast all the events
        # related to your account, including order updates, balance updates, etc.
        self.ws_stream_client.user_data(listen_key=response["listenKey"])
        

        self.event_engine.register(EVENT_TIMER, self.process_timer_event)

    def subscribe(self, req: SubscribeRequest) -> None:
        """订阅行情"""
        pass

    def send_order(self, req: OrderRequest) -> str:
        """委托下单"""
        pass

    def cancel_order(self, req: CancelRequest) -> None:
        """委托撤单"""
        pass

    def query_account(self) -> None:
        """查询资金"""
        pass

    def query_position(self) -> None:
        """查询持仓"""
        pass

    def query_history(self, req: HistoryRequest) -> List[BarData]:
        """查询历史数据"""
        pass

    def close(self) -> None:
        """关闭连接"""
        self.write_log("关闭连接")
        self.ws_api_client.stop()
        self.ws_stream_client.stop()

    def process_timer_event(self, event: Event) -> None:
        """定时事件处理"""
        self.ws_api_client.ping_connectivity()
        self.ws_stream_client.ping()
        self.spot_api_client.ping()
        

    def on_order(self, order: OrderData) -> None:
        """推送委托数据"""
        self.orders[order.orderid] = copy(order)
        super().on_order(order)

    def get_order(self, orderid: str) -> OrderData:
        """查询委托数据"""
        return self.orders.get(orderid, None)


    def on_ws_api_close(self,_):
        self.write_log("websocket API closed")
    def on_ws_api_error(self,_,error):
        self.write_log(f"websocket API error: {error}")
    def on_ws_api_open(self,_):
        self.write_log("websocket API opened")
    def on_ws_api_ping(self,_):
        self.write_log("websocket API ping")
    def on_ws_api_pong(self,_):
        self.write_log("websocket API pong")
    
    def on_ws_stream_close(self,_):
        self.write_log("websocket stream closed")
    def on_ws_stream_error(self,_,error):
        self.write_log(f"websocket stream error: {error}")
    def on_ws_stream_open(self,_):
        self.write_log("websocket stream opened")
    def on_ws_stream_ping(self,_):
        self.write_log("websocket stream ping")
    def on_ws_stream_pong(self,_):
        self.write_log("websocket stream pong")
        
    def on_ws_api_message_handler(self,_, message):
        self.write_log(f"websocket API message: {message}")
    def on_ws_stream_message_handler(self,_, message):
        self.write_log(f"websocket stream message: {message}")

def generate_datetime(timestamp: float) -> datetime:
    """生成时间"""
    dt: datetime = datetime.fromtimestamp(timestamp / 1000)
    dt: datetime = CHINA_TZ.localize(dt)
    return dt
