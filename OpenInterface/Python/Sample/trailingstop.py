# coding:utf-8
import sys
import path
folder = path.path(__file__).abspath()
openft_folder = folder.parent.parent
sys.path.append(openft_folder)
from openft.open_quant_context import *
from emailplugin import EmailNotification
import numpy as np
import matplotlib.pyplot as plt
from math import floor
import time
'''
    跟踪止损:跟踪止损是一种更高级的条件单，需要指定如下参数，以便制造出移动止损价
        跟踪止损的详细介绍：https://www.futu5.com/faq/topic214
    api_svr_ip: (string)ip
    api_svr_port: (int)port
    unlock_password: (string)交易解锁密码, 必需修改！！！，模拟交易设为一个非空字符串即可
    code: (string)股票
    trade_env:
        0: 真实交易
        1: 模拟交易 （美股暂不支持模拟交易）
    method:
        0:股票下跌drop价格就会止损
        1:股票下跌drop的百分比就会止损
    drop:
        method==0,股票下跌的价格
        method==1，股票下跌的百分比，0.01表示下跌1%则止损
    volume: 需要卖掉的股票数量
    how_to_sell：以何种方式卖出股票，默认值为0
        0: 以(市价-diff)的价格卖出
        1: 以smart_sell方式卖出
    diff： 默认为0，当how_to_sell为0时，以(市价-diff)的价格卖出
'''

# 全局参数配置
api_svr_ip = '119.29.141.202'
# api_svr_ip = '127.0.0.1'
api_svr_port = 11111
unlock_password = "a"
code = 'HK.00700'  # 'US.BABA' #'HK.00700'
trade_env = 1
method = 0
drop = 0.2
volume = 100
how_to_sell = 0
diff = 0.2
check_time = 2  # 每隔check_time秒，smart_sell会检查订单状态,需要>=2
#

# 邮件通知参数
enable_email_notification = True
receiver = 'your receiver email'

# 程序中的一些全局变量，不需要修改
RET_OK = 0
RET_ERROR = -1
#


class StockSeller:
    # 传入的 quot_ctx和trade_ctx已经做好了订阅和解锁交易等工作
    # 以某个价格下单
    @staticmethod
    def simple_sell(
            quote_ctx,
            trade_ctx,
            stock_code,
            trade_price,
            volume,
            trade_env):
        lot_size = 0
        while True:
            if lot_size == 0:
                ret, data = quote_ctx.get_market_snapshot([stock_code])
                lot_size = data.iloc[0]['lot_size'] if ret == 0 else 0
                if ret != 0:
                    print("can't get lot_size, retrying")
                    continue
                elif lot_size <= 0:
                    raise BaseException('lot_size error {}'.format(stock_code))
            qty = floor(volume / lot_size) * lot_size
            ret, data = trade_ctx.place_order(
                price=trade_price,
                qty=qty,
                strcode=stock_code,
                orderside=1,
                envtype=trade_env)
            if ret != RET_OK:
                print('place order failed {}'.format(data))
                EmailNotification.sendemail(
                    receiver, '下单失败', '下单失败:{}[FutuOpenAPI]'.format(data))
                return None
            else:
                print('下单成功')
                return data
            return None

    # 以bid[0]价格下单
    @staticmethod
    def smart_sell(quote_ctx, trade_ctx, stock_code, volume, trade_env):
        lot_size = 0
        while True:
            if lot_size == 0:
                ret, data = quote_ctx.get_market_snapshot([stock_code])
                lot_size = data.iloc[0]['lot_size'] if ret == 0 else 0
                if ret != 0:
                    print("can't get lot_size, retrying")
                    continue
                elif lot_size <= 0:
                    raise BaseException('lot_size error {}'.format(stock_code))
            qty = floor(volume / lot_size) * lot_size
            ret, data = quote_ctx.subscribe(stock_code, 'ORDER_BOOK')
            if ret != 0:
                print("retrying to subscribe orderbook")
                continue
            ret, data = quote_ctx.get_order_book(stock_code)
            if ret != RET_OK:
                print('can not get orderbook,retrying')
                continue
            # 以Bid[0]卖出
            price = data['Bid'][0][0]
            print('bid price is {}'.format(price))
            # 下单
            ret, data = trade_ctx.place_order(
                price=price,
                qty=qty,
                strcode=stock_code,
                orderside=1,
                envtype=trade_env)
            if ret != RET_OK:
                print('place order failed {}'.format(data))
                EmailNotification.sendemail(
                    receiver, '下单失败', '下单失败:{}[FutuOpenAPI]'.format(data))
                return None
            else:
                print('下单成功')
                return data
            return None


class TrailingStopHandler(StockQuoteHandlerBase):
    def __init__(self,
                 quote_ctx, trade_ctx,
                 unlock_password="",
                 code='HK.00700',
                 trade_env=1,
                 method=0,
                 drop=1,
                 volume=1000,
                 how_to_sell=1,
                 diff=0):
        self.quote_ctx = quote_ctx
        self.trade_ctx = trade_ctx
        self.unlock_password = unlock_password
        self.code = code
        self.trade_env = trade_env
        self.method = method
        self.drop = drop
        self.volume = volume
        self.how_to_sell = how_to_sell
        self.diff = diff
        # 是否触发交易
        self.finished = False
        # 截止线的价格
        self.stop = None
        self.pricelist = []
        self.stoplist = []
        self.timelist = []

    def on_recv_rsp(self, rsp_str):
        ret_code, content = super(
            TrailingStopHandler, self).on_recv_rsp(rsp_str)
        if ret_code != RET_OK:
            print("StockQuoteTest: error, msg: %s" % content)
            return RET_ERROR, content
        if self.finished:
            # print('已经触发交易')
            return ret_code, content
        # 检查是否处于交易时间段
        is_hk_trade = 'HK.' in (code)
        ret, data = self.quote_ctx.get_global_state()
        if ret != RET_OK:
            print('获取全局状态失败')
            trading = False
        else:
            hk_trading = (data['Market_HK'] == '3' or data['Market_HK'] == '5')
            us_trading = data['Market_US'] == '3'
            trading = hk_trading if is_hk_trade else us_trading
        # 如果不在盘中，什么都不做
        if not trading:
            print('不处在交易时段')
            return 0, content
        last_price = content.iloc[0]['last_price']
        print('last_price={}'.format(last_price))
        if self.stop is None:
            self.stop = last_price - \
                self.drop if self.method == 0 else last_price * (1 - self.drop)
        elif (self.stop + self.drop < last_price) if self.method == 0 else (self.stop < last_price * (1 - self.drop)):
            self.stop = last_price - \
                self.drop if self.method == 0 else last_price * (1 - self.drop)
        elif self.stop >= last_price:
            # 交易己被触发
            self.finished = True
            # 交易股票
            print('sell the stock, stop={}'.format(self.stop))
            qty = self.volume
            sell_price=self.stop
            while qty > 0:
                if how_to_sell == 0:
                    data = StockSeller.simple_sell(
                        quote_ctx=self.quote_ctx,
                        trade_ctx=self.trade_ctx,
                        stock_code=self.code,
                        trade_price=sell_price - self.diff,
                        volume=qty,
                        trade_env=self.trade_env)
                else:
                    data = StockSeller.smart_sell(
                        quote_ctx=self.quote_ctx,
                        trade_ctx=self.trade_ctx,
                        stock_code=self.code,
                        volume=qty,
                        trade_env=self.trade_env)
                if data is None:
                    print('下单失败')
                    sys.exit(-1)
                orderid = data.iloc[0]['orderid']
                envtype = data.iloc[0]['envtype']
                # 停顿一下
                time.sleep(check_time)
                # 检查订单状态
                ret, data = self.trade_ctx.order_list_query(envtype=envtype)
                if ret != RET_OK:
                    print('获取订单状态失败')
                    sys.exit(-1)
                status = data[data['orderid'] == orderid].iloc[0]['status']
                dealt_qty = data[data['orderid'] ==
                                 orderid].iloc[0]['dealt_qty']
                status = int(status)
                dealt_qty = int(dealt_qty)
                qty -= dealt_qty
                if status == 3:
                    print(
                        '全部成交{}:股票代码{}，成交总数{}'.format(
                            status, self.code, self.volume))
                    EmailNotification.sendemail(
                        receiver, '全部成交', '股票代码{}，成交总数{}'.format(
                            self.code, self.volume))
                else:
                    # 撤单
                    ret, data = self.trade_ctx.set_order_status(
                        0, orderid=orderid)
                    if ret != RET_OK:
                        print('撤单失败{}'.format(data))
                        sys.exit(-1)
                if how_to_sell==0:
                    # 更新sell_price
                    ret, data = self.quote_ctx.subscribe(self.code, 'ORDER_BOOK')
                    if ret != 0:
                        print("retrying to subscribe orderbook")
                        continue
                    ret, data = self.quote_ctx.get_order_book(self.code)
                    if ret != RET_OK:
                        print('can not get orderbook,retrying')
                        continue
                    # 以Bid[0]卖出
                    sell_price = data['Bid'][0][0]
                    print('update sell price {}'.format(sell_price))
        self.pricelist.append(last_price)
        self.stoplist.append(self.stop)
        return RET_OK, content


def trailingstop(
        api_svr_ip='127.0.0.1',
        api_svr_port=11111,
        unlock_password="",
        code='HK.00700',
        trade_env=1,
        method=0,
        drop=1,
        volume=1000,
        how_to_sell=1,
        diff=0,
        enable_email_notification=False):
    # 参数检查
    EmailNotification.setenable(enable_email_notification)
    # 检查how_to_sell
    if how_to_sell != 0 and how_to_sell != 1:
        print('how_to_sell must be 0 or 1')
        raise 'how_to_sell value error'
    # 检查trade_env
    if trade_env != 0 and trade_env != 1:
        print('trade_env must be 0 or 1')
        raise 'trade_env value error'
    # 检查method
    if method != 0 and method != 1:
        print('method must be 0 or 1')
        raise 'method value error'
    # 检查api_svr_ip 和api_svr_port
    quote_ctx = OpenQuoteContext(host=api_svr_ip, port=api_svr_port)
    trade_ctx = None
    is_hk_trade = 'HK.' in (code)
    if is_hk_trade:
        trade_ctx = OpenHKTradeContext(host=api_svr_ip, port=api_svr_port)
    else:
        if trade_env != 0:
            raise "美股交易接口不支持仿真环境"
        trade_ctx = OpenUSTradeContext(host=api_svr_ip, port=api_svr_port)
    # 检查解锁密码
    if unlock_password == "":
        raise "请先配置交易解锁密码!"
    if trade_env == 0:
        ret, data = trade_ctx.unlock_trade(unlock_password)
        if ret != RET_OK:
            print('解锁交易失败')
            raise '解锁交易失败'
    # 检查volume
    ret, data = trade_ctx.position_list_query(envtype=trade_env)
    if ret != RET_OK:
        print('can not get position list')
        raise '无法获取持仓列表'
    qty = data[data['code'] == code].iloc[0]['qty']
    qty = int(qty)
    if volume == 0:
        # 把全部持仓卖出
        volume = qty
    elif volume < 0:
        print("volume lower than 0")
        raise "voluem lower than 0"
    else:
        # 检查是否拥有足够的持仓
        if qty < volume:
            print('qty lower than volume')
            raise '你没有足够的持仓'
    stop = None
    ret, data = quote_ctx.subscribe(code, 'QUOTE', push=True)
    if ret != RET_OK:
        print('error {0}:{1}'.format(ret, data))
        exit(ret)
    # 检查diff
    if diff != 0:
        if is_hk_trade:
            ret, data = quote_ctx.subscribe(code, 'ORDER_BOOK')
            if ret != RET_OK:
                print('error {}'.format(data))
                exit(ret)
            ret, data = quote_ctx.get_order_book(code)
            if ret != RET_OK:
                print("can't get order bool{}".format(data))
                exit(ret)
            min_diff = round(abs(data['Bid'][0][0] - data['Bid'][1][0]), 3)
            if floor(diff / min_diff) * min_diff != diff:
                print('diff应是{}的整数倍'.format(min_diff))
                exit(-1)
        else:
            if round(diff, 2) != diff:
                print('美股价差保留两位小数:{}->{}'.format(diff, round(diff, 2)))
                exit(-1)
    trailing_stop_handler = TrailingStopHandler(
        quote_ctx,
        trade_ctx,
        unlock_password,
        code,
        trade_env,
        method,
        drop,
        volume,
        how_to_sell,
        diff)
    quote_ctx.set_handler(trailing_stop_handler)
    quote_ctx.start()
    while True:
        if trailing_stop_handler.finished:
            pricelist = trailing_stop_handler.pricelist
            plt.plot(np.arange(len(pricelist)), pricelist, color='b')
            stoplist = trailing_stop_handler.stoplist
            plt.plot(np.arange(len(stoplist)), stoplist, color='r')
            plt.show()
            break
    quote_ctx.stop()
    trade_ctx.stop()


if __name__ == '__main__':
    trailingstop(
        api_svr_ip,
        api_svr_port,
        unlock_password,
        code,
        trade_env,
        method,
        drop,
        volume,
        how_to_sell,
        diff,
        enable_email_notification)
