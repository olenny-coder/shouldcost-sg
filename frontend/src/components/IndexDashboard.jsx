import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import { money, num, currencySymbol } from '../format.js';
import { useTheme } from '../theme.jsx';
import {
  bridgeFactor, bridgeFromMonths, bridgeFromValue, bridgeKind, bridgeKindLong,
  bridgeReason, bridgeSeries, bridgeToMonths, bridgeToValue,
} from '../bridge.js';
import HelpPopout from './HelpPopout.jsx';

const MATERIAL_LABEL = {
  cement: 'Cement',
  steel_rebar: 'Steel reinforcement',
  ready_mix_concrete: 'Ready-mixed concrete',
};

// How well each measurement section is covered by a published index.
const COVERAGE_STATUS = {
  producer_covered: {
    label: 'producer index',
    tone: 'basis-measured',
    help: 'a published producer price index re-prices this section',
  },
  producer_plus_labour_gap: {
    label: 'producer index, labour gap',
    tone: 'basis-derived',
    help: 'materials and fuel are indexed; site labour is indexed by no publication at all',
  },
  consumer_only: {
    label: 'consumer index only',
    tone: 'basis-assumed',
    help: 'no producer basket maps to this section, so the weaker consumer proxy is used',
  },
  consumer_plus_labour_gap: {
    label: 'consumer index, labour gap',
    tone: 'basis-assumed',
    help: 'no producer basket maps here, and site labour is not indexed either',
  },
  uncovered: {
    label: 'no published series',
    tone: 'basis-assumed',
    help: 'held at base year - nothing loaded re-prices this section',
  },
};

function fmtMonths(months) {
  if (!months || !months.length) return 'n/a';
  if (months.length === 1) return months[0];
  return months[0] + '-' + months[months.length - 1];
}

function kindShort(kind) {
  return kind === 'PPI' ? 'PPI' : 'CPI';
}

// The section coverage list is reference material rather than a result: 11 sections, each
// with a rate, a series list and a gap. It collapses as a whole AND per section, and the
// choice is remembered, so a returning analyst is not made to scroll past it every time.
const COVERAGE_STORAGE_KEY = 'shouldcost.index-dashboard.coverage-collapsed';

function readCoverageCollapsed() {
  try {
    return window.localStorage.getItem(COVERAGE_STORAGE_KEY) === '1';
  } catch (error) {
    return false;
  }
}

function BasisBadge(props) {
  // "published" means the number is a real observation from the named source.
  // "derived" means the engine computed it from published values - a bridged index,
  // for example - so it shows the trend to date rather than a published figure.
  return props.derived
    ? <span className="badge basis-derived">derived to date</span>
    : <span className="badge basis-measured">published</span>;
}

export default function IndexDashboard(props) {
  const tpiRows = props.tpiRows || [];
  const priceRows = props.priceRows || [];
  const cpiRows = props.cpiRows || [];
  const ppiRows = props.ppiRows || [];
  const coverage = props.coverage || null;
  const materialRows = props.materialRows || [];
  const benchmarkRates = props.benchmarkRates || [];
  const country = props.country || { name: 'Singapore', currency: 'SGD', base_year: 2010, sources: [] };
  const currency = props.currency || country.currency || 'SGD';
  const { theme, charts } = useTheme();
  const [material, setMaterial] = useState('steel_rebar');
  // DISPLAY-ONLY scenario multiplier. Material prices are not part of the rate
  // formula, so this never changes should-cost - it is labelled as such.
  const [materialScenarioPct, setMaterialScenarioPct] = useState(0);
  const [coverageOpen, setCoverageOpen] = useState(function () { return !readCoverageCollapsed(); });

  useEffect(function () {
    try {
      window.localStorage.setItem(COVERAGE_STORAGE_KEY, coverageOpen ? '0' : '1');
    } catch (error) {
      /* private mode, or storage disabled - the panel still works */
    }
  }, [coverageOpen]);

  const toggleCoverage = useCallback(function () {
    setCoverageOpen(function (current) { return !current; });
  }, []);

  // One series palette entry per published index series. Related series share an
  // entry (the BCA and CPWD rate schedules, the two labour series) and anything
  // unrecognised falls back to the muted grey.
  const seriesColors = useMemo(function () {
    return {
      BCA: charts.series[0], HDB: charts.series[2], RLB: charts.series[1], AECOM: charts.series[4],
      AIS: charts.series[3], CPWD: charts.series[0], NBO: charts.series[2], 'WPI-CON': charts.series[1],
    };
  }, [theme, charts]);

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

  // Every row shown anywhere on this page, tagged with the basis it is on.
  const allRows = tpiRows.concat(priceRows, materialRows, benchmarkRates);
  const derivedOrRetainedCount = allRows.filter(function (r) { return r.is_placeholder; }).length;
  const publishedCount = allRows.length - derivedOrRetainedCount;

  const selectedUnit = (materialRows.filter(function (r) { return r.material === material; })[0] || {}).unit || '';
  // India publishes WPI cost indices; Singapore publishes actual prices. Say which.
  const materialIsIndex = selectedUnit.indexOf('index') === 0;

  const freshness = props.freshness || null;
  const bridgeMode = props.bridgeMode || 'auto';
  const preferredKind = (freshness && freshness.bridge_preference) || 'none';
  // An API older than this UI has no producer side at all. Read every producer field
  // through these, so a stale backend degrades to "none published" instead of
  // throwing on an undefined property.
  const ppiSeriesList = (freshness && freshness.ppi_series_list) || [];
  const ppiAvailable = !!(freshness && freshness.ppi_series_available);

  const cpiData = useMemo(function () {
    return cpiRows
      .map(function (row) { return { month: row.month, value: row.value }; })
      .sort(function (a, b) { return a.month.localeCompare(b.month); });
  }, [cpiRows]);

  const ppiData = useMemo(function () {
    // India publishes 16 commodity baskets; charting all of them at once is noise,
    // so the preferred basket is charted and the rest are listed beneath.
    const preferred = (freshness && freshness.ppi_series_name) || '';
    const chosen = ppiRows.filter(function (row) { return row.series_name === preferred; });
    const rows = chosen.length ? chosen : ppiRows;
    const byMonth = {};
    const names = [];
    rows.forEach(function (row) {
      if (!byMonth[row.month]) byMonth[row.month] = { month: row.month };
      byMonth[row.month][row.series_name] = row.value;
      if (names.indexOf(row.series_name) === -1) names.push(row.series_name);
    });
    return {
      points: Object.keys(byMonth).sort().map(function (m) { return byMonth[m]; }),
      names: names.sort(),
    };
  }, [ppiRows, freshness]);

  const cpiMeta = useMemo(function () {
    if (!cpiRows.length) return { series: [], latest: null, unit: '' };
    const series = Array.from(new Set(cpiRows.map(function (r) { return r.series_name; }))).sort();
    const latest = cpiRows.slice().sort(function (a, b) { return a.month.localeCompare(b.month); }).slice(-1)[0];
    return { series: series, latest: latest, unit: latest.unit || ('index (base ' + latest.base_year + ' = 100)') };
  }, [cpiRows]);

  // A benchmark rate row is either derived from a published schedule of rates or carried as a
  // retained estimate from a named source.
  const rateQuality = useMemo(function () {
    const map = {};
    benchmarkRates.forEach(function (row) {
      const entry = map[row.smm2_section] || { retained: false, note: '', url: '' };
      if (row.is_placeholder) entry.retained = true;
      if (!entry.note) entry.note = row.provenance_note || '';
      if (!entry.url) entry.url = row.source_url || '';
      map[row.smm2_section] = entry;
    });
    return map;
  }, [benchmarkRates]);

  const derivedRateCount = benchmarkRates.filter(function (r) { return !r.is_placeholder; }).length;
  const coverageRetained = coverage
    ? coverage.sections.filter(function (s) { return s.rate && s.rate.is_placeholder; }).length
    : 0;
  const baseYears = Array.from(new Set(tpiRows.map(function (r) { return r.base_year; }))).sort();
  const baseYearLabel = baseYears.length ? baseYears.join('/') : 'n/a';

  return (
    <section className="panel">
      <h2>
        Index dashboard - {country.name}
        <HelpPopout
          title="Reading the index dashboard"
          items={[
            'The first chart is every published construction cost index series for this market, all rebased so they can be compared on one axis.',
            'The coverage table beneath it is the important one: for every measurement section in this standard, it names the published series that re-prices it, and says where there is no series at all. A section with no covering series cannot be escalated - the benchmark holds it at base year and warns.',
            'Every row is badged published or derived. Published means the number is printed in the named publication. Derived means the engine computed it from published values - most often by carrying a stale index forward along the published trend, which shows the movement to date rather than a tender-quarter observation.',
            'The engine carries a stale index forward with a PRODUCER price index first, because a producer index measures what suppliers charge for the cement, steel, minerals, fuel and power that a construction rate is made of. A CONSUMER price index is used only where the market publishes no producer series covering the window.',
            'The "Data currency and the index bridge" table states, per series, the last quarter actually published, how stale that is against the quarter you are pricing, and the index used to carry it forward. The index kind column says which of the two it was.',
            'A bridged index is a modelled step, not an observation: every line it touches is reported as basis=assumed. Switch it off with the "Stale index" selector and those lines are held at the last published level instead.',
            'The material chart shows input costs. For Singapore these are actual prices from BCA; for India they are WPI cost indices, so the axis is index points, not rupees. The slider is display-only.',
            'The rate library at the bottom is what actually prices your BoQ: the published schedule of rates for each section, escalated to the quarter this library is stated at. Every row names its source and the quarter it is expressed at.',
            'The sources table names the publications each market relies on. Refresh them with python -m app.importer.',
          ]}
          footnote="The app makes no outbound call to any index provider at runtime - all of this is served from the local database."
        />
      </h2>

      <div className="tile-grid">
        <div className="tile tile-good">
          <div className="tile-label">Published observations</div>
          <div className="tile-value">{publishedCount}</div>
          <div className="tile-sub">rows printed in the named official source</div>
        </div>
        <div className={derivedOrRetainedCount ? 'tile tile-warn' : 'tile'}>
          <div className="tile-label">Derived rows</div>
          <div className="tile-value">{derivedOrRetainedCount}</div>
          <div className="tile-sub">computed from published values or carried from a retained series</div>
        </div>
        <div className="tile">
          <div className="tile-label">Producer index used</div>
          <div className="tile-value">
            {ppiAvailable ? (freshness.ppi_latest_month || 'n/a') : 'none'}
          </div>
          <div className="tile-sub">
            {ppiAvailable
              ? freshness.ppi_series_name + (freshness.ppi_base_year ? ' (base ' + freshness.ppi_base_year + ' = 100)' : '')

              : 'no producer series for this market - the bridge falls back to the consumer index'}
          </div>
        </div>
        <div className="tile">
          <div className="tile-label">Consumer index used</div>
          <div className="tile-value">{freshness && freshness.cpi_latest_month ? freshness.cpi_latest_month : 'n/a'}</div>
          <div className="tile-sub">
            {freshness && freshness.cpi_latest_value !== null && freshness.cpi_latest_value !== undefined
              ? freshness.cpi_series_name + ' = ' + num(freshness.cpi_latest_value, 3)
                + (freshness.cpi_base_year ? ' (base ' + freshness.cpi_base_year + ' = 100)' : '')
              : 'no consumer price series loaded for this market'}
          </div>
        </div>
      </div>

      <div className="notice notice-info">
        <strong>What is published and what is derived.</strong> Every index observation on this page
        is real published data from the source named against it. Everything the engine does{' '}
        <em>with</em> those observations is derived: a stale index is carried forward along the
        published trend to show the movement <strong>to date</strong>, which is why a bridged value
        is reported as <code>basis: assumed</code> and never as an observation. The rate library is
        the published schedule of rates for each section, escalated to the quarter the library is
        stated at - a derived rate from a named source, which is what makes it the rate this app
        benchmarks against. No figure in this app is invented, and no source is called at runtime.
      </div>

      <h3>
        Section coverage - the rate, and the index that carries it to current
        <span className="h2-actions">
          <button
            type="button"
            className="secondary small-btn"
            onClick={toggleCoverage}
            aria-expanded={coverageOpen}
            aria-controls="coverage-body"
          >
            <span className="chev">{coverageOpen ? '\u25BC' : '\u25B6'}</span>
            {coverageOpen ? 'Collapse' : 'Expand'}
          </button>
        </span>
      </h3>
      <p className="muted small">
        Per section: the <strong>rate</strong> that prices the BoQ and the quarter it is expressed
        at, and the <strong>published series</strong> that can re-price it. {coverage
          ? coverage.producer_covered_sections.length + ' of ' + coverage.sections.length
            + ' sections are covered by a published producer index in ' + coverage.country_name + '.'
          : (props.coverageUnavailable
            ? 'This view is not available from the API you are connected to.'
            : 'Coverage data has not loaded yet.')}
      </p>
      {coverage && coverage.carry_index_note ? (
        <div className="notice notice-info">
          <strong>How rates reach the quarter being priced.</strong> {coverage.carry_index_note}{' '}
          {coverage.carry_index_kind === 'producer'
            ? 'A producer index measures what suppliers charge for the commodity baskets a construction rate is made of, so it is the stronger of the two proxies.'
            : coverage.carry_index_kind === 'consumer'
              ? 'This market publishes no usable producer index, so consumer prices carry the rates - a weaker proxy, disclosed on every affected line.'
              : ''}{' '}
          The schedules of rates were themselves <strong>cumulative-adjusted to their stated
          quarter</strong> - BCA by 1.171 and CPWD by 1.2364, each the publisher-file cumulative CPI
          factor - so the app escalates from that quarter, never from the index series&rsquo; own
          base year, which would apply the same inflation twice.
        </div>
      ) : null}
      {props.coverageUnavailable && !coverage ? (
        <div className="notice notice-warn">
          <strong>The section coverage table is unavailable on this deployment.</strong>{' '}
          <code>GET /api/indices/coverage</code> returned 404, which means the API is running a
          build from before this panel existed - most often because the frontend was deployed ahead
          of the backend. Everything else on this page is working, and no figure here is affected:
          redeploy the backend service and reload. Details:{' '}
          <code>GET /api/indices/coverage?country=</code>{' '}
          {country.code || 'SG'} on the API host configured for this site.
        </div>
      ) : null}
      {coverage && !coverageOpen ? (
        <div className="assumptions-strip">
          <span className="badge basis-derived">
            {coverage.producer_covered_sections.length} of {coverage.sections.length} producer-covered
          </span>
          <span className="badge basis-measured">
            rates carried by {coverage.carry_index_series || 'no index'}
          </span>
          <span className="badge basis-assumed">{coverageRetained} retained</span>
          <button type="button" className="linkish small" onClick={toggleCoverage}>
            Show all {coverage.sections.length} sections
          </button>
        </div>
      ) : null}

      {coverage && coverageOpen ? (
        <div id="coverage-body">
          <ul className="coverage-list">
            {coverage.sections.map(function (row) {
              const status = COVERAGE_STATUS[row.status] || COVERAGE_STATUS.uncovered;
              const series = row.producer_series.concat(row.consumer_series);
              return (
                <li key={row.section}>
                  <details className="coverage-item">
                    <summary>
                      <span className="section-pill">{row.section}</span>
                      <span className="coverage-headline">
                        {row.rate
                          ? <span><strong>{num(row.rate.base_rate, 2)}</strong>{' '}
                              <span className="muted">per {row.rate.unit}</span></span>
                          : <span className="muted">no library rate</span>}
                      </span>
                      {row.rate ? (
                        row.rate.is_placeholder
                          ? <span className="badge basis-assumed">retained</span>
                          : <span className="badge basis-derived">derived {row.rate.base_quarter}</span>
                      ) : null}
                      <span className={'badge ' + status.tone}>{status.label}</span>
                      <span className="chev" aria-hidden="true">{'\u25B8'}</span>
                    </summary>
                    <div className="coverage-detail">
                      {row.rate ? (
                        <div className="coverage-detail-block">
                          <div className="coverage-detail-label">Rate library</div>
                          <div className="small">
                            {row.rate.source}
                            <div className="muted">
                              carried from {row.rate.carried_from} to the tender quarter by{' '}
                              {coverage.carry_index_series || 'no index'} - confidence{' '}
                              {row.rate.confidence}
                            </div>
                          </div>
                        </div>
                      ) : null}

                      <div className="coverage-detail-block">
                        <div className="coverage-detail-label">Index coverage</div>
                        <div className="small">{status.help}</div>
                        {series.length ? (
                          <ul className="coverage-series">
                            {series.map(function (s) {
                              return (
                                <li key={s.series_name}>
                                  <span className={
                                    'badge ' + (s.kind === 'PPI' ? 'basis-measured' : 'basis-derived')
                                  }>{kindShort(s.kind)}</span>{' '}
                                  <strong>{s.series_name}</strong>
                                  <span className="muted"> - {s.title}</span>
                                </li>
                              );
                            })}
                          </ul>
                        ) : (
                          <div className="muted small">nothing loaded re-prices this section</div>
                        )}
                      </div>

                      {row.note ? (
                        <div className="coverage-detail-block">
                          <div className="coverage-detail-label">How it works</div>
                          <div className="small">{row.note}</div>
                          {row.market_note ? (
                            <div className="muted small">{row.market_note}</div>
                          ) : null}
                        </div>
                      ) : null}

                      {row.gap_sources.length ? (
                        <div className="coverage-detail-block">
                          <div className="coverage-detail-label">Still to wire in</div>
                          <ul className="coverage-series">
                            {row.gap_sources.map(function (gap) {
                              return (
                                <li key={gap.name}>
                                  <span className={
                                    'badge ' + (gap.status === 'wired' ? 'basis-measured' : 'basis-assumed')
                                  }>
                                    {gap.status === 'wired' ? 'wired in'
                                      : (gap.status === 'partial' ? 'partly wired' : 'documented gap')}
                                  </span>{' '}
                                  <a href={gap.url} target="_blank" rel="noreferrer">{gap.name}</a>
                                  <div className="muted small">{gap.what}</div>
                                </li>
                              );
                            })}
                          </ul>
                        </div>
                      ) : null}
                    </div>
                  </details>
                </li>
              );
            })}
          </ul>
          <ul className="muted small coverage-notes">
            {coverage.notes.map(function (note) {
              return <li key={note}>{note}</li>;
            })}
          </ul>
        </div>
      ) : null}

      <h3>Tender / construction cost index by series (base year {baseYearLabel} = 100)</h3>
      <div className="chart-card">
        <div style={{ width: '100%', height: 320 }}>
          <ResponsiveContainer>
            <LineChart data={tpiData.points} margin={{ top: 10, right: 20, bottom: 10, left: 6 }}>
              <CartesianGrid strokeDasharray="3 3" stroke={charts.grid} />
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
                    stroke={seriesColors[name] || charts.series[7]}
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
              const quality = rateQuality[scope.series_name] || {};
              return (
                <tr key={scope.series_name}>
                  <td><span className="section-pill">{scope.series_name}</span></td>
                  <td><BasisBadge derived={false} /></td>
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

      <h3>Data currency and the index bridge - {country.name}</h3>
      <p className="muted small">
        Published construction cost indexes lag. These tables state, per series, the last quarter
        actually published and what the benchmark does about the gap between that quarter and the
        one being priced. The bridge is only ever a <strong>modelled</strong> step: it carries the
        last real observation forward along the published movement of a price index, which shows the
        trend to date rather than the tender quarter. The engine takes a{' '}
        <strong>producer price index first</strong> - it measures what suppliers charge for the
        materials and fuel a construction rate is made of - and falls back to a{' '}
        <strong>consumer price index</strong> only where no producer series spans the window.
        Whichever is used, every line it touches is reported as{' '}
        <span className="badge basis-assumed">assumed</span>.
      </p>
      {props.indexBridge ? (
        <p className={'small ' + (props.indexBridge.applied ? 'bridge-line' : 'muted')}>
          <strong>Last benchmark run:</strong>{' '}
          {props.indexBridge.applied
            ? 'the index for ' + props.indexBridge.requested_quarter + ' was carried forward from '
              + props.indexBridge.observation_quarter + ' to ' + props.indexBridge.bridged_through_month
              + ' along the published trend of ' + bridgeSeries(props.indexBridge) + ' ('
              + bridgeKindLong(bridgeKind(props.indexBridge)) + ', factor '
              + num(bridgeFactor(props.indexBridge), 4) + ').'
            : 'the index observation covers ' + props.indexBridge.requested_quarter
              + ' or no bridge was applied (' + bridgeReason(props.indexBridge.reason) + ').'}
        </p>
      ) : null}

      <div className="tile-grid">
        <div className="tile">
          <div className="tile-label">Quarter being priced</div>
          <div className="tile-value">{freshness ? freshness.reference_quarter : (props.tenderQuarter || 'n/a')}</div>
          <div className="tile-sub">
            stale index series are {bridgeMode === 'none'
              ? 'held at their last published observation'
              : 'carried forward along the published trend of a ' + (preferredKind === 'producer' ? 'producer' : 'consumer') + ' price index'}
          </div>
        </div>
        <div className="tile">
          <div className="tile-label">Bridge of first resort</div>
          <div className="tile-value">{preferredKind === 'producer' ? 'PPI' : (preferredKind === 'consumer' ? 'CPI' : 'none')}</div>
          <div className="tile-sub">
            {preferredKind === 'producer'
              ? (freshness ? freshness.ppi_series_name : '') + ' - a producer basket, used before any consumer index'
              : 'this market publishes no usable producer index, so consumer prices carry the bridge'}
          </div>
        </div>
        <div className={'tile ' + (derivedOrRetainedCount ? 'tile-warn' : 'tile-good')}>
          <div className="tile-label">Retained index series</div>
          <div className="tile-value">
            {freshness ? freshness.series.filter(function (s) { return s.is_placeholder; }).length : 0}
          </div>
          <div className="tile-sub">of {freshness ? freshness.series.length : 0} construction index series in this market; the rest are published to the current quarter</div>
        </div>
      </div>

      <div className="table-scroll">
        <table className="data-table compact">
          <thead>
            <tr>
              <th>Series</th>
              <th>Last published</th>
              <th className="num">Value</th>
              <th className="num">Lag (qtrs)</th>
              <th>Carried forward with</th>
              <th className="num">Factor</th>
              <th className="num">Index used</th>
            </tr>
          </thead>
          <tbody>
            {(freshness ? freshness.series : []).map(function (row) {
              const bridge = row.bridge || {};
              return (
                <tr key={row.series_name}>
                  <td><span className="section-pill">{row.series_name}</span></td>
                  <td>
                    {row.latest_quarter}
                    <div>
                      {row.is_placeholder
                        ? <span className="badge basis-assumed">retained</span>
                        : <span className="badge basis-measured">published</span>}
                    </div>
                  </td>
                  <td className="num">{num(row.latest_value, 2)}</td>
                  <td className="num">{row.lag_quarters}</td>
                  <td>
                    {bridge.applied
                      ? <span>
                          <span className={'badge ' + (bridgeKind(bridge) === 'PPI' ? 'basis-measured' : 'basis-derived')}>
                            {kindShort(bridgeKind(bridge))}
                          </span>{' '}
                          <strong>{bridgeSeries(bridge)}</strong>
                          <div className="small muted">
                            derived to {bridge.bridged_through_month} from
                            {' '}{fmtMonths(bridgeFromMonths(bridge))} {num(bridgeFromValue(bridge), 3)}
                            {' → '}
                            {fmtMonths(bridgeToMonths(bridge))} {num(bridgeToValue(bridge), 3)}
                          </div>
                          {bridge.shortfall_months > 0 ? (
                            <div className="small bridge-line">
                              {bridge.shortfall_months} month(s) short of the quarter end
                            </div>
                          ) : null}
                        </span>
                      : <span className="muted small">
                          {row.lag_quarters === 0 ? 'already covers the quarter' : bridgeReason(bridge.reason)}
                        </span>}
                  </td>
                  <td className="num">
                    {bridge.applied
                      ? <span className="badge basis-derived">x{num(bridgeFactor(bridge), 4)}</span>
                      : <span className="muted">1.0000</span>}
                  </td>
                  <td className="num">
                    {num(bridge.index_value_used !== null && bridge.index_value_used !== undefined
                      ? bridge.index_value_used : row.latest_value, 2)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="muted small">
        Bridge formula:{' '}
        <code>
          index(tender quarter) = index(last published quarter) x price index(bridged month) / price
          index(last published quarter)
        </code>
        , where each endpoint is the mean of the months available in that quarter. Both endpoints are
        real published values; the step that applies a commodity or consumer price movement to a
        construction cost index is the modelled part, and the result is derived to show the trend to
        date. Switch it off with the "Stale index" selector above the tabs.
      </p>

      <h3>
        Producer price index
        {ppiAvailable ? ' (' + freshness.ppi_series_name + ')' : ''}
        {' - '}{country.name}
      </h3>
      <p className="muted small">
        {ppiRows.length
          ? 'This is what the bridge reaches for first: a producer price index measures what '
            + 'manufacturers and utilities charge for the commodity baskets a construction rate is '
            + 'made of. ' + ppiSeriesList.length + ' construction-relevant basket(s) are loaded, monthly, '
            + 'base 2022-23 = 100; the preferred one is charted and the rest are listed below.'
          : 'This market publishes no machine-readable producer price index at commodity level. '
            + 'Singapore\u2019s 1-digit Domestic Supply Price Index is annual, which is too coarse to '
            + 'carry a quarterly observation, so the bridge falls back to the consumer index below. '
            + 'That is a documented gap, not an omission.'}
      </p>
      {ppiData.points.length ? (
        <div className="chart-card">
          <div style={{ width: '100%', height: 280 }}>
            <ResponsiveContainer>
              <LineChart data={ppiData.points} margin={{ top: 10, right: 20, bottom: 10, left: 6 }}>
                <CartesianGrid strokeDasharray="3 3" stroke={charts.grid} />
                <XAxis dataKey="month" tick={{ fontSize: 11 }} minTickGap={24} />
                <YAxis
                  domain={['auto', 'auto']}
                  tick={{ fontSize: 11 }}
                  label={{ value: 'index (2022-23 = 100)', angle: -90, position: 'insideLeft', fontSize: 10, fill: charts.textMuted }}
                />
                <Tooltip formatter={function (value) { return num(value, 1) + ' index points'; }} />
                <Legend />
                {ppiData.names.map(function (name) {
                  return (
                    <Line
                      key={name}
                      type="monotone"
                      dataKey={name}
                      name={name}
                      stroke={charts.primary}
                      strokeWidth={2.4}
                      dot={false}
                    />
                  );
                })}
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>
      ) : null}
      {ppiSeriesList.length ? (
        <div className="table-scroll">
          <table className="data-table compact">
            <thead>
              <tr>
                <th>Producer basket</th><th>What it measures</th><th>Sections it re-prices</th>
                <th>Base</th><th>Covers</th><th className="num">Latest</th><th>Role</th>
              </tr>
            </thead>
            <tbody>
              {ppiSeriesList.map(function (row) {
                return (
                  <tr key={row.series_name}>
                    <td><span className="section-pill">{row.series_name}</span></td>
                    <td className="small">{row.title}</td>
                    <td className="small">{(row.scope_sections || '').split(';').filter(Boolean).join(', ') || '-'}</td>
                    <td>{row.base_year} = 100</td>
                    <td className="small">{row.first_month} .. {row.last_month} ({row.observations} months)</td>
                    <td className="num">{num(row.latest_value, 1)}</td>
                    <td>
                      {row.is_preferred
                        ? <span className="badge basis-derived">preferred</span>
                        : <span className="badge">available</span>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : null}

      <h3>Consumer price index ({cpiMeta.series.join(', ') || 'not loaded'})</h3>
      <p className="muted small">
        The fallback series: used only where no producer index spans the window, because consumer
        prices measure what households pay rather than what is bought for a building.{' '}
        {cpiRows.length} monthly observation(s) loaded, all real published data.
        {freshness && freshness.cpi_source_url ? (
          <span>
            {' '}Source:{' '}
            <a href={freshness.cpi_source_url} target="_blank" rel="noreferrer">
              {freshness.cpi_source_url}
            </a>
          </span>
        ) : null}
      </p>
      {freshness && freshness.cpi_series_list && freshness.cpi_series_list.length > 1 ? (
        <>
          <p className="muted small">
            The publisher has rebased this index, so more than one consumer price series is loaded
            and <strong>only the preferred one is charted</strong>. The bridge picks whichever series
            covers both of its endpoints, and never chains or splices across a base change - if none
            spans the two quarters, it says so instead of inventing a level shift.
          </p>
          <div className="table-scroll">
            <table className="data-table compact">
              <thead>
                <tr>
                  <th>CPI series</th><th>Base</th><th>Covers</th>
                  <th className="num">Latest</th><th>Role</th>
                </tr>
              </thead>
              <tbody>
                {freshness.cpi_series_list.map(function (row) {
                  return (
                    <tr key={row.series_name}>
                      <td><span className="section-pill">{row.series_name}</span></td>
                      <td>{row.base_year} = 100</td>
                      <td className="small">{row.first_month} .. {row.last_month} ({row.observations} months)</td>
                      <td className="num">{num(row.latest_value, 3)}</td>
                      <td>
                        {row.is_preferred
                          ? <span className="badge basis-derived">preferred</span>
                          : <span className="badge">fallback</span>}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      ) : null}
      {cpiData.length ? (
        <div className="chart-card">
          <div style={{ width: '100%', height: 260 }}>
            <ResponsiveContainer>
              <LineChart data={cpiData} margin={{ top: 10, right: 20, bottom: 10, left: 6 }}>
                <CartesianGrid strokeDasharray="3 3" stroke={charts.grid} />
                <XAxis dataKey="month" tick={{ fontSize: 11 }} minTickGap={24} />
                <YAxis
                  domain={['auto', 'auto']}
                  tick={{ fontSize: 11 }}
                  label={{
                    value: cpiMeta.latest ? 'index (' + cpiMeta.latest.base_year + ' = 100)' : 'index',
                    angle: -90, position: 'insideLeft', fontSize: 10, fill: charts.textMuted,
                  }}
                />
                <Tooltip formatter={function (value) { return num(value, 3) + ' index points'; }} />
                <Legend />
                <Line
                  type="monotone"
                  dataKey="value"
                  name="CPI, all items"
                  stroke={charts.accent}
                  strokeWidth={2.4}
                  dot={false}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>
      ) : (
        <div className="notice notice-warn">
          No consumer price series is loaded for this market, so a stale index cannot be carried
          forward and is held at its last published observation instead. Import one with{' '}
          <code>python -m app.importer --kind price_series</code>.
        </div>
      )}

      <h3>{materialIsIndex ? 'Material cost index' : 'Material price trend'} - {country.name}</h3>
      <p className="muted small">
        {materialIsIndex
          ? 'For India the published series is a cost INDEX (2022-23 = 100), not a rupee price - the '
            + 'chart plots index points. The unit is shown against the axis.'
          : 'Published prices in ' + currency + ' per the unit shown.'}{' '}
        Period labels follow the source: an annual series shows the year, a monthly series shows
        year-month. All {materialRows.length} observations here are real published data.
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
              <CartesianGrid strokeDasharray="3 3" stroke={charts.grid} />
              <XAxis dataKey="month" tick={{ fontSize: 11 }} />
              <YAxis
                domain={['auto', 'auto']}
                tick={{ fontSize: 11 }}
                label={{ value: selectedUnit, angle: -90, position: 'insideLeft', fontSize: 10, fill: charts.textMuted }}
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
                name="Published"
                stroke={charts.series[7]}
                strokeWidth={1.5}
                strokeDasharray="4 3"
                dot={false}
              />
              <Line
                type="monotone"
                dataKey="price"
                name={'Scenario ' + (materialScenarioPct > 0 ? '+' : '') + materialScenarioPct + '%'}
                stroke={charts.series[5]}
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
      <p className="muted small">
        What actually prices your BoQ. Every rate is <strong>derived to current</strong> from a named
        published schedule of rates: the BCA Schedule of Rates, May 2022 for Singapore and the CPWD
        Delhi Schedule of Rates 2021 Vol-II for India, each cumulative-adjusted to{' '}
        <strong>Q2 2026</strong> by the factor its own source file states (BCA x1.171, CPWD
        x1.2364 - the cumulative CPI inflation from the schedule&rsquo;s base year to 2026). Each
        row states the quarter it is expressed at, so the app escalates from that quarter and never
        from the index series&rsquo; own base year.{' '}
        {derivedRateCount} of {benchmarkRates.length} rows are derived from the schedules of rates.
        Sections the loaded extracts do not reach -
        the Singapore schedules carry no piling or M&amp;E, and the India extract is DSR{' '}
        <em>Vol-II</em>, so Vol-I earthwork, concrete, reinforcement and formwork are not in it -
        keep a <strong>retained estimate</strong> from a named source instead. The preliminaries
        estimate is retained in both markets at the user&rsquo;s instruction, because neither extract
        contains a general-requirements schedule.
      </p>
      <div className="table-scroll">
        <table className="data-table compact">
          <thead>
            <tr>
              <th>Section</th><th>Unit</th><th className="num">Rate</th>
              <th>Source, derived to current</th><th>Expressed at</th><th>Confidence</th><th>Basis</th>
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
                  <td className="small">
                    {row.base_quarter
                      ? <span className="badge basis-derived">{row.base_quarter}</span>
                      : <span className="badge basis-assumed">base year {row.base_year}</span>}
                  </td>
                  <td>{row.confidence}</td>
                  <td>
                    {row.is_placeholder
                      ? <span className="badge basis-assumed">retained estimate</span>
                      : <span className="badge basis-measured">derived to current</span>}
                    {row.provenance_note
                      ? <div className="small muted" style={{ maxWidth: 320 }}>{row.provenance_note}</div>
                      : null}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <h3>Credible sources for {country.name}</h3>
      <p className="muted small">
        The publications this market's data is drawn from, and the ones named above as gap-closers.
        None of them is fetched at runtime.
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
