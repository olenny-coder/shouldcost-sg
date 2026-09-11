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
import ExportBar from './components/ExportBar.jsx'
import CoveragePanel from './components/CoveragePanel.jsx'

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
}

export default function App() {
  const [health, setHealth] = useState(null)
  const [serverConfig, setServerConfig] = useState(null)
  const [bootError, setBootError] = useState(null)
  const [countries, setCountries] = useState([])
  const [countryCode, setCountryCode] = useState('SG')

  const [tpiRows, setTpiRows] = useState([])
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
  const [tenderQuarter, setTenderQuarter] = useState('2024Q4')
  const [tpiSeries, setTpiSeries] = useState('BCA')
  const [threshold, setThreshold] = useState('15')
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
      try {
        const responses = await Promise.all([
          api.listTpi({ country: countryCode }),
          api.listMaterials({ country: countryCode }),
          api.listBenchmarkRates({ country: countryCode }),
          api.listClassifierRules({ country: countryCode }),
          api.listUploads(countryCode),
          api.listRegions({ country: countryCode }),
        ])
        if (cancelled) return
        const tpi = responses[0]
        setTpiRows(tpi)
        setMaterialRows(responses[1])
        setBenchmarkRates(responses[2])
        setClassifierRules(responses[3])
        setUploads(responses[4])
        setRegions(responses[5])
        const defaultRegion = responses[5].filter(function (r) { return r.is_default })[0]
        setRegionCode(defaultRegion ? defaultRegion.region_code : (responses[5][0] || {}).region_code || '')

        const registry = countries.filter(function (c) { return c.code === countryCode })[0]
        const names = Array.from(new Set(tpi.map(function (r) { return r.series_name }))).sort()
        const preferred = registry && names.indexOf(registry.default_tpi_series) !== -1
          ? registry.default_tpi_series
          : (names[0] || 'BCA')
        const quarters = Array.from(new Set(tpi.map(function (r) { return r.quarter }))).sort()
        let quarter = quarters.length ? quarters[quarters.length - 1] : '2024Q4'
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
          if (detail.tpi_series_name && names.indexOf(detail.tpi_series_name) !== -1) {
            series = detail.tpi_series_name
          }
          applyUploadDetail(detail)
        }
        setTpiSeries(series)
        setTenderQuarter(quarter)
        setTab('variance')
      } catch (error) {
        if (!cancelled) setRunError(error.message)
      }
    }
    if (countryCode) loadCountry()
    return function () { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [countryCode, countries.length])

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
      tpi_series_name: tpiSeries,
      variance_threshold: Number(threshold) || 0,
      region_code: regionCode || null,
      adjustments: adjustments,
      manual_rates: supplied,
    }
  }, [tenderQuarter, tpiSeries, threshold, regionCode, adjustmentKey, manualKey])

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
    if (!uploadId) return undefined
    const handle = setTimeout(function () { runBenchmark() }, 300)
    return function () { clearTimeout(handle) }
  }, [uploadId, benchmarkBody, runBenchmark])

  const benchmarkPayload = useMemo(function () {
    return {
      tender_quarter: tenderQuarter,
      tpi_series_name: tpiSeries,
      variance_threshold: Number(threshold) || 0,
      region_code: regionCode || null,
      adjustments: adjustments,
    }
  }, [tenderQuarter, tpiSeries, threshold, regionCode, adjustmentKey])

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
  const placeholderLineCount = result
    ? result.lines.filter(function (l) { return l.provenance && l.provenance.is_placeholder }).length
    : 0
  const adjustmentsActive = result && result.adjustments_applied && !result.adjustments_applied.is_noop
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
    return set.length ? set.sort().reverse() : ['2024Q4']
  }, [tpiRows])

  const seriesNames = useMemo(function () {
    const set = []
    tpiRows.forEach(function (row) { if (set.indexOf(row.series_name) === -1) set.push(row.series_name) })
    return set.length ? set.sort() : ['BCA']
  }, [tpiRows])

  return (
    <div className="app">
      <header className="app-header">
        <div>
          <h1>shouldcost <span>| BoQ benchmarking</span></h1>
          <p>
            Should-cost benchmarking for the Singapore and India construction markets.
            Section classification, published cost-index adjustment, variance, waterfall and
            sensitivity reporting - all offline, all evidence-tagged.
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
        <strong>Demonstration build.</strong> All bundled index values and benchmark rates for both
        markets are synthetic placeholders carrying <code>is_placeholder: true</code>, a{' '}
        <code>source_url</code> and a <code># TODO: replace with actual ...</code> marker. Do not
        use for a real tender decision.
      </div>

      {result ? (
        <div className="tile-grid">
          <div className="tile">
            <div className="tile-label">BoQ tender total</div>
            <div className="tile-value">{money(result.totals.boq_total, currency)}</div>
            <div className="tile-sub">{result.totals.line_count} lines - {result.filename}</div>
          </div>
          <div className="tile">
            <div className="tile-label">Should-cost total</div>
            <div className="tile-value">{money(result.totals.should_cost_total, currency)}</div>
            <div className="tile-sub">
              {result.tpi_series_name} index for {result.tender_quarter}
              {result.regional_factor !== 1
                ? ' | ' + result.region_name + ' x' + result.regional_factor.toFixed(3)
                : ''}
            </div>
          </div>
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
          onChange={function (patch) {
            if (patch.tenderQuarter !== undefined) setTenderQuarter(patch.tenderQuarter)
            if (patch.tpiSeries !== undefined) setTpiSeries(patch.tpiSeries)
            if (patch.threshold !== undefined) setThreshold(patch.threshold)
            if (patch.regionCode !== undefined) setRegionCode(patch.regionCode)
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
          materialRows={materialRows}
          benchmarkRates={benchmarkRates}
          seriesScopes={seriesScopes}
          country={country || {}}
          currency={currency}
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
          placeholderLineCount={placeholderLineCount}
          adjustmentsActive={adjustmentsActive}
        />
      ) : null}

      {classifierRules && tab === 'variance' ? (
        <section className="panel">
          <h2>Classification rules - {classifierRules.classification_standard}</h2>
          <p className="muted small">{classifierRules.measurement_note}</p>
          <p className="muted small">{classifierRules.note}</p>
          <div className="table-scroll">
            <table className="data-table compact">
              <thead><tr><th>Section</th><th>Rule</th></tr></thead>
              <tbody>
                {classifierRules.rules.map(function (rule) {
                  return (
                    <tr key={rule.smm2_section}>
                      <td><span className="section-pill">{rule.smm2_section}</span></td>
                      <td className="mono small">{rule.rule}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </section>
      ) : null}

      <footer className="app-footer muted">
        Sources referenced by the seed data (all values synthetic). <strong>Singapore:</strong> BCA
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
