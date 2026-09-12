import React from 'react'
import {
  bridgeFactor, bridgeFromMonths, bridgeFromValue, bridgeKind, bridgeKindLong,
  bridgeReason, bridgeSeries, bridgeToMonths, bridgeToValue,
} from '../bridge.js'

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
    // The bridge needs a price series with an observation after the index. It takes
    // a producer index where the market publishes one, otherwise the consumer one.
    const producer = !!(props.freshness && props.freshness.ppi_series_available)
    const consumer = !props.freshness || props.freshness.cpi_series_available !== false
    if (producer) return quarter + ' \u00b7 index carried forward with PPI'
    return consumer
      ? quarter + ' \u00b7 index carried forward with CPI'
      : quarter + ' \u00b7 index held, no price series loaded'
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
            value={props.bridgeMode || 'auto'}
            onChange={function (e) { props.onChange({ bridgeMode: e.target.value }) }}
            disabled={disabled}
            title="How an index that has not published the tender quarter yet is carried forward to it."
          >
            <option value="auto">Carry forward with PPI, CPI as fallback</option>
            <option value="ppi">Carry forward with a producer price index only</option>
            <option value="cpi">Carry forward with a consumer price index only</option>
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

      {props.libraryQuarter ? (
        <p className="small" style={{ marginTop: 8 }}>
          <strong>Rate basis:</strong>{' '}
          the rate library is stated at <strong>{props.libraryQuarter}</strong> in{' '}
          <strong>{props.libraryCurrency || (props.libraryCurrencies || []).join(', ') || 'n/a'}</strong>
          {props.librarySourceDate ? ' (' + props.librarySourceDate + ')' : ''}.{' '}
          {props.tenderQuarter === props.libraryQuarter ? (
            <>
              Benchmarking at that quarter prices the schedule rates as they are published: the
              index ratio from the library quarter is <strong>1.000</strong>, so they are not
              escalated again.{' '}
              <span className="badge basis-measured">library as published</span>
            </>
          ) : (
            <>
              Benchmarking at <strong>{props.tenderQuarter}</strong> instead carries every library
              rate from {props.libraryQuarter} to {props.tenderQuarter} by the index ratio between
              them, so each priced line takes one more modelled step.{' '}
              <span className="badge basis-assumed">derived - rate escalated from the library</span>
            </>
          )}{' '}
          {props.librarySectionsStated ? (
            <>
              {props.librarySectionsStated} of the library's{' '}
              {props.librarySectionsStated + (props.librarySectionsRetained || 0)} sections state
              that quarter;{' '}
              {props.librarySectionsRetained
                ? 'the other ' + props.librarySectionsRetained + ' are retained values from an older '
                  + 'base and are still escalated from it. '
                : 'every one of them does. '}
            </>
          ) : null}
          {props.librarySectionsDisagreeing
            ? props.librarySectionsDisagreeing + ' library row(s) state a different quarter; they '
              + 'are reported rather than averaged. '
            : ''}
        </p>
      ) : null}

      {props.indexBridge ? (
        <p className={'small ' + (props.indexBridge.applied ? 'bridge-line' : 'muted')}
           style={{ marginTop: 8 }}>
          <strong>Index freshness:</strong>{' '}
          {props.indexBridge.applied ? (
            <>
              the {props.indexBridge.index_series} series last published{' '}
              <strong>{props.indexBridge.observation_quarter}</strong> - {props.indexBridge.lag_quarters}{' '}
              quarter(s) before {props.indexBridge.requested_quarter}. It was carried forward to{' '}
              <strong>{props.indexBridge.bridged_through_month}</strong> along the published trend of{' '}
              <strong>{bridgeSeries(props.indexBridge)}</strong>{' '}
              ({bridgeKindLong(bridgeKind(props.indexBridge))},{' '}
              {fmtMonths(bridgeFromMonths(props.indexBridge))} {fmtNum(bridgeFromValue(props.indexBridge))}
              {' \u2192 '}
              {fmtMonths(bridgeToMonths(props.indexBridge))} {fmtNum(bridgeToValue(props.indexBridge))}),
              a factor of <strong>{fmtNum(bridgeFactor(props.indexBridge), 4)}</strong>: index{' '}
              {fmtNum(props.indexBridge.index_value_published, 2)} becomes{' '}
              {fmtNum(props.indexBridge.index_value_used, 2)}.{' '}
              <span className="badge basis-assumed">derived - basis assumed</span>{' '}
              {props.indexBridge.shortfall_months > 0
                ? bridgeSeries(props.indexBridge) + ' is published only to ' + props.indexBridge.bridged_through_month + ', so the index is derived to that month, not the quarter end. '
                : ''}
              This shows the trend to date and is not a published construction cost observation - see
              the assumptions panel.
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
          <strong>Data as at:</strong>{' '}
          {props.freshness.ppi_series_available
            ? 'PPI (' + props.freshness.ppi_series_name + ') published to '
              + (props.freshness.ppi_latest_month || 'n/a') + '. '
            : ''}
          CPI ({props.freshness.cpi_series_name}) published to{' '}
          <strong>{props.freshness.cpi_latest_month || 'n/a'}</strong>
          {props.freshness.cpi_latest_value !== null && props.freshness.cpi_latest_value !== undefined
            ? ' = ' + fmtNum(props.freshness.cpi_latest_value, 3)
            : ''}
          {props.freshness.cpi_base_year ? ' (base ' + props.freshness.cpi_base_year + ' = 100)' : ''}.{' '}
          {(props.freshness.series || []).length} index series loaded; the stale ones are carried
          forward along the published trend to the quarter being priced. See the Index dashboard for
          the per-series view.
        </p>
      ) : null}

      {props.activeRegion ? (
        <p className={'small ' + (props.activeRegion.factor === 1 ? 'muted' : '')}
           style={{ marginTop: 8 }}>
          <strong>Regional adjustment:</strong> every benchmark rate is multiplied by{' '}
          <strong>{props.activeRegion.factor.toFixed(3)}</strong> for{' '}
          {props.activeRegion.region_name}. Source: {props.activeRegion.source}.
          {props.activeRegion.is_placeholder
            ? ' It is a modelled locational adjustment rather than a measured city index, so it is disclosed as an assumption on every affected line.'
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
