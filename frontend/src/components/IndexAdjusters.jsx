import React from 'react';
import HelpPopout from './HelpPopout.jsx';

/**
 * Manual index adjusters.
 *
 * Everything on this panel is an analyst SUPPLY-SIDE ASSUMPTION. The backend
 * marks every affected line basis="assumed" and restates each adjustment in
 * assumptions[], which the assumptions panel renders in full.
 */
const SECTION_KEYS = [
  'Preliminaries', 'Excavation', 'Piling', 'Concrete', 'Reinforcement',
  'Formwork', 'Masonry', 'Waterproofing', 'Plaster', 'M&E Containment',
];

function Slider(props) {
  const value = Number(props.value) || 0;
  return (
    <div className="adjuster">
      <div className="adjuster-head">
        <span>{props.label}</span>
        <span className="adjuster-value">{(value > 0 ? '+' : '') + value.toFixed(1)}%</span>
      </div>
      <input
        type="range"
        min={props.min}
        max={props.max}
        step="0.5"
        value={value}
        onChange={function (e) { props.onChange(Number(e.target.value)); }}
        disabled={props.disabled}
        aria-label={props.label}
      />
      <div className="adjuster-help">{props.help}</div>
    </div>
  );
}

export default function IndexAdjusters(props) {
  const a = props.adjustments || {};
  const tpiScale = Number(a.tpi_scale_pct) || 0;
  const rateScale = Number(a.base_rate_scale_pct) || 0;
  const overrideRaw = a.tpi_value_override;
  const sectionMap = a.section_rate_scale_pct || {};
  const overheadPct = Number(a.overhead_pct) || 0;
  const marginPct = Number(a.margin_pct) || 0;
  const overheadsInTender = a.overheads_in_tender !== false;
  const ohpActive = overheadPct !== 0 || marginPct !== 0;
  const activeSections = Object.keys(sectionMap).filter(function (k) { return Number(sectionMap[k]); });
  const isDirty = tpiScale !== 0 || rateScale !== 0 || overrideRaw !== null && overrideRaw !== undefined
    || activeSections.length > 0 || ohpActive;

  function setSection(key, raw) {
    const next = Object.assign({}, sectionMap);
    const parsed = Number(raw);
    if (!raw || Number.isNaN(parsed) || parsed === 0) delete next[key];
    else next[key] = parsed;
    props.onChange({ section_rate_scale_pct: next });
  }

  // Full-cost readout. The engine's own figure is used whenever a run exists: it
  // grosses up the BENCHMARKED lines only, so recomputing it here from the whole
  // benchmark cost would overstate it by the unbenchmarked lines' share.
  const benchmarkCost = props.benchmarkCost;
  const engineFullCost = props.fullShouldCost;
  const estimate = benchmarkCost === null || benchmarkCost === undefined
    ? null
    : benchmarkCost * (1 + overheadPct / 100) * (1 + marginPct / 100);
  const money = function (value) {
    if (value === null || value === undefined) return 'n/a';
    return new Intl.NumberFormat(props.currency === 'INR' ? 'en-IN' : 'en-SG', {
      style: 'currency', currency: props.currency || 'SGD',
      minimumFractionDigits: 2, maximumFractionDigits: 2,
    }).format(value);
  };

  return (
    <section className={'panel' + (isDirty ? ' adjusters-dirty' : '')}>
      <h2>Index adjusters
        <HelpPopout
          title="Using the index adjusters"
          items={[
            'These move the inputs the benchmark is built on. Every field here is YOUR assumption, not a published value.',
            'Index shift moves the selected index series up or down. It answers "what if tender prices move 5%?" without touching the underlying data.',
            'All benchmark rates applies one shift to every section at once - useful for a general market movement.',
            'Absolute index override replaces the published index value outright. The percentage shift is then applied on top of it. Leave it blank to use the published value.',
            'Per-section shifts let one trade move on its own - steel rising while labour is flat, for example.',
            'Overheads and margin turn the benchmark cost into a FULL should-cost: full rate = benchmark rate x (1 + overheads%) x (1 + margin%). Margin is applied after overheads, so it compounds on them - the usual commercial convention.',
            'Overheads and margin are added to the benchmarked lines only. Lines held at the tendered rate are not grossed up, because that rate already carries the contractor\'s own OH&P.',
            '"Tendered rates include OH&P" decides what the variance is measured against: leave it ticked for a full-to-full comparison, untick it if your tender rates are net of overheads and profit, and the variance reverts to the benchmark rate before overheads.',
            'Any line you touch is reported as basis=assumed and flagged. Nothing adjusted is ever presented as measured.',
            'Set index to break-even uses the sensitivity result to move the index to the level where should-cost equals the tender. Run the sensitivity tab first.',
          ]}
          footnote="Everything you change here is restated in the assumptions panel and in the exported report."
        />
        {isDirty ? <span className="badge basis-assumed">assumptions active</span> : null}
      </h2>
      <p className="muted small">
        Move the published index or the benchmark rates and the whole benchmark re-runs live.
        Any line you touch becomes <strong>basis: assumed</strong> - these are scenario inputs, not
        observations, and they are restated in the assumptions panel below.
      </p>

      {props.indexBridge && props.indexBridge.applied ? (
        <p className="small bridge-line">
          <strong>Index already bridged.</strong> The {props.indexBridge.index_series} index for{' '}
          {props.indexBridge.requested_quarter} is not a published observation: it is the{' '}
          {props.indexBridge.observation_quarter} value carried forward to{' '}
          {props.indexBridge.bridged_through_month} with the {props.indexBridge.cpi_series_name} (
          x{Number(props.indexBridge.cpi_bridge_factor).toFixed(4)}), giving{' '}
          {Number(props.indexBridge.index_value_used).toFixed(2)} against a published{' '}
          {Number(props.indexBridge.index_value_published).toFixed(2)}. Your shifts below are
          applied on top of that bridged level. Set the override to replace it outright.
        </p>
      ) : null}

      <div className="adjuster-grid">
        <Slider
          label={'Index shift (' + (props.tpiSeries || 'index') + ')'}
          value={tpiScale}
          min={-40}
          max={40}
          disabled={props.disabled}
          onChange={function (v) { props.onChange({ tpi_scale_pct: v }); }}
          help={
            'Applies to ' + (props.tpiSeries || 'the selected index') + ' for '
            + (props.tpiQuarter || 'the tender quarter')
            + (props.publishedTpi ? '. Published value ' + Number(props.publishedTpi).toFixed(2) + '.' : '.')
          }
        />
        <Slider
          label="All benchmark rates"
          value={rateScale}
          min={-40}
          max={40}
          disabled={props.disabled}
          onChange={function (v) { props.onChange({ base_rate_scale_pct: v }); }}
          help="Applies to every benchmark base rate, on top of any per-section shift."
        />
        <Slider
          label="Overheads"
          value={overheadPct}
          min={0}
          max={60}
          disabled={props.disabled}
          onChange={function (v) { props.onChange({ overhead_pct: v }); }}
          help="Site and head-office overhead recovery, as a percentage of the benchmark cost."
        />
        <Slider
          label="Margin (profit)"
          value={marginPct}
          min={0}
          max={40}
          disabled={props.disabled}
          onChange={function (v) { props.onChange({ margin_pct: v }); }}
          help="Applied AFTER overheads, so it compounds on them."
        />
        <div className="adjuster">
          <div className="adjuster-head">
            <span>Absolute index override</span>
            <span className="adjuster-value">{overrideRaw === null || overrideRaw === undefined ? 'off' : Number(overrideRaw).toFixed(2)}</span>
          </div>
          <input
            type="number"
            step="0.1"
            min="1"
            placeholder="published value"
            value={overrideRaw === null || overrideRaw === undefined ? '' : overrideRaw}
            onChange={function (e) {
              const raw = e.target.value;
              props.onChange({ tpi_value_override: raw === '' ? null : Number(raw) });
            }}
            disabled={props.disabled}
            aria-label="Absolute index override"
          />
          <div className="adjuster-help">
            Replaces the stored index value outright. The percentage shift above is then applied on
            top of it. Leave blank to use the published value.
          </div>
        </div>
      </div>

      <div className="ohp-block">
        <label className="ohp-toggle">
          <input
            type="checkbox"
            checked={overheadsInTender}
            onChange={function (e) { props.onChange({ overheads_in_tender: e.target.checked }); }}
            disabled={props.disabled}
          />
          <span>
            <strong>Tendered rates include OH&amp;P.</strong> With this ticked, each line's variance
            is measured <em>full-to-full</em>: the tendered rate against the benchmark rate grossed
            up by the same percentages. Untick it if your tender rates are net of overheads and
            profit, and the variance reverts to the benchmark rate before overheads - the full
            should-cost is still reported.
          </span>
        </label>
        <p className={'small ' + (ohpActive ? 'ohp-readout' : 'muted')}>
          {ohpActive ? (
            <>
              <strong>Full should-cost:</strong>{' '}
              {engineFullCost !== null && engineFullCost !== undefined ? (
                <>
                  {money(benchmarkCost)} benchmark cost grossed up by{' '}
                  {overheadPct.toFixed(1)}% overheads and then {marginPct.toFixed(1)}% margin ={' '}
                  <strong>{money(engineFullCost)}</strong> (the engine's own figure: benchmarked
                  lines only, since lines held at the tendered rate already carry OH&amp;P).
                </>
              ) : (
                <>
                  run the benchmark to see it. At these percentages the benchmark cost{' '}
                  {money(benchmarkCost)} would gross up to about {money(estimate)}, before the
                  unbenchmarked lines are excluded.
                </>
              )}{' '}
              Overheads and margin appear as their own assumed steps in the waterfall.
            </>
          ) : (
            <>
              Overheads and margin are off, so the benchmark cost <em>is</em> the full
              should-cost. Add a percentage above to build a commercial full cost: overheads and
              margin are the two inputs a rate library cannot supply.
            </>
          )}
        </p>
      </div>

      <details style={{ marginTop: 14 }}>
        <summary style={{ cursor: 'pointer', fontSize: 13, fontWeight: 700, color: 'var(--blue-700)' }}>
          Per-section rate shifts{activeSections.length ? ' (' + activeSections.length + ' active)' : ''}
        </summary>
        <div className="section-adjusters">
          {SECTION_KEYS.map(function (key) {
            return (
              <label key={key}>
                <span>{key}</span>
                <input
                  type="number"
                  step="1"
                  min="-90"
                  max="300"
                  placeholder="0"
                  value={sectionMap[key] === undefined ? '' : sectionMap[key]}
                  onChange={function (e) { setSection(key, e.target.value); }}
                  disabled={props.disabled}
                  aria-label={key + ' rate shift percent'}
                />
              </label>
            );
          })}
        </div>
      </details>

      <div className="adjuster-actions">
        <button type="button" className="secondary" onClick={props.onReset} disabled={!isDirty}>
          Reset all adjusters
        </button>
        <button type="button" className="secondary" onClick={props.onResetAndUseBreakEven}
                disabled={props.disabled || props.breakEvenPct === null || props.breakEvenPct === undefined}>
          {props.breakEvenPct === null || props.breakEvenPct === undefined
            ? 'Set index to break-even (run sensitivity first)'
            : 'Set index to break-even (' + (props.breakEvenPct > 0 ? '+' : '') + Number(props.breakEvenPct).toFixed(2) + '%)'}
        </button>
        {isDirty ? <span className="muted small">Adjustments are live.</span> : null}
      </div>
    </section>
  );
}
