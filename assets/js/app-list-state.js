(function (global) {
  'use strict';

  var _inst = {
    dim: 'overview',
    data: []
  };
  var _stock = {
    data: [],
    summary: null,
    filterSignal: 'all',
    filterGate: 'all',
    filterAttention: 'all',
    filterTurtle: 'all',
    filterScreening: 'all',
    filterDiscovery: 'all',
    filterQuality: 'all',
    filterStageScore: 'all',
    filterForecast: 'all',
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
      getFilterSignal: function () { return _stock.filterSignal; },
      setFilterSignal: function (value) { _stock.filterSignal = value || 'all'; return _stock.filterSignal; },
      getFilterGate: function () { return _stock.filterGate; },
      setFilterGate: function (value) { _stock.filterGate = value || 'all'; return _stock.filterGate; },
      getFilterAttention: function () { return _stock.filterAttention; },
      setFilterAttention: function (value) { _stock.filterAttention = value || 'all'; return _stock.filterAttention; },
      getFilterTurtle: function () { return _stock.filterTurtle; },
      setFilterTurtle: function (value) { _stock.filterTurtle = value || 'all'; return _stock.filterTurtle; },
      getFilterScreening: function () { return _stock.filterScreening; },
      setFilterScreening: function (value) { _stock.filterScreening = value || 'all'; return _stock.filterScreening; },
      getFilterDiscovery: function () { return _stock.filterDiscovery; },
      setFilterDiscovery: function (value) { _stock.filterDiscovery = value || 'all'; return _stock.filterDiscovery; },
      getFilterQuality: function () { return _stock.filterQuality; },
      setFilterQuality: function (value) { _stock.filterQuality = value || 'all'; return _stock.filterQuality; },
      getFilterStageScore: function () { return _stock.filterStageScore; },
      setFilterStageScore: function (value) { _stock.filterStageScore = value || 'all'; return _stock.filterStageScore; },
      getFilterForecast: function () { return _stock.filterForecast; },
      setFilterForecast: function (value) { _stock.filterForecast = value || 'all'; return _stock.filterForecast; },
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