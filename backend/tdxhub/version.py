from . import __version__

# 并入本仓时改: 原处是模块顶层的裸 ``print(__version__)`` —— import 这个模块
# 就往 stdout 吐一行, 会污染任何解析 stdout 的调用方。加 __main__ 守卫,
# ``python -m tdxhub.version`` 的行为不变。
if __name__ == '__main__':
    print(__version__)
