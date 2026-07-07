"""data_sources 包: sync_registry.yaml 驱动的采集入口 (sync_runner) + tushare 适配器.

2026-07-07 精简收口: 原多源 fallback registry 框架 (base.py/registry.py, priority/
capability清单/健康检查, P1 时期为一套多源 UI 设计) 唯一消费方(旧 updater UI 的
/api/data_sources/* 路由)已随 2026-06-24 重建物删, resolve()/list_sources()/
healthcheck_all() 全仓库 0 调用 — 整个 fallback 机制退役物删。sync_runner.py 原经
get_registry().get_source() 间接拿 TuShareSource 实例的那条线, 已改直接 import 实例化
(见 sync_runner.py::_adapter())。见 analysis/data_sources_registry_retirement_20260707.md
"""
