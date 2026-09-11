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
            'Start from the template so the column names are right: use the download button below, or Download > BoQ template.',
            'Required columns are description, unit, quantity and rate. Common alternative spellings are understood, and an amount column is optional.',
            'Drop the file anywhere in the dashed box, or click it to browse. Your file is parsed in memory on the server and never stored.',
            'Units matter. A line whose unit does not match the benchmark rate unit is set aside rather than compared, because comparing an m2 rate with an m rate is meaningless.',
            'After upload, check the classification result. Lines the classifier could not place are counted as Unclassified and can be fixed in the variance table.',
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
        <button type="button" className="secondary" onClick={function () { props.onDownloadTemplate('csv'); }}>
          Download the {country.name} template (.csv)
        </button>
        <button type="button" className="secondary" onClick={function () { props.onDownloadTemplate('xlsx'); }}>
          Template with instructions (.xlsx)
        </button>
      </div>

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

          <div className="table-scroll">
            <table className="data-table compact">
              <thead>
                <tr><th>Section</th><th className="num">Lines</th></tr>
              </thead>
              <tbody>
                {Object.keys(upload.counts_by_section).map(function (section) {
                  return (
                    <tr key={section}>
                      <td>{section}</td>
                      <td className="num">{upload.counts_by_section[section]}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}
    </section>
  );
}
