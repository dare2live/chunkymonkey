(function (global) {
  'use strict';

  var _inst = {
    dim: 'overview',
    data: []
  };
  // Step 5 任务 D：股票列表 state 只保留 signals_v2 主流程用得到的维度
  //   - gate: follow/watch/observe/avoid 执行档筛选
  //   - industry: TDX L1 行业筛选
  //   - sortMode: composite / notice
  // 删除的 legacy state（与筛选条瘦身同步）：
  //   filterSignal(setup A1-A5) / filterAttention / filterTurtle /
  //   filterScreening / filterDiscovery / filterQuality / filterStageScore /
  //   filterForecast
  var _stock = {
    data: [],
    summary: null,
    filterGate: 'all',
    filterIndustry: 'all',
    sortMode: 'composite'
  };
  var _industry = {
    data: [],
    summary: null
  };

  function setInstData(data) {
    _inst.data = Array.isArray(data) ? data : [];
    return _inst.data;
  }

  function setStockData(data) {
    _stock.data = Array.isArray(data) ? data : [];
    return _stock.data;
  }

  function setStockSummary(summary) {
    _stock.summary = summary || null;
    return _stock.summary;
  }

  function setIndustryData(data) {
    _industry.data = Array.isArray(data) ? data : [];
    return _industry.data;
  }

  global.AppListState = {
    inst: {
      getDim: function () { return _inst.dim; },
      setDim: function (dim) { _inst.dim = dim || 'overview'; return _inst.dim; },
      getData: function () { return _inst.data; },
      setData: setInstData,
    },
    stock: {
      getData: function () { return _stock.data; },
      setData: setStockData,
      getSummary: function () { return _stock.summary; },
      setSummary: setStockSummary,
      getFilterGate: function () { return _stock.filterGate; },
      setFilterGate: function (value) { _stock.filterGate = value || 'all'; return _stock.filterGate; },
      getFilterIndustry: function () { return _stock.filterIndustry; },
      setFilterIndustry: function (value) { _stock.filterIndustry = value || 'all'; return _stock.filterIndustry; },
      getSortMode: function () { return _stock.sortMode; },
      setSortMode: function (value) { _stock.sortMode = value || 'composite'; return _stock.sortMode; },
    },
    industry: {
      getData: function () { return _industry.data; },
      setData: setIndustryData,
      getSummary: function () { return _industry.summary; },
      setSummary: function (summary) { _industry.summary = summary || null; return _industry.summary; },
    }
  };
})(window);
