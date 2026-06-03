from __future__ import annotations

from pathlib import Path
import subprocess
import textwrap

import pytest


pytestmark = pytest.mark.contract

REPO = Path(__file__).resolve().parents[3]


def test_stock_view_build_stock_index_is_single_pass_and_uses_current_maps():
    script = textwrap.dedent(
        r"""
        const fs = require('fs');
        const vm = require('vm');
        const file = process.argv[1];
        global.window = global;
        global.document = { getElementById: () => null };
        global.alert = () => {};
        vm.runInThisContext(fs.readFileSync(file, 'utf8'), { filename: file });

        const view = globalThis.StockView;
        if (!view || typeof view._buildStockIndex !== 'function') {
          throw new Error('StockView._buildStockIndex missing');
        }

        const byStock = [
          { stockCode: '000001', industry: 'T10', stockName: 'A', topEvent: { institutionType: '券商' } },
          { stockCode: '000002', industry: 'T01', stockName: 'B', topEvent: { institutionType: '券商' } },
          { stockCode: '000003', industry: 'T10', stockName: 'C', topEvent: { institutionType: '公募' } },
        ];
        const screeningMap = new Map([
          ['000001', { f1_hit: true, f3_hit: false, f5_hit: true }],
          ['000002', { f1_hit: false, f3_hit: true, f5_hit: false }],
        ]);
        const turtleMap = new Map([
          ['000001', { turtle_setup_state: '突破触发' }],
          ['000002', { turtle_setup_state: '等待形态' }],
          ['000003', { turtle_setup_state: '退出触发' }],
        ]);

        const index = view._buildStockIndex(byStock, screeningMap, turtleMap);
        const industries = JSON.stringify(index.industries);
        const instTypes = JSON.stringify(index.instTypes);
        const screening = JSON.stringify(index.screeningHits);
        const turtles = JSON.stringify(index.turtleHits);

        if (industries !== JSON.stringify([['T01', 1], ['T10', 2]])) throw new Error('industry ordering mismatch: ' + industries);
        if (instTypes !== JSON.stringify([['券商', 2], ['公募', 1]])) throw new Error('inst type ordering mismatch: ' + instTypes);
        if (screening !== JSON.stringify({ f1: 1, f3: 1, f5: 1 })) throw new Error('screening counts mismatch: ' + screening);
        if (turtles !== JSON.stringify({ breakout: 1, pre: 0, exit: 1, wait: 1 })) throw new Error('turtle counts mismatch: ' + turtles);
        if (index.stockCodes.size !== 3 || !index.stockCodes.has('000003')) throw new Error('coverage set mismatch');
        if (index.stockMap.get('000002')?.stockName !== 'B') throw new Error('stock map mismatch');
        """
    ).strip()

    result = subprocess.run(
        ["node", "-e", script, str(REPO / "assets/js/stock-view.js")],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout
