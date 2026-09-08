# cython: language_level=3
import struct
from datetime import datetime

from tdxhub.protocol.constants import SECURITY_COEFFICIENT
from tdxhub.protocol.logger import logger


def get_price(data, pos):
    """
    分析了一下，貌似是类似utf-8的编码方式保存有符号数字

    :param data:
    :param pos:
    :return:
    """

    pos_byte = 6

    bdata = index_bytes(data, pos)
    int_data = bdata & 0x3F

    if bdata & 0x40:
        sign = True
    else:
        sign = False

    if bdata & 0x80:
        while True:
            pos += 1

            bdata = index_bytes(data, pos)

            int_data += (bdata & 0x7F) << pos_byte
            pos_byte += 7

            if bdata & 0x80:
                pass
            else:
                break

    pos += 1

    if sign:
        int_data = -int_data

    return int_data, pos


def get_volume(vol):
    """
    获取交易量
    通达信把成交量/成交额编成一个私有浮点: 1 字节指数(乘 2 后偏移) + 3 字节尾数,
    外加一个**隐含前导 1**(下面的 ``dbl_xmm6`` 项)。本函数是对客户端反汇编的逐字转写
    (变量名 dbl_xmm6 / dw_ecx 等即 x86 寄存器名), 沿 tdxpy -> mootdx -> 本仓 vendor 而来。

    零值必须单独处理: 字段全零表示"这根 bar 没有成交"(停牌/无量), 但隐含前导 1 那一项
    在 logpoint=0 时算出 ``2 ** -127`` 并被无条件累加, 于是 ``get_volume(0)`` 会返回
    5.877471754111438e-39 而不是 0.0。这个值物理上不可能(A 股最小成交 1 手), 却是有限
    正数, 会一路穿过下游的 ``float()`` 转换落库, 再被"非零"过滤条件静默滤掉 —— 与
    "停牌日整行缺失"完全同形, 无法区分。

    2026-09-08 实测发现: 消费方 chunkymonkey 的 canonical_nominal_ohlcv_daily 里有 6 行
    (2026-08-31 的停牌股) vol=2**-127、amount=2**-127/1000, 即本函数对全零输入的输出。
    回归锁见 tests/test_protocol_helper_volume.py。

    :param vol: 4 字节小端整数原样
    :return: 解码后的量; 输入为 0 时返回 0.0
    """
    if vol == 0:
        return 0.0

    logpoint = vol >> (8 * 3)

    hleax = (vol >> (8 * 2)) & 0xFF  # [2]
    lheax = (vol >> 8) & 0xFF  # [1]
    lleax = vol & 0xFF  # [0]

    dw_ecx = logpoint * 2 - 0x7F
    dw_edx = logpoint * 2 - 0x86

    dw_esi = logpoint * 2 - 0x8E
    dw_eax = logpoint * 2 - 0x96

    if dw_ecx < 0:
        tmp_eax = -dw_ecx
    else:
        tmp_eax = dw_ecx

    dbl_xmm6 = pow(2.0, tmp_eax)

    if dw_ecx < 0:
        dbl_xmm6 = 1.0 / dbl_xmm6

    if hleax > 0x80:
        dwtmpeax = dw_edx + 1
        tmpdbl_xmm3 = pow(2.0, dwtmpeax)

        dbl_xmm0 = pow(2.0, dw_edx) * 128.0
        dbl_xmm0 += (hleax & 0x7F) * tmpdbl_xmm3
        dbl_xmm4 = dbl_xmm0

    else:
        if dw_edx >= 0:
            dbl_xmm0 = pow(2.0, dw_edx) * hleax
        else:
            dbl_xmm0 = (1 / pow(2.0, dw_edx)) * hleax

        dbl_xmm4 = dbl_xmm0

    dbl_xmm3 = pow(2.0, dw_esi) * lheax
    dbl_xmm1 = pow(2.0, dw_eax) * lleax

    if hleax & 0x80:
        dbl_xmm3 *= 2.0
        dbl_xmm1 *= 2.0

    dbl_ret = dbl_xmm6 + dbl_xmm4 + dbl_xmm3 + dbl_xmm1

    return dbl_ret


def get_datetime(category, buffer, pos):
    """
    获取日期时间
    :param category:
    :param buffer:
    :param pos:
    :return:
    """
    minute = 0
    hour = 15

    if category < 4 or category == 7 or category == 8:
        zip_day, minutes = struct.unpack("<HH", buffer[pos: pos + 4])

        month = int((zip_day % 2048) / 100)
        year = (zip_day >> 11) + 2004
        day = (zip_day % 2048) % 100

        minute = minutes % 60
        hour = int(minutes / 60)
    else:
        (zip_day,) = struct.unpack("<I", buffer[pos: pos + 4])

        month = int((zip_day % 10000) / 100)
        year = int(zip_day / 10000)
        day = zip_day % 100

    pos += 4

    return year, month, day, hour, minute, pos


def get_time(buffer, pos):
    """
    获取时间
    :param buffer:
    :param pos:
    :return:
    """
    (minutes,) = struct.unpack("<H", buffer[pos: pos + 2])

    hour = int(minutes / 60)
    minute = minutes % 60

    pos += 2

    return hour, minute, pos


def index_bytes(data, pos):
    """
    索引比特
    :param data:
    :param pos:
    :return:
    """
    return data[pos]


# def get_security_coefficient(market, name):
#     return SECURITY_COEFFICIENT[get_security_type(market, name)]


# TODO 增加 get_coefficient 函数
def get_security_coefficient(market=None, code=None):
    try:
        security_type = get_security_type(market=market, code=code)
        coefficient = SECURITY_COEFFICIENT[security_type]
        return coefficient[0]
    except NotImplementedError:
        logger.error('NotImplementedError')
        return 0.01


def get_security_type(market, code):
    """
    获取股票类型, A股, B股, 指数等

    :param market: 市场
    :param code: 代码
    :return:
    """

    # code = Path(code).stem
    code = str(code)
    code_head = str(code)[:2]

    if market in ["SZ", "sz", 0]:
        if code_head in ["00", "30"]:
            return "SZ_A_STOCK"

        if code_head in ["20"]:
            return "SZ_B_STOCK"

        if code_head in ["39"]:
            return "SZ_INDEX"

        if code_head in ["15", "16"]:
            return "SZ_FUND"

        if code_head in ["10", "11", "12", "13", "14"]:
            return "SZ_BOND"

    if market in ["SH", "sh", 1]:
        if code_head in ["60", "68"]:  # 688XXX科创板
            return "SH_A_STOCK"

        if code_head in ["90"]:
            return "SH_B_STOCK"

        if code_head in ["00", "88", "99"]:
            return "SH_INDEX"

        if code_head in ["50", "51"]:
            return "SH_FUND"

        if code_head in ["01", "10", "11", "12", "13", "14", "20"]:
            return "SH_BOND"

    logger.debug("Unknown security exchange !")
    raise NotImplementedError


def dump(buf):
    from pprint import pprint

    try:
        from hexdump import hexdump

        pprint(hexdump(buf))
    except ImportError:
        pprint(buf)


def time_frame(current_time=None):
    """
    判断时间是否在交易时间段内
    :param current_time: 要检查的时间, 如果空则默认当前时间
    :return: 在交易时间段内返回 True， 否则返回 False
    """
    current_time = current_time or datetime.now()

    start_time = datetime.strptime(str(current_time.date()) + '9:30', '%Y-%m-%d%H:%M')
    end_time = datetime.strptime(str(current_time.date()) + '11:30', '%Y-%m-%d%H:%M')

    if start_time < current_time < end_time:
        return True

    start_time = datetime.strptime(str(current_time.date()) + '13:00', '%Y-%m-%d%H:%M')
    end_time = datetime.strptime(str(current_time.date()) + '15:00', '%Y-%m-%d%H:%M')

    if start_time < current_time < end_time:
        return True

    return False
