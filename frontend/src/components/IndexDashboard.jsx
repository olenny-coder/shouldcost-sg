import React, { useMemo, useState } from 'react';
import {
  CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import { money, num, currencySymbol } from '../format.js';
import HelpPopout from './HelpPopout.jsx';

const SERIES_COLORS = {
  BCA: '#2563eb', HDB: '#047857', RLB: '#d97706', AECOM: '#7c3aed', AIS: '#be123c',
  CPWD: '#2563eb', NBO: '#047857', 'WPI-CON': '#d97706',
};

const MATERIAL_LABEL = {
  cement: 'Cement',
  steel_rebar: 'Steel reinforcement',
  ready_mix_concrete: 'Ready-mixed concrete',
};

export default function IndexDashboard(props) {
  const tpiRows = props.tpiRows || [];
  const materialRows = props.materialRows || [];
  const benchmarkRates = props.benchmarkRates || [];
  const country = props.country || { name: 'Singapore', currency: 'SGD', base_year: 2010, sources: [] };
  const currency = props.currency || country.currency || 'SGD';
  const [material, setMaterial] = useState('steel_rebar');
  // DISPLAY-ONLY scenario multiplier. Material prices are not part of the rate
  // formula, so this never changes should-cost - it is labelled as such.
  const [materialScenarioPct, setMaterialScenarioPct] = useState(0);

  const tpiData = useMemo(function () {
    const byQuarter = {};
    const seriesNames = [];
    tpiRows.forEach(function (row) {
      if (!byQuarter[row.quarter]) byQuarter[row.quarter] = { quarter: row.quarter };
      byQuarter[row.quarter][row.series_name] = row.value;
      if (seriesNames.indexOf(row.series_name) === -1) seriesNames.push(row.series_name);
    });
    return {
      points: Object.keys(byQuarter).sort().map(function (q) { return byQuarter[q]; }),
      seriesNames: seriesNames.sort(),
    };
  }, [tpiRows]);

  const materialData = useMemo(function () {
    const factor = 1 + materialScenarioPct / 100;
    return materialRows
      .filter(function (row) { return row.material === material; })
      .map(function (row) {
        return {
          month: row.month,
          price: row.price * factor,
          published: row.price,
          unit: row.unit,
        };
      })
      .sort(function (a, b) { return a.month.localeCompare(b.month); });
  }, [materialRows, material, materialScenarioPct]);

  const allRows = tpiRows.concat(materialRows, benchmarkRates);
  const selectedUnit = (materialRows.filter(function (r) { return r.material === material; })[0] || {}).unit || '';
  // India publishes WPI cost indices; Singapore publishes actual prices. Say which.
  const materialIsIndex = selectedUnit.indexOf('index') === 0;
  const placeholderCount = allRows.filter(function (r) { return r.is_placeholder; }).length;
  const realCount = allRows.length - placeholderCount;

  // A series is REAL only when every one of its observations is real.
  const seriesQuality = useMemo(function () {
    const map = {};
    tpiRows.forEach(function (row) {
      const entry = map[row.series_name] || { real: true, note: '', url: '', placeholder: false };
      if (row.is_placeholder) { entry.real = false; entry.placeholder = true; entry.note = row.provenance_note || ''; }
      else if (!entry.note) { entry.note = row.provenance_note || ''; }
      if (!entry.url) entry.url = row.source_url;
      map[row.series_name] = entry;
    });
    return map;
  }, [tpiRows]);

  const baseYears = Array.from(new Set(tpiRows.map(function (r) { return r.base_year; }))).sort();
  const baseYearLabel = baseYears.length ? baseYears.join('/') : 'n/a';

  return (
    <section className="panel">
      <h2>
        Index dashboard - {country.name}
        <HelpPopout
          title="Reading the index dashboard"
          items={[
            'The first chart is every published index series for this market, all rebased so they can be compared on one axis.',
            'The table beneath it states what each series includes and excludes. A series that excludes a section cannot price that section - when your BoQ contains one, the benchmark holds it at base year and says so.',
            'Each series is badged real or placeholder. Real means the numbers came from the named official publication; placeholder means they are synthetic and carry a TODO.',
            'The material chart shows input costs. For Singapore these are actual prices from BCA; for India they are WPI cost indices, so the axis is index points, not rupees.',
            'The material scenario slider is display-only. It re-scales the chart and deliberately does not change should-cost, because the agreed formula has no material-escalation term.',
            'The rate library at the bottom is what actually prices your BoQ, with the source, date and confidence behind every section. All rates are placeholders pending a licensed schedule of rates.',
            'The sources table names the publications each market relies on. Refresh them with python -m app.importer.',
          ]}
          footnote="The app makes no outbound call to any index provider at runtime - all of this is served from the local database."
        />
      </h2>

      <div className="tile-grid">
        <div className="tile tile-good">
          <div className="tile-label">Real published data</div>
          <div className="tile-value">{realCount}</div>
          <div className="tile-sub">observations from a named official source</div>
        </div>
        <div className={placeholderCount ? 'tile tile-warn' : 'tile'}>
          <div className="tile-label">Synthetic placeholder</div>
          <div className="tile-value">{placeholderCount}</div>
          <div className="tile-sub">carry is_placeholder = true and a TODO</div>
        </div>
      </div>

      {placeholderCount ? (
        <div className="notice notice-warn">
          <strong>{placeholderCount} row(s) here are still synthetic.</strong> Each carries{' '}
          <code>is_placeholder: true</code>, a <code>source_url</code> naming the real publication it
          stands in for, and a <code># TODO</code> naming the value to fetch. Rows marked{' '}
          <span className="badge basis-measured">real</span> are actual published observations. The
          app makes no outbound call to any index provider at runtime - refresh data with{' '}
          <code>python -m app.importer</code>.
        </div>
      ) : (
        <div className="notice notice-ok">
          <strong>All of this data is real.</strong> Every observation comes from a named official
          source; see the provenance column.
        </div>
      )}

      <h3>Tender / construction cost index by series (base year {baseYearLabel} = 100)</h3>
      <div className="chart-card">
        <div style={{ width: '100%', height: 320 }}>
          <ResponsiveContainer>
            <LineChart data={tpiData.points} margin={{ top: 10, right: 20, bottom: 10, left: 6 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e3eefb" />
              <XAxis dataKey="quarter" tick={{ fontSize: 11 }} />
              <YAxis domain={['auto', 'auto']} tick={{ fontSize: 11 }} />
              <Tooltip />
              <Legend />
              {tpiData.seriesNames.map(function (name) {
                return (
                  <Line
                    key={name}
                    type="monotone"
                    dataKey={name}
                    stroke={SERIES_COLORS[name] || '#334155'}
                    strokeWidth={2}
                    dot={{ r: 3 }}
                  />
                );
              })}
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>

      <div className="table-scroll">
        <table className="data-table compact">
          <thead>
            <tr>
              <th>Series</th><th>Data</th><th>Scope inclusions</th><th>Scope exclusions</th>
              <th>Provenance</th>
            </tr>
          </thead>
          <tbody>
            {(props.seriesScopes || []).map(function (scope) {
              const quality = seriesQuality[scope.series_name] || {};
              return (
                <tr key={scope.series_name}>
                  <td><span className="section-pill">{scope.series_name}</span></td>
                  <td>
                    {quality.real
                      ? <span className="badge basis-measured">real</span>
                      : <span className="badge basis-assumed">placeholder</span>}
                  </td>
                  <td className="small">{scope.scope_inclusions}</td>
                  <td className="small">{scope.scope_exclusions}</td>
                  <td className="small">
                    {quality.note ? <div>{quality.note}</div> : null}
                    {quality.url ? (
                      <a href={quality.url} target="_blank" rel="noreferrer">{quality.url}</a>
                    ) : null}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="muted small">
        A section listed under scope exclusions is NOT re-priced by that series. When the BoQ
        contains such a section, the benchmark holds that section at base year and raises a named
        warning - see the assumptions panel.
      </p>

      <h3>{materialIsIndex ? 'Material cost index' : 'Material price trend'} - {country.name}</h3>
      <p className="muted small">
        {materialIsIndex
          ? 'For India the published series is a cost INDEX (2022-23 = 100), not a rupee price - the '
            + 'chart plots index points. The unit is shown against the axis.'
          : 'Published prices in ' + currency + ' per the unit shown.'}{' '}
        Period labels follow the source: an annual series shows the year, a monthly series shows
        year-month. {materialRows.filter(function (r) { return !r.is_placeholder; }).length} of{' '}
        {materialRows.length} observations here are real published data.
      </p>
      <div className="control-row">
        <label>Material
          <select value={material} onChange={function (e) { setMaterial(e.target.value); }}>
            {Object.keys(MATERIAL_LABEL).map(function (key) {
              return <option key={key} value={key}>{MATERIAL_LABEL[key]}</option>;
            })}
          </select>
        </label>
        <div className="field" style={{ flex: '1 1 240px' }}>
          <span>Scenario: shift displayed prices by {materialScenarioPct > 0 ? '+' : ''}{materialScenarioPct}%</span>
          <input
            type="range"
            min="-25"
            max="25"
            step="1"
            value={materialScenarioPct}
            onChange={function (e) { setMaterialScenarioPct(Number(e.target.value)); }}
          />
        </div>
        {materialScenarioPct !== 0 ? (
          <button type="button" className="secondary" onClick={function () { setMaterialScenarioPct(0); }}>
            Reset material scenario
          </button>
        ) : null}
      </div>
      <div className="chart-card">
        <div style={{ width: '100%', height: 290 }}>
          <ResponsiveContainer>
            <LineChart data={materialData} margin={{ top: 10, right: 20, bottom: 10, left: 6 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e3eefb" />
              <XAxis dataKey="month" tick={{ fontSize: 11 }} />
              <YAxis
                domain={['auto', 'auto']}
                tick={{ fontSize: 11 }}
                label={{ value: selectedUnit, angle: -90, position: 'insideLeft', fontSize: 10, fill: '#64809f' }}
              />
              <Tooltip
                formatter={function (value) {
                  return materialIsIndex ? num(value, 1) + ' index' : money(value, currency);
                }}
              />
              <Legend />
              <Line
                type="monotone"
                dataKey="published"
                name="Published (placeholder)"
                stroke="#94a3b8"
                strokeWidth={1.5}
                strokeDasharray="4 3"
                dot={false}
              />
              <Line
                type="monotone"
                dataKey="price"
                name={'Scenario ' + (materialScenarioPct > 0 ? '+' : '') + materialScenarioPct + '%'}
                stroke="#0f766e"
                strokeWidth={2.5}
                dot={{ r: 3 }}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>
      <div className="notice notice-info">
        <strong>Reference only.</strong> This material scenario does <strong>not</strong> change
        should-cost. The agreed formula contract is{' '}
        <code>base_rate x (current_index / base_index) x scope_factor</code>, which contains no
        material-escalation term - adding one on top of index adjustment would double-count the
        same input cost. See README "Known deviations".
      </div>

      <h3>Benchmark rate library</h3>
      <div className="table-scroll">
        <table className="data-table compact">
          <thead>
            <tr>
              <th>Section</th><th>Unit</th><th className="num">Base rate</th>
              <th>Source</th><th>Base yr</th><th>Confidence</th><th>Data</th>
            </tr>
          </thead>
          <tbody>
            {benchmarkRates.map(function (row) {
              return (
                <tr key={row.id}>
                  <td><span className="section-pill">{row.smm2_section}</span></td>
                  <td>{row.unit}</td>
                  <td className="num">{money(row.base_rate, currency)}</td>
                  <td className="small">
                    {row.source}
                    <div className="muted small">{row.source_date} - {row.classification_standard}</div>
                  </td>
                  <td>{row.base_year}</td>
                  <td>{row.confidence}</td>
                  <td>
                    {row.is_placeholder
                      ? <span className="badge basis-assumed">placeholder</span>
                      : <span className="badge basis-measured">real</span>}
                    {row.provenance_note
                      ? <div className="small muted" style={{ maxWidth: 320 }}>{row.provenance_note}</div>
                      : null}
                    {row.replace_with ? <div className="todo small">{row.replace_with}</div> : null}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <h3>Credible sources for {country.name}</h3>
      <p className="muted small">
        The placeholder values above stand in for these publications. None of them is fetched at
        runtime.
      </p>
      <div className="table-scroll">
        <table className="data-table compact">
          <thead>
            <tr><th>Source</th><th>What it provides</th></tr>
          </thead>
          <tbody>
            {(country.sources || []).map(function (source) {
              return (
                <tr key={source.name}>
                  <td>
                    <a href={source.url} target="_blank" rel="noreferrer">{source.name}</a>
                  </td>
                  <td className="small">{source.what}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="muted small">
        All rates above are denominated in <strong>{currency}</strong> ({currencySymbol(currency)}).
      </p>
    </section>
  );
}
