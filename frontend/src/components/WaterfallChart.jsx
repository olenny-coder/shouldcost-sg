import React, { useMemo, useState } from 'react'
import {
  Bar, BarChart, CartesianGrid, Cell, LabelList, Legend, ReferenceLine,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import { compactMoney as compactFmt, money as moneyFmt, num, BASIS_LABEL } from '../format.js'
import HelpPopout from './HelpPopout.jsx'

const COMPONENT_LABEL = {
  material: 'Material (assumed split)',
  labour: 'Labour (assumed split)',
  market_risk: 'Market risk (published index movement)',
  cpi_bridge: 'Index carried forward to the tender quarter',
  scope: 'Scope (excluded sections)',
  overhead: 'Overheads (analyst %)',
  margin: 'Margin (analyst %)',
  unexplained: 'Unexplained residual',
}

// Bars run through one blue family so the chart reads as a single movement.
// The opening and closing totals are the deepest tones; the components step
// through progressively lighter blues. BASIS is not encoded in the fill - it is
// carried by the value labels and the basis chips under the axis, and in full in
// the reconciliation table.
const COMPONENT_SHORT = {
  material: 'Material',
  labour: 'Labour',
  market_risk: 'Market risk',
  cpi_bridge: 'Index carried forward',
  scope: 'Scope',
  overhead: 'Overheads',
  margin: 'Margin',
  unexplained: 'Unexplained',
}

const COLOR = {
  boq: '#12325c',
  should: '#0e7490',
  material: '#1d4ed8',
  labour: '#2563eb',
  market_risk: '#3b82f6',
  cpi_bridge: '#0e7490',
  scope: '#60a5fa',
  overhead: '#7c3aed',
  margin: '#a855f7',
  unexplained: '#93c5fd',
}

const LABEL_COLOR = {
  measured: '#065f46',
  derived: '#1d4ed8',
  assumed: '#b45309',
}

function WaterfallTooltip(props) {
  const payload = props.payload
  if (!payload || !payload.length) return null
  const row = payload[0].payload
  const money = function (value) { return moneyFmt(value, props.currency) }
  return (
    <div className="chart-tooltip">
      <strong>{row.name}</strong>
      <div>{row.kind === 'total' ? money(row.delta) : (row.signed >= 0 ? '+' : '') + money(row.signed)}</div>
      <div className="small">basis: {BASIS_LABEL[row.basis] || row.basis}</div>
      <div className="small">method: {row.method}</div>
      <div className="small muted">{row.justification}</div>
      {row.kind === 'step' ? <div className="small">running: {money(row.runningAfter)}</div> : null}
    </div>
  )
}

/**
 * Reconciliation waterfall: BoQ tender total -> FULL should-cost total.
 *
 * Identity enforced by the backend and restated here:
 *   boq_total + material + labour + market_risk + cpi_bridge + scope + overhead
 *     + margin + unexplained = full_should_cost_total
 *
 * `cpi_bridge` is the modelled step that carries the last published index
 * observation forward to the tender quarter using the national consumer price
 * index. `overhead` and `margin` are the analyst's commercial percentages. All
 * three are basis=assumed - never measured observations. When no overheads or
 * margin were supplied both steps are exactly zero and the closing bar is the
 * benchmark should-cost, so a run without them reads exactly as before.
 */
export default function WaterfallChart(props) {
  const result = props.result
  const [showTable, setShowTable] = useState(true)
  const currency = props.currency || result.currency || 'SGD'
  // Currency-bound aliases, so every call site below stays unchanged.
  const money = function (value) { return moneyFmt(value, currency) }
  const compactMoney = function (value) { return compactFmt(value, currency) }
  const ohpActive = !!(result.totals.overhead_pct || result.totals.margin_pct)
  const fullTotal = result.totals.full_should_cost_total !== undefined
    && result.totals.full_should_cost_total !== null
    ? result.totals.full_should_cost_total
    : result.totals.should_cost_total

  const rows = useMemo(function () {
    const out = []
    let running = result.totals.boq_total
    out.push({
      name: 'BoQ tender total',
      shortName: 'BoQ total',
      colorKey: 'boq',
      base: 0,
      delta: result.totals.boq_total,
      kind: 'total',
      basis: 'measured',
      method: 'sum(quantity x boq_rate)',
      justification: 'Total of the uploaded Bill of Quantities as tendered.',
      runningAfter: result.totals.boq_total,
    })
    result.waterfall.forEach(function (component) {
      const start = running
      const end = running + component.amount
      out.push({
        name: COMPONENT_LABEL[component.component] || component.component,
        shortName: COMPONENT_SHORT[component.component] || component.component,
        colorKey: component.component,
        base: Math.min(start, end),
        delta: Math.abs(component.amount),
        signed: component.amount,
        kind: 'step',
        basis: component.basis,
        method: component.method,
        justification: component.justification,
        runningAfter: end,
      })
      running = end
    })
    out.push({
      name: ohpActive ? 'Full should-cost total' : 'Should-cost total',
      shortName: ohpActive ? 'Full should-cost' : 'Should-cost',
      colorKey: 'should',
      base: 0,
      delta: fullTotal,
      kind: 'total',
      basis: ohpActive ? 'assumed' : 'derived',
      method: ohpActive
        ? 'sum(quantity x full_adjusted_benchmark_rate)'
        : 'sum(quantity x adjusted_benchmark_rate)',
      justification: ohpActive
        ? 'Benchmark-derived cost grossed up by the analyst\'s overhead and margin percentages. The two OH&P steps are assumptions, so this closing total is assumed rather than derived.'
        : 'Benchmark-derived should-cost for the same scope.',
      runningAfter: fullTotal,
    })
    return out
  }, [result, ohpActive, fullTotal])

  // Draw each bar's amount above it, coloured by basis so the assumed bars stay
  // visually flagged even though every bar is now a shade of blue.
  function renderValueLabel(props) {
    const row = rows[props.index]
    if (!row) return null
    const amount = row.kind === 'total' ? row.delta : row.signed
    const prefix = row.kind === 'step' && amount > 0 ? '+' : ''
    return (
      <text
        x={props.x + props.width / 2}
        y={props.y - 7}
        textAnchor="middle"
        fontSize={11}
        fontWeight={700}
        fill={LABEL_COLOR[row.basis] || '#1d4ed8'}
      >
        {prefix + compactMoney(amount, currency)}
      </text>
    )
  }

  const reconciled = Math.abs(
    result.totals.boq_total + result.waterfall.reduce(function (sum, c) { return sum + c.amount }, 0)
      - fullTotal
  ) <= 0.01

  return (
    <section className="panel">
      <h2>
        Waterfall - BoQ tender total to should-cost
        <HelpPopout
          title="Reading the waterfall"
          items={[
            'Read it left to right. The first bar is the BoQ as tendered; the last is the benchmark should-cost. Everything between is a step from one to the other.',
            'A blue bar rising means that step pushed should-cost above the tender; falling means it pulled it below.',
            'The number above each bar is that step\'s amount. Its colour states the basis: green measured, blue derived, amber assumed.',
            'The small chips under the axis repeat each step\'s basis. Amber steps are not evidence - they are apportioned, bridged or analyst-supplied.',
            'Overheads and margin, when you set them, are the last two steps before the closing bar, and the closing bar is then the FULL should-cost.',
            'Hover any bar for the method that produced it and the full justification.',
            'If the index series has not published the tender quarter yet, it is carried forward along the published movement of a price index - a producer price index first, and a consumer price index only where the market publishes no usable producer series. That step appears as its own amber bar, because it is derived rather than observed.',
            'The banner above the chart must read "Reconciled". If it does not, treat every figure as unreliable.',
            'The reconciliation table below carries the same numbers in text form, with the method and justification spelled out.',
          ]}
          footnote="The identity is boq_total + material + labour + market_risk + cpi_bridge + scope + overhead + margin + unexplained = full_should_cost_total."
        />
      </h2>
      <p className="muted">
        One blue family, light to dark: the deepest bars are the opening BoQ total and the closing
        should-cost total. <strong>Amber numbers and chips mark assumed steps</strong> - apportioned,
        carried forward or analyst-supplied, never measured.
      </p>

      {result.index_bridge && result.index_bridge.applied ? (
        <div className="notice notice-info">
          <strong>The index was carried forward to the tender quarter.</strong>{' '}
          {result.index_bridge.index_series} last published{' '}
          <strong>{result.index_bridge.observation_quarter}</strong>; for{' '}
          {result.index_bridge.requested_quarter} it was carried forward to{' '}
          {result.index_bridge.bridged_through_month} along the published trend of{' '}
          {result.index_bridge.series_name || result.index_bridge.cpi_series_name} (factor{' '}
          {Number(result.index_bridge.cpi_bridge_factor).toFixed(4)}). That step is the
          "Index carried forward" bar, and it is <strong>assumed</strong>, not measured.
        </div>
      ) : null}

      <div className={reconciled ? 'notice notice-ok' : 'notice notice-critical'}>
        {reconciled
          ? 'Reconciled: BoQ total + adjustments = ' + (ohpActive ? 'full should-cost total' : 'should-cost total')
            + ', within 0.01 ' + currency + '.'
          : 'WARNING: the waterfall does not reconcile to the cent. Treat these figures as unreliable.'}
      </div>

      <div style={{ width: '100%', height: 440 }}>
        <ResponsiveContainer>
          <BarChart data={rows} margin={{ top: 28, right: 24, bottom: 84, left: 24 }}>
            <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#e3eefb" />
            <XAxis
              dataKey="name"
              angle={-25}
              textAnchor="end"
              interval={0}
              height={92}
              tick={{ fontSize: 11, fill: '#33507a' }}
            />
            <YAxis tickFormatter={compactMoney} width={92} tick={{ fontSize: 11 }} />
            <Tooltip content={<WaterfallTooltip currency={currency} />} />
            <Legend />
            <ReferenceLine y={0} stroke="#94a3b8" />
            <Bar dataKey="base" stackId="w" fill="rgba(0,0,0,0)" name="Opening position" isAnimationActive={false} />
            <Bar
              dataKey="delta"
              stackId="w"
              name="Adjustment"
              isAnimationActive={false}
              radius={[9, 9, 9, 9]}
            >
              {rows.map(function (row, index) {
                return <Cell key={index} fill={COLOR[row.colorKey]} />
              })}
              <LabelList dataKey="delta" content={renderValueLabel} />
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>

      <div className="basis-strip">
        {rows.map(function (row) {
          return (
            <span key={row.name} className={'basis-chip basis-' + row.basis}>
              <em>{row.shortName}</em>
              {row.basis}
            </span>
          )
        })}
      </div>

      <button type="button" className="linkish" onClick={function () { setShowTable(!showTable) }}>
        {showTable ? 'Hide' : 'Show'} the reconciliation table
      </button>

      {showTable && (
        <div className="table-scroll">
        <table className="data-table">
          <thead>
            <tr>
              <th>Step</th>
              <th className="num">Amount ({currency})</th>
              <th>Basis</th>
              <th>Method</th>
              <th>Justification</th>
            </tr>
          </thead>
          <tbody>
            <tr className="row-total">
              <td>BoQ tender total</td>
              <td className="num">{money(result.totals.boq_total)}</td>
              <td><span className="badge basis-measured">Measured</span></td>
              <td className="mono">sum(quantity x boq_rate)</td>
              <td>As tendered in the uploaded BoQ.</td>
            </tr>
            {result.waterfall.map(function (component) {
              return (
                <tr key={component.component}>
                  <td>{COMPONENT_LABEL[component.component]}</td>
                  <td className="num">{money(component.amount)}</td>
                  <td><span className={'badge basis-' + component.basis}>{BASIS_LABEL[component.basis]}</span></td>
                  <td className="mono small">{component.method}</td>
                  <td className="small">{component.justification}</td>
                </tr>
              )
            })}
            <tr className="row-total">
              <td>{ohpActive ? 'Full should-cost total' : 'Should-cost total'}</td>
              <td className="num">{money(fullTotal)}</td>
              <td>
                <span className={'badge basis-' + (ohpActive ? 'assumed' : 'derived')}>
                  {ohpActive ? 'Assumed' : 'Derived'}
                </span>
              </td>
              <td className="mono">
                {ohpActive
                  ? 'sum(quantity x full_adjusted_benchmark_rate)'
                  : 'sum(quantity x adjusted_benchmark_rate)'}
              </td>
              <td>
                {ohpActive
                  ? 'Benchmark-derived, including the assumed apportionment, CPI bridge and the analyst\'s overheads and margin above.'
                  : 'Benchmark-derived, including the assumed apportionment above.'}
              </td>
            </tr>
          </tbody>
        </table>
        </div>
      )}

      <p className="muted small">
        Total variance {money(result.totals.total_variance_abs)} ({num(result.totals.total_variance_pct, 2)}%)
        {ohpActive
          ? ' against the benchmark rate comparison basis chosen (' +
            (result.totals.overheads_in_tender
              ? 'full-to-full, tendered rates taken to include OH&P'
              : 'benchmark rate before overheads') + ')'
          : ''}
        .{' '}
        {ohpActive
          ? 'Against the FULL should-cost of ' + money(fullTotal) + ' the variance is '
            + money(result.totals.full_variance_abs) + ' ('
            + num(result.totals.full_variance_pct, 2) + '%). '
          : ''}
        {result.totals.unbenchmarked_line_count > 0
          ? ' ' + result.totals.unbenchmarked_line_count + ' line(s) totalling ' + money(result.totals.unbenchmarked_boq_total) + ' could not be benchmarked and are held at the tendered rate.'
          : ''}
      </p>
    </section>
  )
}
