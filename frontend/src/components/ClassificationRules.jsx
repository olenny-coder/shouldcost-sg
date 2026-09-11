import React, { useCallback, useEffect, useState } from 'react'

const STORAGE_KEY = 'shouldcost.classification-rules.collapsed'

function readStoredCollapsed() {
  try {
    return window.localStorage.getItem(STORAGE_KEY) === '1'
  } catch (error) {
    return false
  }
}

/**
 * The effective classifier rule table for the selected market.
 *
 * Collapsible, and the choice is remembered: the table is long, it is reference
 * material rather than a result, and a returning analyst does not need to scroll
 * past it on every re-run. The header keeps the standard and the rule count
 * visible when collapsed, so it is always clear which vocabulary is in force and
 * that an automated, first-match-wins classifier is what placed every line.
 */
export default function ClassificationRules(props) {
  const rules = props.rules || null
  const [collapsed, setCollapsed] = useState(readStoredCollapsed)

  useEffect(function () {
    try {
      window.localStorage.setItem(STORAGE_KEY, collapsed ? '1' : '0')
    } catch (error) {
      /* private mode, or storage disabled - the section still works */
    }
  }, [collapsed])

  const toggle = useCallback(function () {
    setCollapsed(function (current) { return !current })
  }, [])

  if (!rules) return null

  const count = (rules.rules || []).length

  return (
    <section className="panel">
      <h2>
        Classification rules - {rules.classification_standard}
        <span className="h2-actions">
          <button
            type="button"
            className="secondary small-btn"
            onClick={toggle}
            aria-expanded={!collapsed}
            aria-controls="classification-rules-body"
          >
            <span className="chev">{collapsed ? '\u25B6' : '\u25BC'}</span>
            {collapsed ? 'Expand' : 'Collapse'}
          </button>
        </span>
      </h2>
      <p className="muted small">
        {count} rule(s), applied top to bottom, first match wins. A line that matches none becomes{' '}
        <code>{rules.unclassified_label}</code> and is not benchmarked until it is reclassified.
      </p>

      {collapsed ? (
        <div className="assumptions-strip">
          <span className="muted small">Rule table collapsed.</span>
          <span className="badge basis-derived">{count} rule(s)</span>
          <span className="badge basis-assumed">automated classification - review it</span>
          <button type="button" className="linkish small" onClick={toggle}>
            Show the {rules.classification_standard} rule table
          </button>
        </div>
      ) : (
        <div id="classification-rules-body">
          <p className="muted small">{rules.measurement_note}</p>
          <p className="muted small">{rules.note}</p>
          <div className="table-scroll">
            <table className="data-table compact">
              <thead><tr><th>Section</th><th>Rule</th></tr></thead>
              <tbody>
                {(rules.rules || []).map(function (rule) {
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
        </div>
      )}
    </section>
  )
}
