import React, { useState } from 'react';
import {
  Bar, BarChart, CartesianGrid, Cell, Legend, Line, LineChart, ReferenceLine,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import { compactMoney, money, num, pct } from '../format.js';
import HelpPopout from './HelpPopout.jsx';

const NEGATIVE = '#b91c1c';
const POSITIVE = '#2563eb';

/**
 * Sensitivity analysis.
 *
 * Top chart: what happens to should-cost as the index moves, with the tendered
 * BoQ total drawn as a reference line - where the curve crosses it is the
 * break-even index level.
 *
 * Bottom chart: a tornado. Each section's rate is moved +/-N% in isolation, so
 * the bars show which sections actually drive the answer.
 */
export default function SensitivityView(props) {
  const [min, setMin] = useState('-20');
  const [max, setMax] = useState('20');
  const [step, setStep] = useState('5');
  const [sectionScale, setSectionScale] = useState('10');
  const currency = props.currency || 'SGD';
  const data = props.sensitivity;

  function run() {
    props.onRun({
      tpi_scale_min_pct: Number(min),
      tpi_scale_max_pct: Number(max),
      tpi_scale_step_pct: Number(step),
      section_scale_pct: Number(sectionScale),
    });
  }

  return (
    <section className="panel">
      <h2>
        Sensitivity analysis
        <HelpPopout
          title="Running a sensitivity analysis"
          items={[
            'This answers "how wrong can I be?" It varies the index across a range and shows what happens to should-cost and to the number of lines breaching the threshold.',
            'Set the index range and step, then run. Each point is a full re-run of the benchmark, so the centre of the sweep always equals the variance table exactly.',
            'The first chart plots should-cost against the index. The dashed line is the tendered BoQ total. Where the blue curve crosses it is the break-even index level.',
            'The break-even tile states that crossing as a percentage shift and as an index value - the answer to "how much would prices have to move before this tender is fair?"',
            'The tornado chart moves one section\'s rate at a time by the stress percentage. The longest bars are the sections that actually drive your answer, and where to spend your checking effort.',
            'The sweep table adds the count of over- and under-priced lines at each index level, so you can see when a tolerance stops holding.',
            'The centre of the sweep is the index level actually used. If the index series has not published the tender quarter yet, that level is the last published observation bridged with the consumer price index - the banner below says so, and the sweep moves around the bridged level.',
            'Every point is a scenario you chose, not a forecast. Nothing here is a prediction of what prices will do.',
          ]}
          footnote="Set index to break-even on the adjusters panel to carry the break-even level into the variance table."
        />
      </h2>
      <p className="muted small">
        Scenario analysis, not a forecast. The sweep moves the index across a range; the tornado
        moves one section's rate at a time. Every point comes from the same engine as the headline
        benchmark, so the centre of the sweep equals the variance table to the cent.
      </p>

      {data && data.index_bridge && data.index_bridge.applied ? (
        <div className="notice notice-info">
          <strong>This sweep runs on a bridged index.</strong> The{' '}
          {data.index_bridge.index_series} series last published{' '}
          {data.index_bridge.observation_quarter}; the centre of the sweep is that value carried
          forward to {data.index_bridge.bridged_through_month} with{' '}
          {data.index_bridge.cpi_series_name} (factor{' '}
          {Number(data.index_bridge.cpi_bridge_factor).toFixed(4)}), giving{' '}
          {Number(data.baseline_tpi_value).toFixed(2)} from a published{' '}
          {Number(data.baseline_tpi_value_published).toFixed(2)}. The bridged part of the level is a
          modelled assumption, so treat the whole sweep as resting on it.
        </div>
      ) : null}

      <div className="control-row" style={{ marginTop: 12 }}>
        <label>Index from (%)
          <input type="number" step="1" value={min} onChange={function (e) { setMin(e.target.value); }} />
        </label>
        <label>Index to (%)
          <input type="number" step="1" value={max} onChange={function (e) { setMax(e.target.value); }} />
        </label>
        <label>Step (%)
          <input type="number" step="1" min="0.5" value={step} onChange={function (e) { setStep(e.target.value); }} />
        </label>
        <label>Section stress (+/-%)
          <input type="number" step="1" min="1" value={sectionScale} onChange={function (e) { setSectionScale(e.target.value); }} />
        </label>
        <button type="button" className="primary" onClick={run} disabled={props.loading || !props.uploadId}>
          {props.loading ? 'Running...' : 'Run sensitivity'}
        </button>
      </div>

      {!data && !props.loading ? (
        <div className="notice notice-info">
          Run the analysis to see how much the should-cost moves when the index moves, and which
          sections carry the most risk.
        </div>
      ) : null}

      {data ? (
        <>
          <div className="tile-grid" style={{ marginTop: 16 }}>
            <div className="tile">
              <div className="tile-label">Break-even index shift</div>
              <div className="tile-value">
                {data.break_even_scale_pct === null ? 'n/a' : pct(data.break_even_scale_pct)}
              </div>
              <div className="tile-sub">
                {data.break_even_tpi_value === null
                  ? 'no break-even in range'
                  : 'index value ' + num(data.break_even_tpi_value, 2)}
              </div>
            </div>
            <div className="tile">
              <div className="tile-label">Most sensitive section</div>
              <div className="tile-value" style={{ fontSize: 18 }}>{data.most_sensitive_section || 'n/a'}</div>
              <div className="tile-sub">
                {data.section_tornado.length
                  ? 'swing ' + money(data.section_tornado[0].swing, currency)
                  : 'no benchmarked sections'}
              </div>
            </div>
            <div className="tile">
              <div className="tile-label">Sweep points</div>
              <div className="tile-value">{data.tpi_sweep.length}</div>
              <div className="tile-sub">
                index {num(data.tpi_sweep[0].tpi_value, 2)} to{' '}
                {num(data.tpi_sweep[data.tpi_sweep.length - 1].tpi_value, 2)}
              </div>
            </div>
            <div className="tile">
              <div className="tile-label">Region</div>
              <div className="tile-value" style={{ fontSize: 17 }}>{data.region_name || 'n/a'}</div>
              <div className="tile-sub">rate multiplier x{num(data.regional_factor, 3)}</div>
            </div>
          </div>

          <h3>Should-cost as the index moves</h3>
          <div className="chart-card">
            <div style={{ width: '100%', height: 320 }}>
              <ResponsiveContainer>
                <LineChart data={data.tpi_sweep} margin={{ top: 10, right: 20, bottom: 10, left: 10 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#e3eefb" />
                  <XAxis dataKey="tpi_scale_pct" tickFormatter={function (v) { return v + '%'; }} tick={{ fontSize: 11 }} />
                  <YAxis tickFormatter={function (v) { return compactMoney(v, currency); }} width={92} tick={{ fontSize: 11 }} />
                  <Tooltip
                    formatter={function (value) { return money(value, currency); }}
                    labelFormatter={function (v) { return 'index shift ' + v + '%'; }}
                  />
                  <Legend />
                  <ReferenceLine
                    y={data.baseline.boq_total}
                    stroke="#b45309"
                    strokeDasharray="6 3"
                    label={{ value: 'BoQ tender total', position: 'insideTopRight', fontSize: 11, fill: '#b45309' }}
                  />
                  <Line
                    type="monotone"
                    dataKey="should_cost_total"
                    name="Should-cost total"
                    stroke="#2563eb"
                    strokeWidth={2.5}
                    dot={{ r: 3 }}
                  />
                  <Line
                    type="monotone"
                    dataKey="total_variance_abs"
                    name="Variance to BoQ"
                    stroke="#7c3aed"
                    strokeWidth={1.5}
                    strokeDasharray="4 3"
                    dot={false}
                  />
                </LineChart>
              </ResponsiveContainer>
            </div>
            <p className="muted small">
              Where the blue curve crosses the dashed BoQ line is the break-even index level.
            </p>
          </div>

          <h3>Tornado - which section moves the answer</h3>
          <div className="chart-card">
            <div style={{ width: '100%', height: Math.max(280, data.section_tornado.length * 38 + 90) }}>
              <ResponsiveContainer>
                <BarChart
                  layout="vertical"
                  data={data.section_tornado}
                  margin={{ top: 10, right: 24, bottom: 10, left: 10 }}
                >
                  <CartesianGrid strokeDasharray="3 3" stroke="#e3eefb" horizontal={false} />
                  <XAxis type="number" tickFormatter={function (v) { return compactMoney(v, currency); }} tick={{ fontSize: 11 }} />
                  <YAxis type="category" dataKey="smm2_section" width={130} tick={{ fontSize: 11 }} />
                  <Tooltip formatter={function (value) { return money(value, currency); }} />
                  <Legend />
                  <ReferenceLine x={0} stroke="#94a3b8" />
                  <Bar dataKey="delta_low" name={'Rate -' + num(data.section_scale_pct, 1) + '%'} fill={NEGATIVE} />
                  <Bar dataKey="delta_high" name={'Rate +' + num(data.section_scale_pct, 1) + '%'} fill={POSITIVE} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>

          <h3>Sweep detail</h3>
          <div className="table-scroll">
            <table className="data-table compact">
              <thead>
                <tr>
                  <th className="num">Index shift</th>
                  <th className="num">Index value</th>
                  <th className="num">Should-cost total</th>
                  <th className="num">Variance</th>
                  <th className="num">Variance %</th>
                  <th className="num">Over</th>
                  <th className="num">Under</th>
                  <th className="num">Breaching</th>
                </tr>
              </thead>
              <tbody>
                {data.tpi_sweep.map(function (point) {
                  return (
                    <tr key={point.tpi_scale_pct} className={point.is_baseline ? 'row-total' : ''}>
                      <td className="num">{pct(point.tpi_scale_pct)}</td>
                      <td className="num">{num(point.tpi_value, 2)}</td>
                      <td className="num">{money(point.should_cost_total, currency)}</td>
                      <td className={'num ' + (point.total_variance_abs > 0 ? 'danger' : 'good')}>
                        {money(point.total_variance_abs, currency)}
                      </td>
                      <td className="num">{pct(point.total_variance_pct)}</td>
                      <td className="num">{point.lines_over}</td>
                      <td className="num">{point.lines_under}</td>
                      <td className="num">{point.breached_line_count}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <h3>Section detail</h3>
          <div className="table-scroll">
            <table className="data-table compact">
              <thead>
                <tr>
                  <th>Section</th>
                  <th className="num">BoQ</th>
                  <th className="num">Should-cost</th>
                  <th className="num">Share</th>
                  <th className="num">Rate -{num(data.section_scale_pct, 1)}%</th>
                  <th className="num">Rate +{num(data.section_scale_pct, 1)}%</th>
                  <th className="num">Swing</th>
                </tr>
              </thead>
              <tbody>
                {data.section_tornado.map(function (row) {
                  return (
                    <tr key={row.smm2_section}>
                      <td><span className="section-pill">{row.smm2_section}</span></td>
                      <td className="num">{money(row.boq_amount, currency)}</td>
                      <td className="num">{money(row.should_cost_amount, currency)}</td>
                      <td className="num">{num(row.share_of_should_cost_pct, 1)}%</td>
                      <td className="num danger">{money(row.delta_low, currency)}</td>
                      <td className="num">{money(row.delta_high, currency)}</td>
                      <td className="num"><strong>{money(row.swing, currency)}</strong></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {data.assumptions && data.assumptions.length ? (
            <div className="notice notice-warn" style={{ marginTop: 16 }}>
              <strong>Scenario assumptions.</strong>
              <ul className="assumption-list">
                {data.assumptions.slice(-2).map(function (text, i) { return <li key={i}>{text}</li>; })}
              </ul>
            </div>
          ) : null}
        </>
      ) : null}
    </section>
  );
}
