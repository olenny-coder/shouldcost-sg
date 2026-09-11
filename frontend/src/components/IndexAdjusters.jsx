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
  const activeSections = Object.keys(sectionMap).filter(function (k) { return Number(sectionMap[k]); });
  const isDirty = tpiScale !== 0 || rateScale !== 0 || overrideRaw !== null && overrideRaw !== undefined
    || activeSections.length > 0;

  function setSection(key, raw) {
    const next = Object.assign({}, sectionMap);
    const parsed = Number(raw);
    if (!raw || Number.isNaN(parsed) || parsed === 0) delete next[key];
    else next[key] = parsed;
    props.onChange({ section_rate_scale_pct: next });
  }

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
            'Any line you touch is reported as basis=assumed and flagged "index adjusted". Nothing adjusted is ever presented as measured.',
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
