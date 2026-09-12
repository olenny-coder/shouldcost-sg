import React from 'react';
import HelpPopout from './HelpPopout.jsx';

/** Downloads: a blank BoQ template, or the benchmark exactly as configured. */
export default function ExportBar(props) {
  const disabled = !props.uploadId || props.busy;
  return (
    <section className="panel">
      <h2>
        Download
        <HelpPopout
          title="What to download, and when"
          items={[
            'The BoQ template is the one download that is not a result: a single XLSX that lists every item in this market\'s schedule of rates, with its instructions on a second sheet. Fill in quantities and your own rates, delete the rows you do not need.',
            'Line items is the working file: every BoQ line with its adjusted benchmark rate, variance and the basis behind it.',
            'Section subtotals is the one-page summary for a commercial review - one row per section.',
            'Waterfall gives the reconciliation steps as data, if you want to rebuild the chart elsewhere.',
            'Summary carries the totals plus every warning and assumption, for the audit file.',
            'The full report is the one to send. It is self-describing: header, totals, sections, waterfall, every line, every adjustment applied, every warning, every assumption and the source list, each row carrying its basis.',
            'Every export reflects the screen exactly, including any index adjusters, region and analyst-supplied rates that are currently active.',
          ]}
          footnote="The report uses the long format block,ref,item,value,basis, which opens cleanly in Excel and can be filtered by block."
        />
      </h2>
      <p className="muted small">
        The template is a ready-to-fill spreadsheet in the vocabulary of the selected market.
        The exports contain the benchmark <strong>exactly as configured</strong>, including any
        index adjusters and the assumptions that go with them.
      </p>

      <div className="row" style={{ marginTop: 10 }}>
        <span className="muted small" style={{ minWidth: 92, fontWeight: 700 }}>BoQ TEMPLATE</span>
        <button type="button" className="secondary" disabled={props.busy}
                onClick={function () { props.onDownloadTemplate('xlsx'); }}>
          Template (.xlsx, schedule of rates + instructions)
        </button>
      </div>

      <div className="row" style={{ marginTop: 10 }}>
        <span className="muted small" style={{ minWidth: 92, fontWeight: 700 }}>BENCHMARK</span>
        <button type="button" className="secondary" disabled={disabled}
                onClick={function () { props.onExport('items', 'csv'); }}>
          Line items (.csv)
        </button>
        <button type="button" className="secondary" disabled={disabled}
                onClick={function () { props.onExport('items', 'xlsx'); }}>
          Line items (.xlsx)
        </button>
        <button type="button" className="secondary" disabled={disabled}
                onClick={function () { props.onExport('sections', 'csv'); }}>
          Section subtotals (.csv)
        </button>
        <button type="button" className="secondary" disabled={disabled}
                onClick={function () { props.onExport('waterfall', 'csv'); }}>
          Waterfall (.csv)
        </button>
        <button type="button" className="secondary" disabled={disabled}
                onClick={function () { props.onExport('summary', 'csv'); }}>
          Summary + assumptions (.csv)
        </button>
      </div>

      <div className="row" style={{ marginTop: 12 }}>
        <span className="muted small" style={{ minWidth: 92, fontWeight: 700 }}>REPORT</span>
        <button type="button" className="primary" disabled={disabled}
                onClick={function () { props.onExport('report', 'csv'); }}>
          Full benchmark report (.csv)
        </button>
        <button type="button" className="secondary" disabled={disabled}
                onClick={function () { props.onExport('report', 'xlsx'); }}>
          Full report (.xlsx)
        </button>
      </div>
      <p className="muted small" style={{ marginTop: 8 }}>
        The report is self-describing: one row per data point across the{' '}
        <code>report</code>, <code>totals</code>, <code>section</code>, <code>waterfall</code>,{' '}
        <code>line</code>, <code>adjustment</code>, <code>warning</code>, <code>assumption</code>{' '}
        and <code>source</code> blocks, each carrying its <strong>basis</strong>. Hand it to
        someone who never opened the app and they can reconstruct the whole analysis.
      </p>

      {props.status ? <p className="muted small" style={{ marginTop: 10 }}>{props.status}</p> : null}
      {!props.uploadId ? (
        <p className="muted small" style={{ marginTop: 10 }}>
          Load a BoQ first - the benchmark exports need one.
        </p>
      ) : null}
    </section>
  );
}
