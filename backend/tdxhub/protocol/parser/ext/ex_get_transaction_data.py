# cython: language_level=3
import datetime
import struct
from collections import OrderedDict

from tdxhub.protocol.parser.base import BaseParser


class GetTransactionData(BaseParser):
    def setParams(self, market, code, start, count):
        """

        :param market:
        :param code:
        :param start:
        :param count:
        """
        if type(code) is str:
            code = code.encode("utf-8")

        pkg = bytearray.fromhex("01 01 08 00 03 01 12 00 12 00 fc 23")
        pkg.extend(struct.pack("<B9siH", market, code, start, count))

        self.send_pkg = pkg

    def parseResponse(self, body_buf):
        """

        :param body_buf:
        :return:
        """
        pos = 0
        market, code, _, num = struct.unpack("<B9s4sH", body_buf[pos : pos + 16])

        pos += 16
        result = []
        nature_name = ""

        for _ in range(num):
            raw_time, price, volume, zengcang, direction = struct.unpack("<HIIiH", body_buf[pos : pos + 16])

            pos += 16
            hour = raw_time // 60

            minute = raw_time % 60
            second = direction % 10000
            nature = direction  # 保持老接口的兼容性

            if second > 59:
                second = 0

            # 并入本仓时改: 原处是
            #     date = datetime.datetime.combine(datetime.date.today(), datetime.time(...))
            # TDX 的分笔报文只带 HH:MM:SS, 不带日期 —— 上游拿**本机今天**补齐, 于是每一条
            # 历史分笔都被盖上运行当天的日期。这既是 wall-clock 当交易日, 也是拿不知道的
            # 东西编一个值; 撞本项目两条红线(交易日只从 services.calendar 取 / 缺失只能
            # 传播为缺失)。日期由调用方在请求里给定, 解析器不知道, 就传 None。
            # hour/minute/second 三个字段原样保留, 时间信息一点没丢。
            date = None

            value = direction // 10000

            if value == 0:
                direction = 1
                if zengcang > 0:
                    if volume > zengcang:
                        nature_name = "多开"
                    elif volume == zengcang:
                        nature_name = "双开"
                elif zengcang == 0:
                    nature_name = "多换"
                else:
                    if volume == -zengcang:
                        nature_name = "双平"
                    else:
                        nature_name = "空平"
            elif value == 1:
                direction = -1
                if zengcang > 0:
                    if volume > zengcang:
                        nature_name = "空开"
                    elif volume == zengcang:
                        nature_name = "双开"
                elif zengcang == 0:
                    nature_name = "空换"
                else:
                    if volume == -zengcang:
                        nature_name = "双平"
                    else:
                        nature_name = "多平"
            else:
                direction = 0
                if zengcang > 0:
                    if volume > zengcang:
                        nature_name = "开仓"
                    elif volume == zengcang:
                        nature_name = "双开"
                elif zengcang < 0:
                    if volume > -zengcang:
                        nature_name = "平仓"
                    elif volume == -zengcang:
                        nature_name = "双平"
                else:
                    nature_name = "换手"

            if market in [31, 48]:
                if nature == 0:
                    direction = 1
                    nature_name = "B"
                elif nature == 256:
                    direction = -1
                    nature_name = "S"
                else:  # 512
                    direction = 0
                    nature_name = ""

            result.append(
                OrderedDict(
                    [
                        ("date", date),
                        ("hour", hour),
                        ("minute", minute),
                        ("second", second),
                        ("price", price),
                        ("volume", volume),
                        ("zengcang", zengcang),
                        ("nature", nature),
                        ("nature_mark", nature // 10000),
                        ("nature_value", nature % 10000),
                        ("nature_name", nature_name),
                        ("direction", direction),
                    ]
                )
            )

        return result
