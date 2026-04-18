(function () {
    // 浏览器检查
    if (typeof WebSocket != 'function') {
        window.onerror = function () { return true; }
        window.onload = function () {
            document.write('<div style="position:fixed;left:0;top:0;right:0;bottom:0;z-index:999999;\
            background:#fff;">您的浏览器版本太低，请使用最新版浏览器浏览本页面</div>');
        }
    }
})();