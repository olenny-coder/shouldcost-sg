import React, { useMemo, useState } from 'react'
import { money as moneyFmt, num, pct, BASIS_LABEL } from '../format.js'
import { bridgeKind, bridgeKindLong } from '../bridge.js'
import HelpPopout from './HelpPopout.jsx'

const COLUMNS = [
  { key: 'smm2_section', label: 'Section', type: 'text' },
  { key: 'raw_description', label: 'Description', type: 'text' },
  { key: 'unit', label: 'Unit', type: 'text' },
  { key: 'quantity', label: 'Qty', type: 'num' },
  { key: 'boq_rate', label: 'BoQ rate', type: 'money' },
  { key: 'adjusted_benchmark_rate', label: 'Adj. benchmark', type: 'money' },
  { key: 'variance_pct', label: 'Variance %', type: 'pct' },
  { key: 'variance_amount', label: 'Variance', type: 'money' },
  { key: 'should_cost_amount', label: 'Should-cost', type: 'money' },
]

function flagSummary(line) {
  const labels = {
    over_threshold: 'over threshold',
    under_threshold: 'under threshold',
    within_threshold: 'within threshold',
    scope_excluded: 'scope excluded',
    unit_mismatch: 'unit mismatch',
    unclassified: 'unclassified',
    no_benchmark_rate: 'no benchmark rate',
    retained_library_rate: 'retained library rate',
    tpi_quarter_fallback: 'TPI quarter fallback',
    index_bridged: 'index carried forward',
    cpi_bridged: 'index carried forward',
    overhead_applied: 'overheads added',
    margin_applied: 'margin added',
    reclassified_manually: 'reclassified',
    user_adjusted: 'index adjusted',
  }
  return (line.flags || []).map(function (flag) { return labels[flag] || flag })
}

export default function VarianceTable(props) {
  const result = props.result
  const [sortKey, setSortKey] = useState('smm2_section')
  const [sortDir, setSortDir] = useState('asc')
  const [expanded, setExpanded] = useState(null)
  const [collapsed, setCollapsed] = useState({})

  const lines = result.lines
  const currency = props.currency || result.currency || 'SGD'
  // Currency-bound alias, so every money(...) call site below stays unchanged.
  const money = function (value) { return moneyFmt(value, currency) }
  // Overheads and margin, when the analyst has supplied them, add a full-cost line
  // to every subtotal and to the footer.
  const ohpActive = !!(result.totals.overhead_pct || result.totals.margin_pct)

  const sorted = useMemo(function () {
    const copy = lines.slice()
    copy.sort(function (a, b) {
      const av = a[sortKey]
      const bv = b[sortKey]
      if (av === null || av === undefined) return 1
      if (bv === null || bv === undefined) return -1
      if (typeof av === 'number' && typeof bv === 'number') return av - bv
      return String(av).localeCompare(String(bv))
    })
    if (sortDir === 'desc') copy.reverse()
    return copy
  }, [lines, sortKey, sortDir])

  const sectionByName = useMemo(function () {
    const map = {}
    result.sections.forEach(function (s) { map[s.smm2_section] = s })
    return map
  }, [result.sections])

  const grouped = sortKey === 'smm2_section'
  const groups = useMemo(function () {
    if (!grouped) return null
    const order = []
    const bucket = {}
    sorted.forEach(function (line) {
      if (!bucket[line.smm2_section]) { bucket[line.smm2_section] = []; order.push(line.smm2_section) }
      bucket[line.smm2_section].push(line)
    })
    return order.map(function (name) { return { name: name, rows: bucket[name] } })
  }, [sorted, grouped])

  function toggleGroup(name) {
    setCollapsed(function (current) {
      const next = Object.assign({}, current)
      if (next[name]) delete next[name]
      else next[name] = true
      return next
    })
  }

  function setAllGroups(value) {
    if (!value) { setCollapsed({}); return }
    const next = {}
    ;(groups || []).forEach(function (group) { next[group.name] = true })
    setCollapsed(next)
  }

  function toggleSort(key) {
    if (key === sortKey) {
      setSortDir(sortDir === 'asc' ? 'desc' : 'asc')
    } else {
      setSortKey(key)
      setSortDir('asc')
    }
  }

  function renderCell(line, column) {
    const value = line[column.key]
    if (column.type === 'money') return money(value)
    if (column.type === 'pct') return pct(value)
    if (column.type === 'num') return num(value)
    return value
  }

  function renderRow(line) {
    const flags = line.flags || []
    const over = flags.indexOf('over_threshold') !== -1
    const under = flags.indexOf('under_threshold') !== -1
    const excluded = !line.is_benchmarked
    let rowClass = 'row-ok'
    if (excluded) rowClass = 'row-not-benchmarked'
    else if (over) rowClass = 'row-over'
    else if (under) rowClass = 'row-under'
    const isOpen = expanded === line.item_id

    return (
      <React.Fragment key={line.item_id}>
        <tr className={rowClass}>
          <td>
            {excluded ? (
              <select
                className="reclassify"
                value={line.smm2_section}
                onChange={function (e) { props.onReclassify(line.item_id, e.target.value) }}
                title="Manual reclassification (PATCH /api/boq/item/{item_id})"
              >
                <option value={line.smm2_section}>{line.smm2_section}</option>
                {props.sectionOptions
                  .filter(function (s) { return s !== line.smm2_section })
                  .map(function (s) { return <option key={s} value={s}>{s}</option> })}
              </select>
            ) : (
              <span className="section-pill">{line.smm2_section}</span>
            )}
          </td>
          <td className="desc">
            <button type="button" className="linkish" onClick={function () { setExpanded(isOpen ? null : line.item_id) }}>
              {line.raw_description}
            </button>
            <div className="flag-row">
              {excluded ? (
                <span className="flag flag-alert" title={line.exclusion_reason || ''}>
                  NOT BENCHMARKED - held at the tendered rate
                </span>
              ) : null}
              {line.sor_code ? (
                <span className="flag" title="Quoted from this market's schedule of rates">
                  schedule item {line.sor_code}
                </span>
              ) : null}
              {flagSummary(line).map(function (label) { return <span key={label} className="flag">{label}</span> })}
            </div>
          </td>
          <td>{line.unit}</td>
          <td className="num">{num(line.quantity)}</td>
          <td className="num">{money(line.boq_rate)}</td>
          <td className="num">
            {line.adjusted_benchmark_rate === null
              ? <span className="muted">not benchmarked</span>
              : money(line.adjusted_benchmark_rate)}
          </td>
          <td className={over ? 'num danger' : under ? 'num good' : 'num'}>
            {line.variance_pct === null ? <span className="muted">n/a</span> : pct(line.variance_pct)}
          </td>
          <td className="num">{money(line.variance_amount)}</td>
          <td className="num">{money(line.should_cost_amount)}</td>
        </tr>
        {isOpen && (
          <tr className="detail-row">
            <td colSpan={COLUMNS.length}>
              <div className="detail-grid">
                <div>
                  <h4>Basis</h4>
                  <span className={'badge basis-' + line.basis}>{BASIS_LABEL[line.basis]}</span>
                  <p className="muted small">
                    {line.exclusion_reason
                      ? 'Excluded from variance testing: ' + line.exclusion_reason + '. Should-cost is held at the tendered rate, so this line contributes zero tested variance.'
                      : line.overhead_pct || line.margin_pct
                        ? 'Modelled: the benchmark rate is grossed up by the analyst\'s overheads and margin, which are commercial inputs rather than observations, so this line is assumed rather than derived. Formula: base_rate x (index used / base index) x scope_factor, then x (1 + overheads%) x (1 + margin%).'
                        : line.tpi_bridged
                          ? 'Modelled: the index for the tender quarter is not a published observation - it was bridged with the consumer price index, so this line is assumed rather than derived. Formula: base_rate x (index used / base index) x scope_factor.'
                          : 'Derived: base_rate x (current TPI / base TPI) x scope_factor.'}
                  </p>
                </div>
                <div>
                  <h4>Index applied</h4>
                  <p className="small">
                    {line.tpi_series_name} - requested {line.tpi_quarter_requested}, used{" "}
                    <strong>{line.tpi_quarter_used}</strong>
                    {line.tpi_fallback_used ? ' (nearest prior quarter fallback)' : ''}
                    <br />
                    value {num(line.tpi_value, 2)} / base {num(line.tpi_base_value, 2)} = ratio {num(line.tpi_ratio, 6)}
                    <br />
                    scope_factor {num(line.scope_factor, 6)}{line.scope_excluded ? ' (section excluded by this series)' : ''}
                  </p>
                  {line.tpi_bridged ? (
                    <p className="small bridge-line">
                      <strong>
                        Index carried forward with the {bridgeKindLong(line.index_bridge_kind)}.
                      </strong>{' '}
                      Published observation {num(line.tpi_value_published, 2)} at{' '}
                      {line.tpi_quarter_used} ({line.index_lag_quarters} quarter(s) stale) was
                      carried forward to {line.cpi_month_used} along the published trend of{' '}
                      {line.cpi_series_name}
                      {' '}({bridgeKind(line.index_bridge_kind)})
                      {line.cpi_value_used !== null && line.cpi_value_used !== undefined
                        ? ' = ' + num(line.cpi_value_used, 3) : ''}
                      , a factor of {num(line.cpi_bridge_factor, 4)}. The result is derived to show
                      the trend to date, not a published construction cost observation, so the line
                      is <strong>basis: assumed</strong>.
                      {line.cpi_source_url ? (
                        <span>
                          {' '}Source:{' '}
                          <a href={line.cpi_source_url} target="_blank" rel="noreferrer">
                            {line.cpi_source_url}
                          </a>
                        </span>
                      ) : null}
                    </p>
                  ) : null}
                  {line.overhead_pct || line.margin_pct ? (
                    <p className="small ohp-readout">
                      <strong>
                        Full cost: overheads {num(line.overhead_pct, 1)}% and margin{' '}
                        {num(line.margin_pct, 1)}%.
                      </strong>{' '}
                      Benchmark rate {money(line.adjusted_benchmark_rate)} becomes{' '}
                      <strong>{money(line.full_adjusted_benchmark_rate)}</strong> per{' '}
                      {line.unit} (margin compounded on overheads). On this line that adds{' '}
                      {money(line.overhead_amount)} of overheads and {money(line.margin_amount)} of
                      margin, for a full should-cost of{' '}
                      <strong>{money(line.full_should_cost_amount)}</strong>.
                      {line.compared_against_full
                        ? ' The variance above is measured full-to-full against that rate.'
                        : ' The variance above is measured against the benchmark rate before overheads.'}
                    </p>
                  ) : null}
                </div>
                {line.provenance && (
                  <div>
                    <h4>Benchmark provenance</h4>
                    <p className="small">
                      source: <strong>{line.provenance.source}</strong>
                      <br />source_date: {line.provenance.source_date} | base_year: {line.provenance.base_year} | confidence: {line.provenance.confidence}
                      <br />scope_inclusions: {line.provenance.scope_inclusions}
                      <br />scope_exclusions: {line.provenance.scope_exclusions}
                      {line.provenance.source_url ? <span><br />source_url: <a href={line.provenance.source_url} target="_blank" rel="noreferrer">{line.provenance.source_url}</a></span> : null}
                    </p>
                  </div>
                )}
              </div>
            </td>
          </tr>
        )}
      </React.Fragment>
    )
  }

  function groupHeaderRow(group, isCollapsed) {
    const untested = group.rows.filter(function (r) { return !r.is_benchmarked; }).length
    // How much of this section was quoted from the market's schedule of rates rather than
    // written by hand: the section-level view of the upload's sections summary.
    const fromSchedule = group.rows.filter(function (r) { return r.sor_code }).length
    return (
      <tr key={'group-' + group.name} className="group-row">
        <td colSpan={COLUMNS.length}>
          <button
            type="button"
            className="group-toggle"
            onClick={function () { toggleGroup(group.name) }}
            aria-expanded={!isCollapsed}
          >
            <span className="chev">{isCollapsed ? '\u25B6' : '\u25BC'}</span>
            <span className="section-pill">{group.name}</span>
            <span className="muted small">
              {group.rows.length} line(s)
              {fromSchedule
                ? ' \u00b7 ' + fromSchedule + ' from the schedule of rates'
                : ''}
              {untested ? ' \u00b7 ' + untested + ' not benchmarked' : ''}
            </span>
          </button>
        </td>
      </tr>
    )
  }

  function subtotalRow(name) {
    const section = sectionByName[name]
    if (!section) return null
    return (
      <tr key={'subtotal-' + name} className="subtotal-row">
        <td colSpan={5}>Subtotal - {name} ({section.item_count} lines, {section.benchmarked_item_count} benchmarked)</td>
        <td className="num">{(section.basis || '').toUpperCase()}</td>
        <td className={section.breaches_threshold ? 'num danger' : 'num'}>{pct(section.variance_pct)}</td>
        <td className="num">{money(section.variance_amount)}</td>
        <td className="num">
          {ohpActive && section.full_should_cost_amount
            ? <span>
                {money(section.should_cost_amount)}
                <div className="small muted">full {money(section.full_should_cost_amount)}</div>
              </span>
            : money(section.should_cost_amount)}
        </td>
      </tr>
    )
  }

  return (
    <section className="panel">
      <h2>
        Variance table
        <HelpPopout
          title="Working the variance table"
          items={[
            'Rows are grouped by section. Click a section heading to collapse or expand it; use the buttons on the right to collapse or expand everything at once.',
            'Click a column heading to sort by it. Clicking the same heading again reverses the order. Sorting by anything other than Section switches to a flat list.',
            'Red rows are above the breach threshold, blue rows are below it. Grey rows marked NOT BENCHMARKED were not priced at all - see the coverage panel above the table.',
            'Click any description to expand that line\'s full working: the rate used, the index applied, the scope factor, and the source and date of the benchmark rate.',
            'A grey section dropdown means the classifier could not place the line. Pick the right section and the benchmark re-runs immediately.',
            'Each section ends with a subtotal row showing the section\'s variance and its basis.',
            'If you have set overheads and margin on the adjusters panel, every line also carries a full cost: benchmark rate x (1 + overheads%) x (1 + margin%), and the table footer adds a full should-cost row. Those percentages are assumptions, so the lines they touch are grey-amber, not derived.',
            'The footer reconciles the whole BoQ. If a line is not benchmarked it carries zero tested variance - that is why the coverage panel matters.',
          ]}
          footnote="Measured means taken from the BoQ or a publication; derived means calculated from those; assumed means apportioned or analyst-supplied."
        />
        {grouped ? (
          <span className="h2-actions">
            <button type="button" className="secondary small-btn" onClick={function () { setAllGroups(false) }}>
              Expand all
            </button>
            <button type="button" className="secondary small-btn" onClick={function () { setAllGroups(true) }}>
              Collapse all
            </button>
          </span>
        ) : null}
      </h2>
      <p className="muted">
        Threshold {"+"}/-{num(result.variance_threshold, 2)}%. {result.totals.breached_line_count} of{" "}
        {result.totals.line_count} lines breach it. Click a description to see the full provenance of
        the benchmark rate behind it.
      </p>

      <div className="table-scroll">
        <table className="data-table">
          <thead>
            <tr>
              {COLUMNS.map(function (column) {
                return (
                  <th
                    key={column.key}
                    className={column.type === 'num' || column.type === 'money' || column.type === 'pct' ? 'num sortable' : 'sortable'}
                    onClick={function () { toggleSort(column.key) }}
                  >
                    {column.label}{sortKey === column.key ? (sortDir === 'asc' ? ' ^' : ' v') : ''}
                  </th>
                )
              })}
            </tr>
          </thead>
          <tbody>
            {grouped
              ? groups.map(function (group) {
                  const isCollapsed = !!collapsed[group.name]
                  return (
                    <React.Fragment key={group.name}>
                      {groupHeaderRow(group, isCollapsed)}
                      {isCollapsed ? null : group.rows.map(renderRow)}
                      {subtotalRow(group.name)}
                    </React.Fragment>
                  )
                })
              : sorted.map(renderRow)}
          </tbody>
          <tfoot>
            <tr className="total-row">
              <td colSpan={5}>Total - {result.totals.line_count} lines ({result.totals.unbenchmarked_line_count} not benchmarked)</td>
              <td className="num">BoQ {money(result.totals.boq_total)}</td>
              <td className="num">{pct(result.totals.total_variance_pct)}</td>
              <td className="num">{money(result.totals.total_variance_abs)}</td>
              <td className="num">{money(result.totals.should_cost_total)}</td>
            </tr>
            {ohpActive ? (
              <tr className="total-row">
                <td colSpan={5}>
                  Full should-cost - benchmark {money(result.totals.should_cost_total)}
                  {' + '}{money(result.totals.overhead_amount_total)} overheads (
                  {num(result.totals.overhead_pct, 1)}%)
                  {' + '}{money(result.totals.margin_amount_total)} margin (
                  {num(result.totals.margin_pct, 1)}%)
                </td>
                <td className="num">
                  <span className="badge basis-assumed">OH&amp;P</span>
                </td>
                <td className="num">{pct(result.totals.full_variance_pct)}</td>
                <td className="num">{money(result.totals.full_variance_abs)}</td>
                <td className="num">{money(result.totals.full_should_cost_total)}</td>
              </tr>
            ) : null}
          </tfoot>
        </table>
      </div>
    </section>
  )
}
