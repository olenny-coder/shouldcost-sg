import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, API_BASE_URL, USING_LOCAL_FALLBACK } from './api.js'
import { money, num, pct } from './format.js'
import UploadBoQ from './components/UploadBoQ.jsx'
import BenchmarkControls from './components/BenchmarkControls.jsx'
import IndexAdjusters from './components/IndexAdjusters.jsx'
import VarianceTable from './components/VarianceTable.jsx'
import WaterfallChart from './components/WaterfallChart.jsx'
import SensitivityView from './components/SensitivityView.jsx'
import IndexDashboard from './components/IndexDashboard.jsx'
import AssumptionsPanel from './components/AssumptionsPanel.jsx'
import ClassificationRules from './components/ClassificationRules.jsx'
import ExportBar from './components/ExportBar.jsx'
import CoveragePanel from './components/CoveragePanel.jsx'
import Logo from './components/Logo.jsx'

const SECTION_OPTIONS = [
  'Concrete', 'Reinforcement', 'Formwork', 'Masonry', 'Plaster',
  'Excavation', 'Piling', 'Waterproofing', 'M&E Containment', 'Preliminaries', 'Unclassified',
]

const TABS = [
  { key: 'upload', label: 'Upload' },
  { key: 'variance', label: 'Variance table' },
  { key: 'waterfall', label: 'Waterfall' },
  { key: 'sensitivity', label: 'Sensitivity' },
  { key: 'indices', label: 'Index dashboard' },
  { key: 'download', label: 'Download' },
]

const FLAGS = { SG: '\uD83C\uDDF8\uD83C\uDDEC', IN: '\uD83C\uDDEE\uD83C\uDDF3' }

const NO_ADJUSTMENTS = {
  tpi_scale_pct: 0,
  base_rate_scale_pct: 0,
  tpi_value_override: null,
  section_rate_scale_pct: {},
  // Overheads and margin: the analyst inputs that turn the benchmark cost into a
  // full commercial should-cost. Zero means the two are identical.
  overhead_pct: 0,
  margin_pct: 0,
  overheads_in_tender: true,
}

function quarterIndex(quarter) {
  const match = /^(\d{4})Q([1-4])$/.exec(String(quarter || ''))
  if (!match) return null
  return Number(match[1]) * 4 + Number(match[2])
}

function quarterLabel(year, q) {
  return year + 'Q' + q
}

function currentCalendarQuarter() {
  const now = new Date()
  return quarterLabel(now.getFullYear(), Math.floor(now.getMonth() / 3) + 1)
}

function quarterRange(first, last) {
  const start = quarterIndex(first)
  const end = quarterIndex(last)
  if (start === null || end === null || end < start) return []
  const out = []
  for (let key = start; key <= end; key += 1) {
    out.push(quarterLabel(Math.floor((key - 1) / 4), ((key - 1) % 4) + 1))
  }
  return out
}

export default function App() {
  const [health, setHealth] = useState(null)
  const [serverConfig, setServerConfig] = useState(null)
  const [bootError, setBootError] = useState(null)
  const [countries, setCountries] = useState([])
  const [countryCode, setCountryCode] = useState('SG')

  const [tpiRows, setTpiRows] = useState([])
  // Every monthly price observation for the market, producer and consumer alike.
  // The dashboard splits it by kind; the engine prefers the producer series.
  const [priceRows, setPriceRows] = useState([])
  const [freshness, setFreshness] = useState(null)
  const [indexCoverage, setIndexCoverage] = useState(null)
  // True when the API is older than this UI and has no /api/indices/coverage route.
  const [coverageUnavailable, setCoverageUnavailable] = useState(false)
  // False while a market's reference data is in flight, so nothing is run against a
  // half-switched state.
  const [countryReady, setCountryReady] = useState(false)
  const [materialRows, setMaterialRows] = useState([])
  const [benchmarkRates, setBenchmarkRates] = useState([])
  const [classifierRules, setClassifierRules] = useState(null)
  const [uploads, setUploads] = useState([])
  const [regions, setRegions] = useState([])
  const [regionCode, setRegionCode] = useState('')

  const [upload, setUpload] = useState(null)
  const [result, setResult] = useState(null)
  const [sensitivity, setSensitivity] = useState(null)

  const [tab, setTab] = useState('upload')
  // The quarter the rate library is expressed at, so a fresh run opens on the library
  // as built. Every other quarter stays selectable and carries the index from here.
  const [tenderQuarter, setTenderQuarter] = useState('2026Q2')
  const [tpiSeries, setTpiSeries] = useState('BCA')
  const [threshold, setThreshold] = useState('15')
  // How a stale index observation is brought up to the tender quarter.
  // 'auto' (default) bridges it with a PRODUCER price index where the market
  // publishes one, falling back to the consumer index; 'ppi' and 'cpi' force one
  // kind or the other; 'none' holds the last published observation.
  const [bridgeMode, setBridgeMode] = useState('auto')
  const [adjustments, setAdjustments] = useState(NO_ADJUSTMENTS)
  // Analyst-supplied rates for lines the library cannot price, keyed by item id.
  const [manualRates, setManualRates] = useState({})

  const [loading, setLoading] = useState(false)
  const [loadingSensitivity, setLoadingSensitivity] = useState(false)
  const [downloading, setDownloading] = useState(false)
  const [downloadStatus, setDownloadStatus] = useState(null)
  const [runError, setRunError] = useState(null)

  const uploadId = upload ? upload.upload_id : null
  const country = useMemo(function () {
    return countries.filter(function (c) { return c.code === countryCode })[0] || null
  }, [countries, countryCode])
  const currency = country ? country.currency : 'SGD'

  // ------------------------------------------------------------------ boot --
  useEffect(function () {
    let cancelled = false
    async function boot() {
      try {
        const responses = await Promise.all([api.health(), api.config(), api.listCountries()])
        if (cancelled) return
        setHealth(responses[0])
        setServerConfig(responses[1])
        setCountries(responses[2])
        if (responses[2].length) setCountryCode(responses[2][0].code)
      } catch (error) {
        if (!cancelled) setBootError(error.message)
      }
    }
    boot()
    return function () { cancelled = true }
  }, [])

  // --------------------------------------------------- country-scoped data --
  useEffect(function () {
    let cancelled = false
    async function loadCountry() {
      setRunError(null)
      setUpload(null)
      setResult(null)
      setSensitivity(null)
      setAdjustments(NO_ADJUSTMENTS)
      setManualRates({})
      // Everything below is scoped to ONE market, and the previous market's values survive
      // until this resolves. Firing a benchmark in that window sends Singapore's index to
      // India and the API answers 400 - so the auto-run is held until the load settles.
      setCountryReady(false)
      try {
        // The coverage matrix is an OPTIONAL panel. It was added after the rest of
        // the reference data, so a backend that predates it answers 404 - and a
        // frontend deployed ahead of its API must degrade to "coverage unavailable"
        // rather than failing the whole country load. Everything else is required.
        setCoverageUnavailable(false)
        const coverageCall = api.indexCoverage({ country: countryCode })
          .catch(function () {
            setCoverageUnavailable(true)
            return null
          })
        const responses = await Promise.all([
          api.listTpi({ country: countryCode }),
          api.listMaterials({ country: countryCode }),
          api.listBenchmarkRates({ country: countryCode }),
          api.listClassifierRules({ country: countryCode }),
          api.listUploads(countryCode),
          api.listRegions({ country: countryCode }),
          api.listCpi({ country: countryCode }),
          coverageCall,
        ])
        if (cancelled) return
        const tpi = responses[0]
        setTpiRows(tpi)
        setPriceRows(responses[6])
        setIndexCoverage(responses[7])
        setMaterialRows(responses[1])
        setBenchmarkRates(responses[2])
        setClassifierRules(responses[3])
        setUploads(responses[4])
        setRegions(responses[5])
        const defaultRegion = responses[5].filter(function (r) { return r.is_default })[0]
        setRegionCode(defaultRegion ? defaultRegion.region_code : (responses[5][0] || {}).region_code || '')

        const registry = countries.filter(function (c) { return c.code === countryCode })[0]
        // The rate library was derived from ONE published schedule of rates per market, so
        // a benchmark may only be run against the index that belongs with it. The other
        // series stay loaded (the dashboard charts them) but are not selectable here.
        const selectable = (registry && registry.selectable_tpi_series) || []
        const preferred = selectable.length
          ? selectable[0]
          : (registry && registry.default_tpi_series) || 'BCA'
        const quarters = Array.from(new Set(tpi.map(function (r) { return r.quarter }))).sort()
        let quarter = quarters.length ? quarters[quarters.length - 1] : '2026Q2'
        let series = preferred

        const sample = responses[4].filter(function (u) { return u.is_seeded_sample })[0]
          || responses[4][0]
        if (sample) {
          const detail = await api.getUpload(sample.upload_id)
          if (cancelled) return
          // Honour the parameters the upload was last benchmarked with, so a
          // demonstration bill opens on the quarter it was calibrated for.
          if (detail.tender_quarter && quarters.indexOf(detail.tender_quarter) !== -1) {
            quarter = detail.tender_quarter
          }
          // Only honour the uploaded run's series if it is one this market may be
          // benchmarked against. A demonstration bill saved before the market's index was
          // pinned to a single series - or an upload moved between markets - would otherwise
          // open on BCA against India and fail with a 400 on the first render.
          if (detail.tpi_series_name && selectable.indexOf(detail.tpi_series_name) !== -1) {
            series = detail.tpi_series_name
          }
          applyUploadDetail(detail)
        }
        setTpiSeries(series)
        setTenderQuarter(quarter)
        setTab('variance')
        setCountryReady(true)
      } catch (error) {
        if (!cancelled) {
          setRunError(error.message)
          // Leave it un-ready: a market whose reference data failed to load must not be
          // silently benchmarked with the previous market's parameters.
        }
      }
    }
    if (countryCode) loadCountry()
    return function () { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [countryCode, countries.length])

  // ------------------------------------------------------- index freshness --
  // How current every index series is against the quarter being priced, and what
  // the CPI bridge would do about it. Displayed in the controls, the index
  // dashboard and the assumptions panel, and used to explain the freshness tile.
  useEffect(function () {
    let cancelled = false
    async function loadFreshness() {
      try {
        const data = await api.indexFreshness({
          country: countryCode,
          reference_quarter: tenderQuarter,
        })
        if (!cancelled) setFreshness(data)
      } catch (error) {
        if (!cancelled) setFreshness(null)
      }
    }
    if (countryCode) loadFreshness()
    return function () { cancelled = true }
  }, [countryCode, tenderQuarter])

  function applyUploadDetail(detail) {
    const items = detail.items || []
    setUpload({
      upload_id: detail.upload_id,
      country: detail.country,
      filename: detail.filename,
      uploaded_at: detail.uploaded_at,
      currency: detail.currency,
      row_count: items.length,
      classified_count: items.filter(function (i) { return i.smm2_section !== 'Unclassified' }).length,
      unclassified_count: items.filter(function (i) { return i.smm2_section === 'Unclassified' }).length,
      items: items,
      counts_by_section: detail.counts_by_section,
      warnings: [],
      assumptions: [],
    })
  }

  // -------------------------------------------------- benchmark (debounced) --
  const adjustmentKey = JSON.stringify(adjustments)

  const manualKey = JSON.stringify(manualRates)

  // The series actually sent to the API. A market pins its own index (BCA for Singapore,
  // CPWD for India), so anything else in state - a stale value from the previous market, or
  // an upload saved before that pin existed - resolves to the market's own series rather
  // than being posted and rejected. Belt and braces with the countryReady gate above.
  const effectiveTpiSeries = useMemo(function () {
    const selectable = (country && country.selectable_tpi_series) || []
    if (!selectable.length) return tpiSeries
    return selectable.indexOf(tpiSeries) !== -1 ? tpiSeries : selectable[0]
  }, [country, tpiSeries])

  const benchmarkBody = useCallback(function () {
    // Only send rates that are actually filled in; a blank input means "not set".
    const supplied = {}
    Object.keys(manualRates).forEach(function (key) {
      const entry = manualRates[key] || {}
      if (Number(entry.base_rate) > 0) {
        supplied[key] = {
          base_rate: Number(entry.base_rate),
          indexed: entry.indexed !== false,
          note: entry.note || 'Analyst-supplied rate',
        }
      }
    })
    return {
      tender_quarter: tenderQuarter,
      tpi_series_name: effectiveTpiSeries,
      variance_threshold: Number(threshold) || 0,
      region_code: regionCode || null,
      index_bridge: bridgeMode,
      adjustments: adjustments,
      manual_rates: supplied,
    }
  }, [tenderQuarter, effectiveTpiSeries, threshold, regionCode, bridgeMode, adjustmentKey, manualKey])

  const runBenchmark = useCallback(async function () {
    if (!uploadId) return
    setLoading(true)
    setRunError(null)
    try {
      const payload = Object.assign({}, benchmarkBody())
      setResult(await api.benchmark(uploadId, payload))
    } catch (error) {
      setResult(null)
      setRunError(error.message)
    } finally {
      setLoading(false)
    }
  }, [uploadId, benchmarkBody])

  useEffect(function () {
    if (!uploadId || !countryReady) return undefined
    const handle = setTimeout(function () { runBenchmark() }, 300)
    return function () { clearTimeout(handle) }
  }, [uploadId, countryReady, benchmarkBody, runBenchmark])

  const benchmarkPayload = useMemo(function () {
    return {
      tender_quarter: tenderQuarter,
      tpi_series_name: effectiveTpiSeries,
      variance_threshold: Number(threshold) || 0,
      region_code: regionCode || null,
      index_bridge: bridgeMode,
      adjustments: adjustments,
    }
  }, [tenderQuarter, effectiveTpiSeries, threshold, regionCode, bridgeMode, adjustmentKey])

  // ------------------------------------------------------------ sensitivity --
  async function runSensitivity(overrides) {
    if (!uploadId) return
    setLoadingSensitivity(true)
    setRunError(null)
    try {
      setSensitivity(await api.sensitivity(uploadId, Object.assign({}, benchmarkPayload, overrides)))
    } catch (error) {
      setSensitivity(null)
      setRunError(error.message)
    } finally {
      setLoadingSensitivity(false)
    }
  }

  // ------------------------------------------------------------- downloads --
  async function handleTemplate(format) {
    setDownloading(true)
    setDownloadStatus(null)
    try {
      const name = await api.downloadTemplate(countryCode, format)
      setDownloadStatus('Downloaded ' + name)
    } catch (error) {
      setDownloadStatus('Download failed: ' + error.message)
    } finally {
      setDownloading(false)
    }
  }

  async function handleExport(level, format) {
    if (!uploadId) return
    setDownloading(true)
    setDownloadStatus(null)
    try {
      const name = await api.exportBenchmark(uploadId, benchmarkPayload, level, format)
      setDownloadStatus('Downloaded ' + name + ' (includes any active index adjusters)')
    } catch (error) {
      setDownloadStatus('Export failed: ' + error.message)
    } finally {
      setDownloading(false)
    }
  }

  // ------------------------------------------------------------------ edit --
  async function handleUpload(file) {
    const response = await api.uploadBoQ(file, countryCode)
    setUpload(response)
    setSensitivity(null)
    // A freshly uploaded BoQ is priced to date by default: the current calendar
    // quarter, bridged with the CPI where the index has not published it yet.
    // The seeded demonstration bills keep the quarter they were calibrated for.
    setTenderQuarter(currentCalendarQuarter())
    setTab('variance')
    setUploads(await api.listUploads(countryCode))
    return response
  }

  async function handleLoadUpload(id) {
    setRunError(null)
    setSensitivity(null)
    try {
      applyUploadDetail(await api.getUpload(id))
      setTab('variance')
    } catch (error) {
      setRunError(error.message)
    }
  }

  async function handleReclassify(itemId, section) {
    if (!uploadId) return
    try {
      await api.patchItem(itemId, section)
      await runBenchmark()
    } catch (error) {
      setRunError(error.message)
    }
  }

  function updateAdjustments(patch) {
    setAdjustments(function (current) { return Object.assign({}, current, patch) })
  }

  function setManualRate(itemId, entry) {
    setManualRates(function (current) {
      const next = Object.assign({}, current)
      next[itemId] = entry
      return next
    })
  }

  function clearManualRate(itemId) {
    setManualRates(function (current) {
      const next = Object.assign({}, current)
      delete next[itemId]
      return next
    })
  }

  // ----------------------------------------------------------------- render --
  const cpiRows = useMemo(function () {
    return priceRows.filter(function (row) { return row.kind !== 'PPI' })
  }, [priceRows])
  const ppiRows = useMemo(function () {
    return priceRows.filter(function (row) { return row.kind === 'PPI' })
  }, [priceRows])
  // Lines the engine could only price with an indicative seed rate rather than a
  // published schedule of rates. Reported, never hidden.
  const indicativeLineCount = result
    ? result.lines.filter(function (l) { return l.provenance && l.provenance.is_placeholder }).length
    : 0
  const adjustmentsActive = result && result.adjustments_applied && !result.adjustments_applied.is_noop
  const ohpActive = !!(
    result && (result.totals.overhead_pct || result.totals.margin_pct)
  )
  const seriesScopes = useMemo(function () {
    const seen = {}
    const out = []
    tpiRows.forEach(function (row) {
      if (seen[row.series_name]) return
      seen[row.series_name] = true
      out.push({
        series_name: row.series_name,
        scope_inclusions: row.scope_inclusions,
        scope_exclusions: row.scope_exclusions,
      })
    })
    return out.sort(function (a, b) { return a.series_name.localeCompare(b.series_name) })
  }, [tpiRows])

  const quarters = useMemo(function () {
    const set = []
    tpiRows.forEach(function (row) { if (set.indexOf(row.quarter) === -1) set.push(row.quarter) })
    if (!set.length) return ['2026Q2']
    // A tender priced today may fall in a quarter the index has not published
    // yet. Those quarters ARE selectable: the index is carried forward along the
    // published trend of a price index (or held, when bridging is switched off),
    // and the selector says which.
    const sorted = set.slice().sort()
    const lastPublished = sorted[sorted.length - 1]
    const priceMonths = priceRows.map(function (row) { return row.month }).sort()
    const cpiMonths = priceMonths
    const latestCpiQuarter = priceMonths.length
      ? quarterLabel(
          Number(priceMonths[priceMonths.length - 1].slice(0, 4)),
          Math.floor((Number(priceMonths[priceMonths.length - 1].slice(5, 7)) - 1) / 3) + 1,
        )
      : lastPublished
    const reach = [currentCalendarQuarter(), latestCpiQuarter, lastPublished].sort().slice(-1)[0]
    quarterRange(lastPublished, reach).forEach(function (quarter) {
      if (set.indexOf(quarter) === -1) set.push(quarter)
    })
    return set.sort().reverse()
  }, [tpiRows, priceRows])

  // Only the series that belongs with the market's rate library is offered for pricing.
  // Every loaded series is still charted on the index dashboard.
  const seriesNames = useMemo(function () {
    const loaded = []
    tpiRows.forEach(function (row) { if (loaded.indexOf(row.series_name) === -1) loaded.push(row.series_name) })
    const selectable = (country && country.selectable_tpi_series) || []
    const allowed = selectable.filter(function (name) { return loaded.indexOf(name) !== -1 })
    if (allowed.length) return allowed
    return loaded.length ? loaded.sort() : ['BCA']
  }, [tpiRows, country])

  return (
    <div className="app">
      <header className="app-header">
        <div>
          <div className="app-title">
            <Logo size={38} />
            <h1>shouldcost <span>| BoQ benchmarking</span></h1>
          </div>
          <p>
            Should-cost benchmarking for the Singapore and India construction markets -
            classification, cost-index adjustment, variance and waterfall reporting, all offline.
          </p>
        </div>
        <div className="header-status">
          <span className="pill">
            <span className={health && health.status === 'ok' ? 'dot dot-ok' : 'dot dot-bad'} />
            {health ? 'API ' + health.status + ' | db ' + health.db : 'connecting...'}
            {serverConfig ? ' | ' + serverConfig.environment : ''}
          </span>
          <span className="pill">{country ? country.name + ' | ' + currency : 'loading...'}</span>
          <span className="pill" title={API_BASE_URL}>
            {USING_LOCAL_FALLBACK ? 'local-dev fallback' : 'deployed API'}
          </span>
        </div>
      </header>

      {bootError ? (
        <div className="notice notice-critical">
          <strong>Cannot reach the backend.</strong> {bootError}
          <div className="small">
            Start it with <code>make dev-backend</code> (http://localhost:8000), or set{' '}
            <code>VITE_API_BASE_URL</code> and rebuild. On production, confirm the Render{' '}
            <code>FRONTEND_URL</code> matches this origin - CORS is an explicit allowlist.
          </div>
        </div>
      ) : null}

      <div className="country-bar">
        <span className="label">Market</span>
        {countries.map(function (entry) {
          return (
            <button
              key={entry.code}
              type="button"
              className={entry.code === countryCode ? 'country-chip active' : 'country-chip'}
              onClick={function () { setCountryCode(entry.code) }}
            >
              <span className="flag">{FLAGS[entry.code] || ''}</span>
              {entry.name}
              <span style={{ opacity: 0.7 }}>{entry.currency}</span>
            </button>
          )
        })}
        <span className="standard">
          {country ? country.measurement_standard + ' - ' + country.unit_convention : ''}
        </span>
      </div>

      <div className="notice notice-warn">
        <strong>Demonstration build - not for a real tender decision.</strong> Index series are
        real published data; rates derived from the BCA and CPWD schedules are labelled{' '}
        <em>derived</em>; anything carried forward from a stale index, and any retained estimate, is
        labelled <span className="badge basis-assumed">assumed</span>. The index dashboard shows the
        basis of every row.
      </div>

      {result ? (
        <div className="tile-grid">
          <div className="tile">
            <div className="tile-label">BoQ tender total</div>
            <div className="tile-value">{money(result.totals.boq_total, currency)}</div>
            <div className="tile-sub">{result.totals.line_count} lines - {result.filename}</div>
          </div>
          <div className="tile">
            <div className="tile-label">
              Should-cost total{ohpActive ? ' (benchmark cost)' : ''}
            </div>
            <div className="tile-value">{money(result.totals.should_cost_total, currency)}</div>
            <div className="tile-sub">
              {result.tpi_series_name} index for {result.tender_quarter}
              {result.regional_factor !== 1
                ? ' | ' + result.region_name + ' x' + result.regional_factor.toFixed(3)
                : ''}
              {ohpActive ? ' | before overheads and margin' : ''}
            </div>
          </div>
          {ohpActive ? (
            <div className="tile tile-full">
              <div className="tile-label">Full should-cost (incl. OH&amp;P)</div>
              <div className="tile-value">{money(result.totals.full_should_cost_total, currency)}</div>
              <div className="tile-sub">
                +{money(result.totals.overhead_amount_total, currency)} overheads (
                {num(result.totals.overhead_pct, 1)}%) +{' '}
                {money(result.totals.margin_amount_total, currency)} margin (
                {num(result.totals.margin_pct, 1)}%)
                {result.totals.overheads_in_tender ? ' | compared full-to-full' : ' | variance excludes OH&P'}
              </div>
            </div>
          ) : null}
          <div className={'tile ' + (result.totals.total_variance_abs > 0 ? 'tile-bad' : 'tile-good')}>
            <div className="tile-label">Variance</div>
            <div className="tile-value">{money(result.totals.total_variance_abs, currency)}</div>
            <div className="tile-sub">{pct(result.totals.total_variance_pct)} vs should-cost</div>
          </div>
          <div className="tile tile-warn">
            <div className="tile-label">Breaching threshold</div>
            <div className="tile-value">{result.totals.breached_line_count}</div>
            <div className="tile-sub">of {result.totals.line_count} lines, +/-{num(result.variance_threshold, 1)}%</div>
          </div>
          <div className="tile">
            <div className="tile-label">Not benchmarked</div>
            <div className="tile-value">{result.totals.unbenchmarked_line_count}</div>
            <div className="tile-sub">{money(result.totals.unbenchmarked_boq_total, currency)} carried untested</div>
          </div>
          <div className={'tile ' + (result.index_bridge && result.index_bridge.applied ? 'tile-warn' : 'tile-good')}>
            <div className="tile-label">Index currency</div>
            <div className="tile-value">
              {result.index_bridge && result.index_bridge.observation_quarter
                ? result.index_bridge.observation_quarter
                : 'n/a'}
            </div>
            <div className="tile-sub">
              {result.index_bridge && result.index_bridge.applied
                ? 'last published observation - carried forward to ' + result.index_bridge.bridged_through_month
                  + ' with ' + (result.index_bridge.series_name || result.index_bridge.cpi_series_name)
                  + ' (x' + Number(result.index_bridge.cpi_bridge_factor).toFixed(4) + ')'
                : result.index_bridge && result.index_bridge.lag_quarters > 0
                  ? 'held at the last published observation, '
                    + result.index_bridge.lag_quarters + ' quarter(s) stale - '
                    + (result.index_bridge.mode === 'none'
                      ? 'bridging is switched off'
                      : 'no consumer price series available to bridge with')
                  : 'published observation covers ' + result.tender_quarter}
            </div>
          </div>
        </div>
      ) : null}

      <nav className="tabs">
        {TABS.map(function (item) {
          return (
            <button
              key={item.key}
              type="button"
              className={tab === item.key ? 'tab active' : 'tab'}
              onClick={function () { setTab(item.key) }}
            >
              {item.label}
            </button>
          )
        })}
      </nav>

      {runError ? <div className="notice notice-critical">{runError}</div> : null}

      {tab === 'upload' ? (
        <UploadBoQ
          upload={upload}
          country={country || {}}
          uploads={uploads}
          onUpload={handleUpload}
          onLoadUpload={handleLoadUpload}
          onDownloadTemplate={handleTemplate}
        />
      ) : null}

      {tab !== 'upload' && tab !== 'download' ? (
        <BenchmarkControls
          uploadId={uploadId}
          loading={loading}
          quarters={quarters}
          series={seriesNames}
          tenderQuarter={tenderQuarter}
          tpiSeries={tpiSeries}
          threshold={threshold}
          currency={currency}
          regions={regions}
          regionCode={regionCode}
          activeRegion={regions.filter(function (r) { return r.region_code === regionCode })[0]}
          seriesScope={seriesScopes.filter(function (s) { return s.series_name === tpiSeries })[0]}
          freshness={freshness}
          bridgeMode={bridgeMode}
          indexBridge={result ? result.index_bridge : null}
          onChange={function (patch) {
            if (patch.tenderQuarter !== undefined) setTenderQuarter(patch.tenderQuarter)
            if (patch.tpiSeries !== undefined) setTpiSeries(patch.tpiSeries)
            if (patch.threshold !== undefined) setThreshold(patch.threshold)
            if (patch.regionCode !== undefined) setRegionCode(patch.regionCode)
            if (patch.bridgeMode !== undefined) setBridgeMode(patch.bridgeMode)
          }}
          onRun={runBenchmark}
        />
      ) : null}

      {uploadId && (tab === 'variance' || tab === 'waterfall' || tab === 'indices' || tab === 'sensitivity') ? (
        <IndexAdjusters
          adjustments={adjustments}
          tpiSeries={tpiSeries}
          tpiQuarter={tenderQuarter}
          publishedTpi={result ? result.lines[0] && result.lines[0].tpi_value_published : null}
          usedTpi={result ? result.lines[0] && result.lines[0].tpi_value : null}
          indexBridge={result ? result.index_bridge : null}
          benchmarkCost={result ? result.totals.should_cost_total : null}
          fullShouldCost={result ? result.totals.full_should_cost_total : null}
          currency={currency}
          breakEvenPct={sensitivity ? sensitivity.break_even_scale_pct : null}
          disabled={loading}
          onChange={updateAdjustments}
          onReset={function () { setAdjustments(NO_ADJUSTMENTS) }}
          onResetAndUseBreakEven={function () {
            setAdjustments(Object.assign({}, NO_ADJUSTMENTS, {
              tpi_scale_pct: Number(sensitivity.break_even_scale_pct.toFixed(2)),
            }))
          }}
        />
      ) : null}

      {!uploadId && tab !== 'upload' && tab !== 'download' ? (
        <div className="notice notice-warn">
          No BoQ loaded for {country ? country.name : 'this market'}. Upload one from the Upload tab,
          or pick a seeded sample there.
        </div>
      ) : null}

      {result && (tab === 'variance' || tab === 'waterfall' || tab === 'sensitivity') ? (
        <CoveragePanel
          result={result}
          currency={currency}
          manualRates={manualRates}
          onChange={setManualRate}
          onClear={clearManualRate}
        />
      ) : null}

      {result && tab === 'variance' ? (
        <VarianceTable
          result={result}
          currency={currency}
          sectionOptions={SECTION_OPTIONS}
          onReclassify={handleReclassify}
        />
      ) : null}

      {result && tab === 'waterfall' ? <WaterfallChart result={result} currency={currency} /> : null}

      {tab === 'sensitivity' ? (
        <SensitivityView
          sensitivity={sensitivity}
          uploadId={uploadId}
          loading={loadingSensitivity}
          currency={currency}
          onRun={runSensitivity}
          regions={regions}
          regionCode={regionCode}
        />
      ) : null}

      {tab === 'indices' ? (
        <IndexDashboard
          tpiRows={tpiRows}
          priceRows={priceRows}
          cpiRows={cpiRows}
          ppiRows={ppiRows}
          coverage={indexCoverage}
          coverageUnavailable={coverageUnavailable}
          freshness={freshness}
          indexBridge={result ? result.index_bridge : null}
          materialRows={materialRows}
          benchmarkRates={benchmarkRates}
          seriesScopes={seriesScopes}
          country={country || {}}
          currency={currency}
          tenderQuarter={tenderQuarter}
          bridgeMode={bridgeMode}
        />
      ) : null}

      {tab === 'download' ? (
        <ExportBar
          uploadId={uploadId}
          busy={downloading}
          status={downloadStatus}
          onDownloadTemplate={handleTemplate}
          onExport={handleExport}
        />
      ) : null}

      {result ? (
        <AssumptionsPanel
          warnings={result.warnings}
          assumptions={result.assumptions}
          indicativeLineCount={indicativeLineCount}
          adjustmentsActive={adjustmentsActive}
          indexBridge={result.index_bridge}
        />
      ) : null}

      {classifierRules && tab === 'variance' ? (
        <ClassificationRules rules={classifierRules} />
      ) : null}

      <footer className="app-footer muted">
        Sources referenced by the seed data. <strong>Singapore:</strong> BCA
        Tender Price Index (2010 = 100), SISV Tender Price Index circulars, BCA Construction InfoNet,
        SingStat / BCA material price series, RLB Rider's Digest, Arcadis Quarterly Cost Review,
        SMM2 (Standard Method of Measurement, 2nd Edition). <strong>India:</strong> CPWD Cost Index
        and Delhi Schedule of Rates, WPI (Office of the Economic Adviser, DPIIT), National Buildings
        Organisation, MoSPI, Bureau of Indian Standards (IS 1200, IS 456), RBI Handbook of
        Statistics.
      </footer>
    </div>
  )
}
