import React, { useRef, useState } from 'react';
import { money } from '../format.js';
import HelpPopout from './HelpPopout.jsx';

/** Drag-and-drop BoQ upload, template download and recent-upload picker. */
export default function UploadBoQ(props) {
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const inputRef = useRef(null);

  async function handleFile(file) {
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      await props.onUpload(file);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  function onDrop(event) {
    event.preventDefault();
    setDragging(false);
    handleFile(event.dataTransfer.files && event.dataTransfer.files[0]);
  }

  const upload = props.upload;
  const country = props.country || { name: 'Singapore', currency: 'SGD', measurement_standard: 'SMM2' };
  const currency = country.currency;

  return (
    <section className="panel">
      <h2>
        1. Upload a Bill of Quantities
        <HelpPopout
          title="Uploading a BoQ"
          items={[
            'Start from the template so the column names and the item vocabulary are right: one download button below, with its instructions on a second sheet.',
            'The template lists the whole schedule of rates for this market - hundreds of items - so you fill in quantities against the real wording instead of retyping descriptions. Delete the rows you do not need.',
            'Required columns are description, unit, quantity and rate. Common alternative spellings are understood, and an amount column is optional.',
            'Drop the file anywhere in the dashed box, or click it to browse. Your file is parsed in memory on the server and never stored.',
            'Units matter. A line whose unit does not match the benchmark rate unit is set aside rather than compared, because comparing an m2 rate with an m rate is meaningless.',
            'After upload, check the sections summary: it shows, per section, how many lines came from the schedule, how many the rate library can price, and how many schedule items exist for that section.',
            'Prefer a seeded sample to see the whole app working? Use the recent-uploads buttons.',
          ]}
          footnote="Accepted formats are CSV and XLSX. Legacy .xls must be re-saved as .xlsx or .csv."
        />
      </h2>
      <p className="muted">
        You are working in <strong>{country.name}</strong> - {country.measurement_standard}, amounts
        in <strong>{currency}</strong>. CSV or XLSX with headers{' '}
        <code>description, unit, quantity, rate</code>. Column names are matched leniently. The file
        is parsed in memory on the backend and never written to disk. PDF extraction is not enabled
        in this build - see README "Environment".
      </p>

      <div className="row" style={{ marginTop: 8 }}>
        <button type="button" className="secondary" onClick={function () { props.onDownloadTemplate('xlsx'); }}>
          Download the {country.name} BoQ template (.xlsx, 2 sheets: the schedule of rates + instructions)
        </button>
      </div>
      <p className="muted small" style={{ marginTop: 6 }}>
        One template per market, and the instructions are built into it. The BoQ sheet lists every
        item in the {country.name} schedule of rates, with a <code>section</code> column so you can
        filter to the rows the rate library can price, and a <code>sor_code</code> so a line can be
        traced back to the schedule. Fill in quantities and your own rates.
      </p>
      {props.templateStatus ? (
        <p className="muted small" style={{ marginTop: 4 }}>{props.templateStatus}</p>
      ) : null}

      <div
        className={dragging ? 'dropzone dragging' : 'dropzone'}
        onDragOver={function (e) { e.preventDefault(); setDragging(true); }}
        onDragLeave={function () { setDragging(false); }}
        onDrop={onDrop}
        onClick={function () { if (inputRef.current) inputRef.current.click(); }}
        onKeyDown={function (e) { if (e.key === 'Enter' && inputRef.current) inputRef.current.click(); }}
        role="button"
        tabIndex={0}
      >
        <strong>{busy ? 'Uploading and classifying...' : 'Drop a BoQ file here, or tap to browse'}</strong>
        <span className="muted small">No file is stored on the server; Render disks are ephemeral.</span>
        <input
          ref={inputRef}
          type="file"
          accept=".csv,.xlsx,.xlsm"
          onChange={function (e) { handleFile(e.target.files && e.target.files[0]); e.target.value = ''; }}
        />
      </div>

      {error ? <div className="notice notice-critical">{error}</div> : null}

      {props.uploads && props.uploads.length ? (
        <>
          <h3>Recent uploads</h3>
          <div className="row">
            {props.uploads.slice(0, 6).map(function (row) {
              return (
                <button
                  key={row.upload_id}
                  type="button"
                  className="secondary"
                  onClick={function () { props.onLoadUpload(row.upload_id); }}
                  title={row.filename}
                >
                  {(row.is_seeded_sample ? 'Sample: ' : '') + row.filename.length > 26
                    ? (row.is_seeded_sample ? 'Sample: ' : '') + row.filename.slice(0, 26) + '...'
                    : (row.is_seeded_sample ? 'Sample: ' : '') + row.filename}
                  {' (' + row.country + ', ' + row.row_count + ' lines)'}
                </button>
              );
            })}
          </div>
        </>
      ) : null}

      {upload ? (
        <div className="upload-result" style={{ marginTop: 14 }}>
          <h3>Classification result</h3>
          <dl className="stat-grid tile-grid">
            <div className="tile">
              <div className="tile-label">File</div>
              <div className="tile-value" style={{ fontSize: 14 }}>{upload.filename}</div>
              <div className="tile-sub">upload #{upload.upload_id} - {upload.country}</div>
            </div>
            <div className="tile">
              <div className="tile-label">Rows</div>
              <div className="tile-value">{upload.row_count}</div>
              <div className="tile-sub">{upload.currency}</div>
            </div>
            <div className="tile tile-good">
              <div className="tile-label">Classified</div>
              <div className="tile-value">{upload.classified_count}</div>
              <div className="tile-sub">matched a section rule</div>
            </div>
            <div className={upload.unclassified_count ? 'tile tile-bad' : 'tile'}>
              <div className="tile-label">Unclassified</div>
              <div className="tile-value">{upload.unclassified_count}</div>
              <div className="tile-sub">reclassify in the variance table</div>
            </div>
          </dl>

          <h3>Sections summary</h3>
          <p className="muted small">
            {upload.sor_catalogue && upload.sor_catalogue.sor_items ? (
              <>
                The {country.name} schedule of rates holds{' '}
                <strong>{upload.sor_catalogue.sor_items.toLocaleString()}</strong> items;{' '}
                <strong>{(upload.sor_catalogue.sor_items - upload.sor_catalogue.outside_sections).toLocaleString()}</strong>{' '}
                fall in a section the rate library prices and{' '}
                <strong>{upload.sor_catalogue.outside_sections.toLocaleString()}</strong> are outside
                the ten sections (painting, glazing, metalwork, roofing, joinery, finishes,
                demolition, repairs) and need a manual rate. This table shows how your lines map onto
                it. {upload.sor_catalogue.sources && upload.sor_catalogue.sources.length
                  ? 'Schedule: ' + upload.sor_catalogue.sources.join('; ') + '.'
                  : ''}
              </>
            ) : (
              <>How your lines map onto the market's schedule of rates, section by section.</>
            )}
          </p>
          <div className="table-scroll">
            <table className="data-table compact">
              <thead>
                <tr>
                  <th>Section</th>
                  <th className="num">Lines</th>
                  <th className="num" title="Lines carrying a schedule code, i.e. taken from the template">
                    From template
                  </th>
                  <th className="num" title="Lines whose wording matches a schedule item exactly">
                    Schedule wording
                  </th>
                  <th className="num" title="Schedule items this market holds for the section">
                    Schedule items
                  </th>
                  <th>Rate library</th>
                </tr>
              </thead>
              <tbody>
                {(upload.sections_summary || []).map(function (row) {
                  return (
                    <tr key={row.smm2_section}>
                      <td>
                        <span className={row.smm2_section === 'Unclassified' ? 'muted' : 'section-pill'}>
                          {row.smm2_section}
                        </span>
                      </td>
                      <td className="num">{row.lines}</td>
                      <td className="num">{row.from_sor_template || 0}</td>
                      <td className="num">{row.matched_sor_description || 0}</td>
                      <td className="num">{row.sor_items_available || 0}</td>
                      <td>
                        {row.benchmark_rate_available
                          ? <span className="badge basis-measured">priced</span>
                          : <span className="badge basis-assumed">manual rate needed</span>}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <p className="muted small">
            "Schedule items" is how many items the market's schedule holds for that section, so it is
            the ceiling on how much of the section the template could cover. A section the library
            cannot price can only be benchmarked against a rate you supply - the Coverage panel on
            the benchmark views tracks exactly those lines.
          </p>
        </div>
      ) : null}
    </section>
  );
}
