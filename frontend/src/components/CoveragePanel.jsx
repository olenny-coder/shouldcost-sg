import React from 'react';
import { money } from '../format.js';

const REASON_LABEL = {
  unclassified_section: 'Could not be classified to a section',
  no_benchmark_rate_for_section: 'No published rate for this section',
  unit_mismatch: 'BoQ unit does not match the library rate unit',
};

/**
 * Completes the benchmark.
 *
 * A line the library cannot price is carried at the tendered rate and contributes
 * ZERO tested variance - which is easy to miss and quietly flatters the result.
 * This panel names every such line and lets the analyst supply a rate, so the
 * benchmark can cover the whole BoQ.
 */
export default function CoveragePanel(props) {
  const result = props.result;
  const currency = props.currency || 'SGD';
  if (!result) return null;

  const lines = result.lines;
  const unbenchmarked = lines.filter(function (l) { return !l.is_benchmarked; });
  const benchmarked = lines.length - unbenchmarked.length;
  const coveragePct = lines.length ? (benchmarked / lines.length) * 100 : 100;
  const carriedValue = unbenchmarked.reduce(function (sum, l) { return sum + l.boq_amount; }, 0);
  const manualRates = props.manualRates || {};

  function setRate(line, patch) {
    const current = manualRates[String(line.item_id)] || {
      base_rate: '', indexed: true, note: '',
    };
    props.onChange(String(line.item_id), Object.assign({}, current, patch));
  }

  function clearRate(line) {
    props.onClear(String(line.item_id));
  }

  if (!unbenchmarked.length) {
    return (
      <section className="panel">
        <h2>Benchmark coverage</h2>
        <div className="notice notice-ok">
          <strong>Every line is benchmarked.</strong> All {lines.length} lines carry a
          should-cost figure, so the whole BoQ is tested.
          {Object.keys(manualRates).length
            ? ' ' + Object.keys(manualRates).length + ' line(s) use an analyst-supplied rate, which is ' +
              'disclosed as an assumption and tagged basis=assumed.'
            : ''}
        </div>
      </section>
    );
  }

  return (
    <section className="panel">
      <h2>Benchmark coverage
        <span className="badge basis-assumed" style={{ marginLeft: 10 }}>
          {unbenchmarked.length} line(s) untested
        </span>
      </h2>

      <div className="coverage-bar" title={coveragePct.toFixed(0) + '% of lines benchmarked'}>
        <div className="coverage-fill" style={{ width: coveragePct + '%' }} />
      </div>
      <p className="muted small">
        <strong>{benchmarked} of {lines.length}</strong> lines are benchmarked ({coveragePct.toFixed(0)}%).
        The other <strong>{unbenchmarked.length}</strong>, worth <strong>{money(carriedValue, currency)}</strong>,
        are held at the tendered rate and contribute <strong>zero tested variance</strong> - their cost is
        carried but never challenged. Supply a rate below to benchmark them, or reclassify them in the
        variance table.
      </p>

      <div className="coverage-list">
        {unbenchmarked.map(function (line) {
          const entry = manualRates[String(line.item_id)] || {};
          const hasRate = Number(entry.base_rate) > 0;
          return (
            <div key={line.item_id} className={hasRate ? 'coverage-row set' : 'coverage-row'}>
              <div className="coverage-desc">
                <strong>{line.raw_description}</strong>
                <div className="flag-row">
                  <span className="flag">line {line.item_id}</span>
                  <span className="flag">{line.smm2_section}</span>
                  <span className="flag">{line.unit}</span>
                  <span className="flag">qty {line.quantity}</span>
                  <span className="flag">{money(line.boq_rate, currency)} tendered</span>
                </div>
                <div className="muted small">{REASON_LABEL[line.exclusion_reason] || line.exclusion_reason}</div>
              </div>

              <label className="coverage-input">
                <span>Benchmark rate / {line.unit}</span>
                <input
                  type="number"
                  min="0"
                  step="0.01"
                  placeholder="not set"
                  value={entry.base_rate === undefined ? '' : entry.base_rate}
                  onChange={function (e) { setRate(line, { base_rate: e.target.value }); }}
                />
              </label>

              <label className="coverage-check">
                <input
                  type="checkbox"
                  checked={entry.indexed !== false}
                  onChange={function (e) { setRate(line, { indexed: e.target.checked }); }}
                />
                <span>
                  Index it
                  <em className="muted"> rate is at the library base year</em>
                </span>
              </label>

              <button type="button" className="secondary" disabled={!hasRate} onClick={function () { clearRate(line); }}>
                Clear
              </button>
            </div>
          );
        })}
      </div>

      <div className="notice notice-info">
        <strong>An analyst-supplied rate is an assumption, not evidence.</strong> Each one is tagged{' '}
        <code>basis: assumed</code>, flagged <em>manual rate</em> in the variance table, and listed in
        the assumptions and the exported report. Tick <em>Index it</em> when the rate is stated at the
        published library's base year (it is then indexed, scoped and regionally adjusted like any
        library rate); untick it when you are stating a rate at today's prices.
      </div>
    </section>
  );
}
