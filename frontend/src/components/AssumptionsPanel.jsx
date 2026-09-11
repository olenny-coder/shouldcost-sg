import React, { useCallback, useEffect, useState } from 'react'
import { BASIS_LABEL, BASIS_HELP } from '../format.js'
import {
  bridgeFactor, bridgeFromMonths, bridgeFromValue, bridgeKind, bridgeKindLong,
  bridgeReason, bridgeSeries, bridgeToMonths, bridgeToValue,
} from '../bridge.js'

const STORAGE_KEY = 'shouldcost.assumptions-panel.collapsed'

function readStoredCollapsed() {
  try {
    return window.localStorage.getItem(STORAGE_KEY) === '1'
  } catch (error) {
    return false
  }
}

/**
 * Renders warnings[], assumptions[] and the data-quality notices above the fold.
 *
 * The panel is COLLAPSIBLE at the user's request, and remembers the choice, so a
 * returning analyst who already knows what the warnings say is not made to scroll
 * past them on every re-run. Collapsing is never silent: the header keeps a live
 * count of warnings and assumptions, and the amber "index carried forward",
 * "indicative seed data" and "index adjusters active" chips stay visible in the
 * collapsed strip, so an assumed figure can never be mistaken for a measured one.
 *
 * Each list also collapses on its own, so the warnings can be folded away while
 * the assumptions stay open, or the other way round.
 */
export default function AssumptionsPanel(props) {
  const warnings = props.warnings || []
  const assumptions = props.assumptions || []
  const indicativeLines = props.indicativeLineCount || 0
  const adjustmentsActive = props.adjustmentsActive === true
  const indexBridge = props.indexBridge || null
  const bridgeApplied = !!(indexBridge && indexBridge.applied)

  const [collapsed, setCollapsed] = useState(readStoredCollapsed)
  const [warningsOpen, setWarningsOpen] = useState(true)
  const [assumptionsOpen, setAssumptionsOpen] = useState(true)

  useEffect(function () {
    try {
      window.localStorage.setItem(STORAGE_KEY, collapsed ? '1' : '0')
    } catch (error) {
      /* private mode, or storage disabled - the panel still works */
    }
  }, [collapsed])

  const toggle = useCallback(function () { setCollapsed(function (current) { return !current }) }, [])

  if (!warnings.length && !assumptions.length && !indicativeLines) {
    return null
  }

  const chips = []
  if (indicativeLines > 0) {
    chips.push({ key: 'indicative', label: indicativeLines + ' line(s) on an indicative seed rate', tone: 'basis-assumed' })
  }
  if (bridgeApplied) {
    chips.push({
      key: 'bridge',
      label: 'index carried forward with ' + bridgeKind(indexBridge)
        + (indexBridge.bridged_through_month ? ' to ' + indexBridge.bridged_through_month : ''),
      tone: 'basis-assumed',
    })
  }
  if (adjustmentsActive) {
    chips.push({ key: 'adjusters', label: 'index adjusters active', tone: 'basis-assumed' })
  }

  return (
    <section className="assumptions-panel" aria-label="Assumptions and warnings">
      <div className="assumptions-head">
        <h2>
          Assumptions &amp; warnings
          <span className="h2-actions">
            <button
              type="button"
              className="secondary small-btn"
              onClick={toggle}
              aria-expanded={!collapsed}
              aria-controls="assumptions-body"
            >
              <span className="chev">{collapsed ? '\u25B6' : '\u25BC'}</span>
              {collapsed ? 'Expand' : 'Collapse'}
            </button>
          </span>
        </h2>
        <p className="muted">
          Figures in this app are tagged <strong>measured</strong>, <strong>derived</strong> or{" "}
          <strong>assumed</strong>. Nothing apportioned is ever presented as measured.
        </p>
        <div className="basis-legend">
          {['measured', 'derived', 'assumed'].map(function (key) {
            return (
              <span key={key} className={'badge basis-' + key} title={BASIS_HELP[key]}>
                {BASIS_LABEL[key]}
              </span>
            )
          })}
          <span className="muted small">
            {warnings.length} warning(s), {assumptions.length} assumption(s) on this run
          </span>
        </div>
      </div>

      {collapsed ? (
        <div className="assumptions-strip">
          <span className="muted small">
            Collapsed - the full text is one click away.
          </span>
          {chips.map(function (chip) {
            return (
              <span key={chip.key} className={'badge ' + chip.tone} title="Expand the panel to read the full text">
                {chip.label}
              </span>
            )
          })}
          <button type="button" className="linkish small" onClick={toggle}>
            Show {warnings.length} warning(s) and {assumptions.length} assumption(s)
          </button>
        </div>
      ) : (
        <div id="assumptions-body">
          {indicativeLines > 0 && (
            <div className="notice notice-critical">
              <strong>Indicative seed rates in this run.</strong> {indicativeLines} benchmark line(s)
              are priced from the indicative rate library rather than a licensed schedule of rates -
              flagged <code>is_placeholder: true</code>. The published <em>index</em> series are real
              data, but these base rates are not, so they must not be used for a real tender
              decision. Every such row carries a source_url and a{' '}
              <code># TODO: replace with actual ...</code> marker naming the publication it stands in
              for.
            </div>
          )}

          {bridgeApplied && indexBridge && (
            <div className="notice notice-warn">
              <strong>
                Index carried forward with the {bridgeKindLong(bridgeKind(indexBridge))}.
              </strong>{' '}
              The <code>{indexBridge.index_series}</code> series has no published observation for{' '}
              <code>{indexBridge.requested_quarter}</code>; its last observation is{' '}
              <code>{indexBridge.observation_quarter}</code> ({indexBridge.lag_quarters} quarter(s)
              earlier). That observation was carried forward along the published movement in{' '}
              <code>{bridgeSeries(indexBridge)}</code> from{' '}
              {formatMonths(bridgeFromMonths(indexBridge))} ({fmt(bridgeFromValue(indexBridge))}) to{' '}
              {formatMonths(bridgeToMonths(indexBridge))} ({fmt(bridgeToValue(indexBridge))}) - a
              factor of <strong>{fmt(bridgeFactor(indexBridge), 4)}</strong>, giving{' '}
              <strong>{fmt(indexBridge.index_value_used, 4)}</strong> from a published{' '}
              {fmt(indexBridge.index_value_published, 4)}. The result is{' '}
              <strong>derived to show the trend to date</strong>
              {bridgeKind(indexBridge) === 'PPI'
                ? ' - producer prices measure what suppliers charge for the materials a construction rate is made of, which makes this the closest published proxy for the movement being estimated.'
                : ' - consumer prices measure what households pay, not what is bought for a building, so this is the weaker proxy, used only because no producer series spans this window.'}{' '}
              Every bridged line is <code>basis: assumed</code> and the step is shown separately in
              the waterfall.
              {indexBridge.shortfall_months > 0
                ? ' ' + bridgeSeries(indexBridge) + ' is published only to ' + indexBridge.bridged_through_month + ', so the index is derived to that month, not to the quarter end.'
                : ''}
              {indexBridge.cpi_is_placeholder
                ? ' The series used for the bridge is itself an indicative seed series rather than a published observation.'
                : ''}
            </div>
          )}

          {!bridgeApplied && indexBridge && indexBridge.lag_quarters > 0 && (
            <div className="notice notice-info">
              <strong>Index is stale on this run.</strong> The{' '}
              <code>{indexBridge.index_series}</code> observation for{' '}
              <code>{indexBridge.observation_quarter}</code> is {indexBridge.lag_quarters}{' '}
              quarter(s) earlier than {indexBridge.requested_quarter}, and it was held unchanged
              rather than bridged ({indexBridge.mode === 'none'
                ? 'the bridge is switched off for this run'
                : bridgeReason(indexBridge.reason)}).
            </div>
          )}

          {adjustmentsActive ? (
            <div className="notice notice-warn">
              <strong>Index adjusters are active.</strong> Some rates on screen were moved by a manual
              adjustment rather than by a published index. Every affected line is tagged{' '}
              <code>basis: assumed</code> and flagged <em>index adjusted</em>, and each adjustment is
              restated in full below.
            </div>
          ) : null}

          {warnings.length > 0 && (
            <div className="assumptions-block">
              <h3>
                <button
                  type="button"
                  className="block-toggle"
                  onClick={function () { setWarningsOpen(!warningsOpen) }}
                  aria-expanded={warningsOpen}
                >
                  <span className="chev">{warningsOpen ? '\u25BC' : '\u25B6'}</span>
                  Warnings ({warnings.length})
                </button>
              </h3>
              {warningsOpen ? (
                <ul className="warning-list">
                  {warnings.map(function (text, index) {
                    return <li key={'w' + index}>{text}</li>
                  })}
                </ul>
              ) : null}
            </div>
          )}

          {assumptions.length > 0 && (
            <div className="assumptions-block">
              <h3>
                <button
                  type="button"
                  className="block-toggle"
                  onClick={function () { setAssumptionsOpen(!assumptionsOpen) }}
                  aria-expanded={assumptionsOpen}
                >
                  <span className="chev">{assumptionsOpen ? '\u25BC' : '\u25B6'}</span>
                  Assumptions ({assumptions.length})
                </button>
              </h3>
              {assumptionsOpen ? (
                <ul className="assumption-list">
                  {assumptions.map(function (text, index) {
                    return <li key={'a' + index}>{text}</li>
                  })}
                </ul>
              ) : null}
            </div>
          )}
        </div>
      )}
    </section>
  )
}

function fmt(value, digits) {
  if (value === null || value === undefined || value === '') return 'n/a'
  const places = digits === undefined ? 3 : digits
  return Number(value).toFixed(places)
}

function formatMonths(months) {
  if (!months || !months.length) return 'the index quarter'
  if (months.length === 1) return months[0]
  return months[0] + ' to ' + months[months.length - 1]
}


