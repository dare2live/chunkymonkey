"""
tdxhub 数据能力全景图 (API Capability Catalog)

本模块将 tdxhub 包装的全部 tdxpy/通达信协议能力显化为结构化目录，
便于开发者快速了解可用数据源、对应方法名、底层协议方法和数据限制。

使用方式:
    from tdxhub.capabilities import CAPABILITIES, summary
    summary()                    # 打印全景表
    CAPABILITIES['std_quotes']   # 查看标准行情能力列表
"""

# ---------------------------------------------------------------------------
# 数据能力注册表
# ---------------------------------------------------------------------------

CAPABILITIES = {
    # ===== 标准行情 (StdQuotes) =====
    'std_quotes': [
        {
            'method': 'quotes',
            'description': '实时行情快照（五档盘口 + 最新价）',
            'tdxpy_method': 'TdxHq_API.get_security_quotes',
            'params': 'symbol: str | list[str]',
            'limits': '单次最多约 80 只股票',
            'returns': 'records(code, open, high, low, price, bid1-5, ask1-5, vol, amount ...)',
            'category': '实时数据',
        },
        {
            'method': 'bars',
            'description': 'K线数据（支持 12 种周期: 1m/5m/15m/30m/1h/日/周/月/季/年）',
            'tdxpy_method': 'TdxHq_API.get_security_bars',
            'params': 'symbol, frequency, start=0, offset=800',
            'limits': '单次最多 800 根K线',
            'returns': 'records(datetime, open, high, low, close, vol, amount)',
            'category': '历史数据',
        },
        {
            'method': 'index_bars',
            'description': '指数K线数据',
            'tdxpy_method': 'TdxHq_API.get_index_bars',
            'params': 'symbol, frequency, start=0, offset=800',
            'limits': '单次最多 800 根K线',
            'returns': 'records(datetime, open, high, low, close, vol, amount, up_count, down_count)',
            'category': '历史数据',
        },
        {
            'method': 'stocks',
            'description': '股票列表（按市场获取全部证券代码）',
            'tdxpy_method': 'TdxHq_API.get_security_list',
            'params': 'market: 0=深/1=沪',
            'limits': '每页 1000 条，自动翻页',
            'returns': 'records(code, volunit, decimal_point, name, pre_close)',
            'category': '基础信息',
        },
        {
            'method': 'stock_count',
            'description': '市场证券数量',
            'tdxpy_method': 'TdxHq_API.get_security_count',
            'params': 'market: 0=深/1=沪/2=北',
            'limits': '—',
            'returns': 'int',
            'category': '基础信息',
        },
        {
            'method': 'minute',
            'description': '当日实时分时数据',
            'tdxpy_method': 'TdxHq_API.get_minute_time_data',
            'params': 'symbol',
            'limits': '仅当日',
            'returns': 'records(price, vol)',
            'category': '实时数据',
        },
        {
            'method': 'minutes',
            'description': '历史分时数据',
            'tdxpy_method': 'TdxHq_API.get_history_minute_time_data',
            'params': 'symbol, date',
            'limits': '每次一天',
            'returns': 'records(price, vol)',
            'category': '历史数据',
        },
        {
            'method': 'transaction',
            'description': '当日分笔成交',
            'tdxpy_method': 'TdxHq_API.get_transaction_data',
            'params': 'symbol, start=0, offset=800',
            'limits': '单次最多 2000 条',
            'returns': 'records(time, price, vol, buyorsell)',
            'category': '实时数据',
        },
        {
            'method': 'transactions',
            'description': '历史分笔成交',
            'tdxpy_method': 'TdxHq_API.get_history_transaction_data',
            'params': 'symbol, date, start=0, offset=800',
            'limits': '单次最多 2000 条',
            'returns': 'records(time, price, vol, buyorsell)',
            'category': '历史数据',
        },
        {
            'method': 'xdxr',
            'description': '除权除息信息',
            'tdxpy_method': 'TdxHq_API.get_xdxr_info',
            'params': 'symbol',
            'limits': '—',
            'returns': 'records(category, name, fenhong, peigujia, songzhuangu, peigu ...)',
            'category': '基础信息',
        },
        {
            'method': 'finance',
            'description': '个股财务摘要（每股收益/净资产/公积金/市盈率等）',
            'tdxpy_method': 'TdxHq_API.get_finance_info',
            'params': 'symbol',
            'limits': '单只股票',
            'returns': 'records(liutongguben, zongguben, bstock, profit, gongji, ...)',
            'category': '基本面',
        },
        {
            'method': 'k',
            'description': '按日期范围获取日K线（自动翻页拼接）',
            'tdxpy_method': 'TdxHq_API.get_security_bars (组合)',
            'params': 'symbol, begin, end',
            'limits': '内部自动分页',
            'returns': 'records(date, open, high, low, close, vol, amount, code)',
            'category': '历史数据',
        },
        {
            'method': 'block',
            'description': '板块/概念/风格/指数成分信息',
            'tdxpy_method': 'TdxHq_API.get_and_parse_block_info',
            'params': 'tofile: block.dat/block_zs.dat/block_fg.dat/block_gn.dat',
            'limits': '—',
            'returns': 'records(blockname, block_type, code_index, code)',
            'category': '基础信息',
        },
        {
            'method': 'F10C',
            'description': '公司 F10 资料目录',
            'tdxpy_method': 'TdxHq_API.get_company_info_category',
            'params': 'symbol',
            'limits': '—',
            'returns': 'list[dict(name, filename, start, length)]',
            'category': '基本面',
        },
        {
            'method': 'F10',
            'description': '公司 F10 资料详情（可按标题查询）',
            'tdxpy_method': 'TdxHq_API.get_company_info_content',
            'params': 'symbol, name=""',
            'limits': '—',
            'returns': 'dict{标题: 内容} 或 str',
            'category': '基本面',
        },
    ],

    # ===== 扩展行情 (ExtQuotes) =====
    'ext_quotes': [
        {
            'method': 'markets',
            'description': '扩展市场列表（期货/外汇/黄金等）',
            'tdxpy_method': 'TdxExHq_API.get_markets',
            'params': '—',
            'limits': '—',
            'returns': 'records(market, name, short_name)',
            'category': '基础信息',
        },
        {
            'method': 'instruments / instrument',
            'description': '扩展市场合约列表',
            'tdxpy_method': 'TdxExHq_API.get_instrument_info',
            'params': 'start=0, offset=800',
            'limits': '每页 100 条',
            'returns': 'records(market, code, name, ...)',
            'category': '基础信息',
        },
        {
            'method': 'quote',
            'description': '扩展市场实时报价',
            'tdxpy_method': 'TdxExHq_API.get_instrument_quote',
            'params': 'market, symbol',
            'limits': '—',
            'returns': 'records(market, code, price, ...)',
            'category': '实时数据',
        },
        {
            'method': 'bars',
            'description': '扩展市场K线',
            'tdxpy_method': 'TdxExHq_API.get_instrument_bars',
            'params': 'frequency, market, symbol, start, offset',
            'limits': '单次最多 800 根K线',
            'returns': 'records(datetime, open, high, low, close, vol, amount)',
            'category': '历史数据',
        },
        {
            'method': 'minute',
            'description': '扩展市场分时数据',
            'tdxpy_method': 'TdxExHq_API.get_minute_time_data',
            'params': 'market, symbol',
            'limits': '—',
            'returns': 'records(price, vol)',
            'category': '实时数据',
        },
        {
            'method': 'minutes',
            'description': '扩展市场历史分时',
            'tdxpy_method': 'TdxExHq_API.get_history_minute_time_data',
            'params': 'market, symbol, date',
            'limits': '—',
            'returns': 'records(price, vol)',
            'category': '历史数据',
        },
        {
            'method': 'transaction',
            'description': '扩展市场分笔成交',
            'tdxpy_method': 'TdxExHq_API.get_transaction_data',
            'params': 'market, symbol, start, offset',
            'limits': '—',
            'returns': 'records(time, price, vol, buyorsell)',
            'category': '实时数据',
        },
        {
            'method': 'transactions',
            'description': '扩展市场历史分笔',
            'tdxpy_method': 'TdxExHq_API.get_history_transaction_data',
            'params': 'market, symbol, date, start, offset',
            'limits': '—',
            'returns': 'records(time, price, vol, buyorsell)',
            'category': '历史数据',
        },
    ],

    # ===== 财务文件 (Affair) =====
    'affair': [
        {
            'method': 'Affair.files',
            'description': '远端财务文件列表（gpcw 文件清单 + hash）',
            'tdxpy_method': 'TdxHq_API.get_report_file_by_size',
            'params': '—',
            'limits': '连接财务专用服务器 120.76.152.87',
            'returns': 'list[dict(filename, hash, filesize)]',
            'category': '财务数据',
        },
        {
            'method': 'Affair.fetch',
            'description': '下载财务文件（单个或全部）',
            'tdxpy_method': 'TdxHq_API.get_report_file_by_size',
            'params': 'downdir, filename=None',
            'limits': '文件约 1-4MB/个',
            'returns': 'bool',
            'category': '财务数据',
        },
        {
            'method': 'Affair.parse',
            'description': '动态解析 gpcw 财务文件（三大报表 + 机构持仓 + 盈利预测；已知字段名 + colNN raw fallback）',
            'tdxpy_method': '本地二进制解析',
            'params': 'downdir, filename',
            'limits': '—',
            'returns': 'records(code, report_date, 基本每股收益, ... 全部 report_size 字段)',
            'category': '财务数据',
        },
    ],

    # ===== 离线读取 (Reader) =====
    'reader': [
        {
            'method': 'Reader.daily',
            'description': '读取通达信本地日线文件 (.day)',
            'tdxpy_method': 'TdxDailyBarReader',
            'params': 'symbol',
            'limits': '需本地安装通达信',
            'returns': 'records(open, high, low, close, vol, amount)',
            'category': '离线数据',
        },
        {
            'method': 'Reader.minute',
            'description': '读取通达信本地分钟文件 (.lc1/.lc5)',
            'tdxpy_method': 'TdxMinBarReader / TdxLCMinBarReader',
            'params': 'symbol, suffix="lc5"',
            'limits': '需本地安装通达信',
            'returns': 'records(open, high, low, close, vol, amount)',
            'category': '离线数据',
        },
        {
            'method': 'Reader.fzline',
            'description': '读取通达信本地分时线文件',
            'tdxpy_method': 'TdxMinBarReader',
            'params': 'symbol',
            'limits': '需本地安装通达信',
            'returns': 'records',
            'category': '离线数据',
        },
    ],
}

# ---------------------------------------------------------------------------
# 统计常量
# ---------------------------------------------------------------------------

TOTAL_STD_METHODS = len(CAPABILITIES['std_quotes'])
TOTAL_EXT_METHODS = len(CAPABILITIES['ext_quotes'])
TOTAL_AFFAIR_METHODS = len(CAPABILITIES['affair'])
TOTAL_READER_METHODS = len(CAPABILITIES['reader'])
TOTAL_METHODS = TOTAL_STD_METHODS + TOTAL_EXT_METHODS + TOTAL_AFFAIR_METHODS + TOTAL_READER_METHODS

CATEGORIES = {}
for _section in CAPABILITIES.values():
    for _cap in _section:
        cat = _cap['category']
        CATEGORIES.setdefault(cat, []).append(_cap['method'])

# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def summary(verbose=False):
    """打印 tdxhub 数据能力全景表"""

    sections = [
        ('标准行情 StdQuotes', 'std_quotes'),
        ('扩展行情 ExtQuotes', 'ext_quotes'),
        ('财务文件 Affair', 'affair'),
        ('离线读取 Reader', 'reader'),
    ]

    print('=' * 72)
    print('  tdxhub 数据能力全景图')
    print(f'  共计 {TOTAL_METHODS} 个 API 方法')
    print('=' * 72)

    for title, key in sections:
        caps = CAPABILITIES[key]
        print(f'\n▸ {title} ({len(caps)} 个方法)')
        print('-' * 72)

        for cap in caps:
            marker = '●'
            print(f'  {marker} {cap["method"]:<24s} {cap["description"]}')

            if verbose:
                print(f'    底层: {cap["tdxpy_method"]}')
                print(f'    参数: {cap["params"]}')
                print(f'    限制: {cap["limits"]}')
                print(f'    返回: {cap["returns"]}')
                print()

    print('\n' + '=' * 72)
    print('  按数据类别统计:')
    for cat, methods in sorted(CATEGORIES.items()):
        print(f'    {cat}: {len(methods)} 个方法')
    print('=' * 72)


def find(keyword):
    """按关键词搜索能力"""

    results = []
    keyword_lower = keyword.lower()

    for section_key, caps in CAPABILITIES.items():
        for cap in caps:
            searchable = f"{cap['method']} {cap['description']} {cap['category']} {cap['returns']}"
            if keyword_lower in searchable.lower():
                results.append({'section': section_key, **cap})

    return results


if __name__ == '__main__':
    summary(verbose=True)
