import React from 'react'
import { BASIS_LABEL, BASIS_HELP } from '../format.js'

/**
 * Renders warnings[] and assumptions[] prominently above the fold.
 *
 * Hard rule 6 requires apportioned figures to be shown to the user as visible
 * text, never hidden in a tooltip. This panel is deliberately not collapsible
 * when there is anything to say.
 */
export default function AssumptionsPanel(props) {
  const warnings = props.warnings || []
  const assumptions = props.assumptions || []
  const placeholderLines = props.placeholderLineCount || 0
  const adjustmentsActive = props.adjustmentsActive === true

  if (!warnings.length && !assumptions.length && !placeholderLines) {
    return null
  }

  return (
    <section className="assumptions-panel" aria-label="Assumptions and warnings">
      <div className="assumptions-head">
        <h2>Assumptions &amp; warnings</h2>
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
        </div>
      </div>

      {placeholderLines > 0 && (
        <div className="notice notice-critical">
          <strong>Placeholder data in this run.</strong> {placeholderLines} benchmark line(s) are
          flagged <code>is_placeholder: true</code>. The bundled index values and benchmark rates
          are SYNTHETIC and must not be used for a real tender decision. Every placeholder row
          carries a source_url and a <code># TODO: replace with actual ...</code> marker.
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
          <h3>Warnings ({warnings.length})</h3>
          <ul className="warning-list">
            {warnings.map(function (text, index) {
              return <li key={'w' + index}>{text}</li>
            })}
          </ul>
        </div>
      )}

      {assumptions.length > 0 && (
        <div className="assumptions-block">
          <h3>Assumptions ({assumptions.length})</h3>
          <ul className="assumption-list">
            {assumptions.map(function (text, index) {
              return <li key={'a' + index}>{text}</li>
            })}
          </ul>
        </div>
      )}
    </section>
  )
}
