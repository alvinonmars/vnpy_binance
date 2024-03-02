import vnpy_crypto
vnpy_crypto.init()

from vnpy.event import EventEngine
from vnpy.trader.engine import MainEngine
from vnpy.trader.ui import MainWindow, create_qapp
from vnpy_chartwizard import ChartWizardApp
from vnpy_datarecorder import DataRecorderApp
from vnpy_datamanager import DataManagerApp
from vnpy_portfoliomanager import PortfolioManagerApp
from vnpy_paperaccount import PaperAccountApp
from vnpy_binance import (
    BinanceSpotGatewayPro
)

from vnpy_spreadtrading import SpreadTradingApp
from vnpy_portfoliomanager import PortfolioManagerApp

def main():
    """主入口函数"""
    qapp = create_qapp()

    event_engine = EventEngine()
    main_engine = MainEngine(event_engine)
    main_engine.add_gateway(BinanceSpotGatewayPro)
    
    main_engine.add_app(SpreadTradingApp)
    main_engine.add_app(ChartWizardApp)
    main_engine.add_app(DataRecorderApp)
    main_engine.add_app(DataManagerApp)
    main_engine.add_app(PortfolioManagerApp)
    main_engine.add_app(PaperAccountApp)
    main_engine.add_app(PortfolioManagerApp)
    
    main_window = MainWindow(main_engine, event_engine)
    main_window.showMaximized()

    qapp.exec()


if __name__ == "__main__":
    main()
