import React from 'react'

/** Tender quarter + TPI series selector. Changing either re-runs the benchmark live. */
export default function BenchmarkControls(props) {
  const disabled = !props.uploadId || props.loading
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
          >
            {props.quarters.map(function (q) { return <option key={q} value={q}>{q}</option> })}
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

        <button
          type="button"
          onClick={props.onRun}
          disabled={disabled}
          className="primary"
        >
          {props.loading ? 'Running...' : 'Re-run benchmark'}
        </button>
      </div>

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
          <strong>{props.tpiSeries}</strong> scope - includes: {props.seriesScope.inclusions || 'n/a'}.
          <br />
          <strong>Excludes:</strong> {props.seriesScope.exclusions || 'nothing declared'}.
        </p>
      )}
    </section>
  )
}
