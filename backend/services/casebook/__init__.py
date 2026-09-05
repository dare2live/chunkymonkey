"""判例查询引擎 —— 系统的产品本体。

回答且只回答一件事: **一条人手写的选股公式, 历史上每次触发之后发生了什么,
在什么情形下表现更好。** 不产出策略、不拟合参数、不判定"够不够格"。

模块划分对应 casebook.yaml 的结构:
  outcome.py   —— 与策略无关的地基: 每股每日固定窗口结果 + 两个基线
                  (后续) signals.py / pair_stats.py / dossier.py

口径的唯一真相源是 `backend/config/casebook.yaml`, 不在代码里另设常量。
"""
