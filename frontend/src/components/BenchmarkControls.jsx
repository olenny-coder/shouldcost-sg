import React from 'react'

function quarterIndex(quarter) {
  const match = /^(\d{4})Q([1-4])$/.exec(String(quarter || ''))
  if (!match) return null
  return Number(match[1]) * 4 + Number(match[2])
}

function fmtNum(value, digits) {
  if (value === null || value === undefined || value === '') return 'n/a'
  return Number(value).toFixed(digits === undefined ? 3 : digits)
}

function fmtMonths(months) {
  if (!months || !months.length) return 'n/a'
  if (months.length === 1) return months[0]
  return months[0] + '-' + months[months.length - 1]
}

function bridgeReason(reason) {
  const map = {
    no_cpi_series_configured_for_country: 'no consumer price series is configured for this market',
    no_cpi_observations_loaded: 'no consumer price observations are loaded',
    no_cpi_observation_for_the_observation_quarter: 'the CPI has no observation for the index quarter',
    no_cpi_observation_after_the_index_observation: 'the CPI has no observation after the index quarter',
    no_cpi_series_covers_both_quarters: 'no loaded CPI series spans both quarters',
    cpi_value_at_the_observation_quarter_is_zero: 'the CPI value at the index quarter is zero',
    index_observation_covers_requested_quarter: 'the index already covers the tender quarter',
  }
  return map[reason] || 'no CPI bridge was available'
}

/** Tender quarter + TPI series selector. Changing either re-runs the benchmark live. */
export default function BenchmarkControls(props) {
  const disabled = !props.uploadId || props.loading
  const seriesRow = ((props.freshness && props.freshness.series) || [])
    .filter(function (row) { return row.series_name === props.tpiSeries })[0]

  /** Label a quarter with what the benchmark will do to the index for it. */
  function optionLabel(quarter) {
    if (!seriesRow) return quarter
    const selected = quarterIndex(quarter)
    const published = quarterIndex(seriesRow.latest_quarter)
    if (selected === null || published === null || selected <= published) return quarter
    const stale = selected - published
    if (props.bridgeMode === 'none') return quarter + ' \u00b7 index held, ' + stale + 'q stale'
    // Bridging needs a consumer price series with an observation after the index.
    const bridgeable = !props.freshness || props.freshness.cpi_series_available !== false
    return bridgeable
      ? quarter + ' \u00b7 index bridged with CPI'
      : quarter + ' \u00b7 index held, no CPI loaded'
  }

  return (
    <section className="panel controls">
      <h2>2. Benchmark parameters</h2>
      <div className="control-row">
        <label>
          Tender quarter
          <select
            value={props.tenderQuarter}
            onChange={function (e) { props.onChange({ tenderQuarter: e.target.value }) }}
            disabled={disabled}
            title="Quarters later than the last published index observation are priced by bridging, or held, according to the 'Stale index' setting."
          >
            {props.quarters.map(function (q) {
              return <option key={q} value={q}>{optionLabel(q)}</option>
            })}
          </select>
        </label>

        <label>
          Index series
          <select
            value={props.tpiSeries}
            onChange={function (e) { props.onChange({ tpiSeries: e.target.value }) }}
            disabled={disabled}
          >
            {props.series.map(function (s) { return <option key={s} value={s}>{s}</option> })}
          </select>
        </label>

        {props.regions && props.regions.length > 1 ? (
          <label>
            Region
            <select
              value={props.regionCode || ''}
              onChange={function (e) { props.onChange({ regionCode: e.target.value }) }}
              disabled={disabled}
            >
              {props.regions.map(function (r) {
                return (
                  <option key={r.region_code} value={r.region_code}>
                    {r.region_name} (x{r.factor.toFixed(3)})
                  </option>
                )
              })}
            </select>
          </label>
        ) : null}

        <label>
          Variance threshold (%)
          <input
            type="number"
            min="0"
            max="1000"
            step="0.5"
            value={props.threshold}
            onChange={function (e) { props.onChange({ threshold: e.target.value }) }}
            disabled={disabled}
          />
        </label>

        <label>
          Stale index
          <select
            value={props.bridgeMode || 'cpi'}
            onChange={function (e) { props.onChange({ bridgeMode: e.target.value }) }}
            disabled={disabled}
            title="How an index that has not published the tender quarter yet is brought up to date."
          >
            <option value="cpi">Bridge with CPI (to the tender quarter)</option>
            <option value="none">Hold the last published observation</option>
          </select>
        </label>

        <button
          type="button"
          onClick={props.onRun}
          disabled={disabled}
          className="primary"
        >
          {props.loading ? 'Running...' : 'Re-run benchmark'}
        </button>
      </div>

      {props.indexBridge ? (
        <p className={'small ' + (props.indexBridge.applied ? 'bridge-line' : 'muted')}
           style={{ marginTop: 8 }}>
          <strong>Index freshness:</strong>{' '}
          {props.indexBridge.applied ? (
            <>
              the {props.indexBridge.index_series} series last published{' '}
              <strong>{props.indexBridge.observation_quarter}</strong> - {props.indexBridge.lag_quarters}{' '}
              quarter(s) before {props.indexBridge.requested_quarter}. The index was carried forward
              to <strong>{props.indexBridge.bridged_through_month}</strong> using the observed change
              in <strong>{props.indexBridge.cpi_series_name}</strong> (
              {fmtMonths(props.indexBridge.cpi_from_months)} {fmtNum(props.indexBridge.cpi_from_value)}
              {' \u2192 '}
              {fmtMonths(props.indexBridge.cpi_to_months)} {fmtNum(props.indexBridge.cpi_to_value)}),
              a factor of <strong>{fmtNum(props.indexBridge.cpi_bridge_factor, 4)}</strong>: index{' '}
              {fmtNum(props.indexBridge.index_value_published, 2)} becomes{' '}
              {fmtNum(props.indexBridge.index_value_used, 2)}.{' '}
              <span className="badge basis-assumed">modelled - basis assumed</span>{' '}
              {props.indexBridge.shortfall_months > 0
                ? 'The CPI is published only to ' + props.indexBridge.bridged_through_month + ', so the index is current to that month, not the quarter end. '
                : ''}
              This is not a published construction cost observation - see the assumptions panel.
            </>
          ) : (
            <>
              the {props.indexBridge.index_series} series last published{' '}
              <strong>{props.indexBridge.observation_quarter}</strong>, {props.indexBridge.lag_quarters}{' '}
              quarter(s) before {props.indexBridge.requested_quarter}, and no bridge was applied (
              {props.indexBridge.mode === 'none'
                ? 'bridging is switched off'
                : bridgeReason(props.indexBridge.reason)}). Those lines are held at the last
              published index level.
            </>
          )}
        </p>
      ) : null}

      {props.freshness ? (
        <p className="muted small">
          <strong>Data as at:</strong> CPI ({props.freshness.cpi_series_name}) published to{' '}
          <strong>{props.freshness.cpi_latest_month || 'n/a'}</strong>
          {props.freshness.cpi_latest_value !== null && props.freshness.cpi_latest_value !== undefined
            ? ' = ' + fmtNum(props.freshness.cpi_latest_value, 3)
            : ''}
          {props.freshness.cpi_base_year ? ' (base ' + props.freshness.cpi_base_year + ' = 100)' : ''}.{' '}
          {(props.freshness.series || []).length} index series loaded; the stale ones are bridged to
          the quarter being priced. See the Index dashboard for the per-series view.
        </p>
      ) : null}

      {props.activeRegion ? (
        <p className={'small ' + (props.activeRegion.factor === 1 ? 'muted' : '')}
           style={{ marginTop: 8 }}>
          <strong>Regional adjustment:</strong> every benchmark rate is multiplied by{' '}
          <strong>{props.activeRegion.factor.toFixed(3)}</strong> for{' '}
          {props.activeRegion.region_name}. Source: {props.activeRegion.source}.
          {props.activeRegion.is_placeholder
            ? ' This multiplier is a PLACEHOLDER estimate — the published city index has not been licensed yet, so it is disclosed as an assumption on every affected line.'
            : ''}
        </p>
      ) : null}

      {props.seriesScope && (
        <p className="muted small">
          <strong>{props.tpiSeries}</strong> scope - includes:{' '}
          {props.seriesScope.scope_inclusions || 'n/a'}.
          <br />
          <strong>Excludes:</strong> {props.seriesScope.scope_exclusions || 'nothing declared'}.
        </p>
      )}
    </section>
  )
}
