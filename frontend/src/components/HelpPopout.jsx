import React, { useEffect, useRef, useState } from 'react';

/**
 * A pop-out instruction box for a section of the app.
 *
 * Deliberately a pop-out rather than a tooltip: the text is long enough to need
 * reading, and it must remain on screen while the user works.
 */
export default function HelpPopout(props) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef(null);

  useEffect(function () {
    if (!open) return undefined;
    function onDocumentClick(event) {
      if (wrapRef.current && !wrapRef.current.contains(event.target)) setOpen(false);
    }
    function onKey(event) { if (event.key === 'Escape') setOpen(false); }
    document.addEventListener('mousedown', onDocumentClick);
    document.addEventListener('keydown', onKey);
    return function () {
      document.removeEventListener('mousedown', onDocumentClick);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  return (
    <span className="help-wrap" ref={wrapRef}>
      <button
        type="button"
        className={open ? 'help-btn active' : 'help-btn'}
        onClick={function () { setOpen(!open); }}
        aria-expanded={open}
        title={'How to use: ' + props.title}
      >
        ? How to use this
      </button>
      {open ? (
        <div className="help-popout" role="dialog" aria-label={'Instructions: ' + props.title}>
          <div className="help-head">
            <strong>{props.title}</strong>
            <button type="button" className="help-close" onClick={function () { setOpen(false); }} aria-label="Close">
              &#215;
            </button>
          </div>
          <ol className="help-list">
            {(props.items || []).map(function (text, index) {
              return <li key={index}>{text}</li>;
            })}
          </ol>
          {props.footnote ? <p className="help-footnote">{props.footnote}</p> : null}
        </div>
      ) : null}
    </span>
  );
}
