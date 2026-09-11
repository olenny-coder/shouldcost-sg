import React, { useMemo, useState } from 'react'
import { money as moneyFmt, num, pct, BASIS_LABEL } from '../format.js'
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
    placeholder_benchmark_rate: 'placeholder rate',
    tpi_quarter_fallback: 'TPI quarter fallback',
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
                </div>
                {line.provenance && (
                  <div>
                    <h4>Benchmark provenance</h4>
                    <p className="small">
                      source: <strong>{line.provenance.source}</strong>
                      <br />source_date: {line.provenance.source_date} | base_year: {line.provenance.base_year} | confidence: {line.provenance.confidence}
                      <br />scope_inclusions: {line.provenance.scope_inclusions}
                      <br />scope_exclusions: {line.provenance.scope_exclusions}
                      <br />is_placeholder: <strong>{String(line.provenance.is_placeholder)}</strong>
                      {line.provenance.source_url ? <span><br />source_url: <a href={line.provenance.source_url} target="_blank" rel="noreferrer">{line.provenance.source_url}</a></span> : null}
                    </p>
                    {line.provenance.replace_with ? (
                      <p className="todo">{line.provenance.replace_with}</p>
                    ) : null}
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
        <td className="num">{money(section.should_cost_amount)}</td>
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
          </tfoot>
        </table>
      </div>
    </section>
  )
}
