/**
 * Single place where the frontend learns the backend URL.
 *
 * The backend URL is NEVER hardcoded in a component. It comes from the
 * VITE_API_BASE_URL environment variable. The localhost value below is a
 * documented LOCAL-DEVELOPMENT FALLBACK only, used when the variable is unset.
 *
 * Vite inlines this at build time, so changing VITE_API_BASE_URL on Vercel
 * requires a REDEPLOY - it is not read at runtime.
 */
import axios from 'axios';

const LOCAL_DEV_FALLBACK = 'http://localhost:8000';

const configured = (import.meta.env.VITE_API_BASE_URL || '').trim();

export const API_BASE_URL = (configured || LOCAL_DEV_FALLBACK).replace(/\/+$/, '');
export const USING_LOCAL_FALLBACK = configured === '';

const http = axios.create({
  baseURL: API_BASE_URL,
  timeout: 120000,
  headers: { Accept: 'application/json' },
});

function describeError(error) {
  if (error.response) {
    const detail = error.response.data && error.response.data.detail;
    if (Array.isArray(detail)) {
      return 'HTTP ' + error.response.status + ': ' + detail.map(function (d) { return d.msg; }).join('; ');
    }
    return 'HTTP ' + error.response.status + ': ' + (detail || JSON.stringify(error.response.data));
  }
  if (error.request) {
    return 'No response from ' + API_BASE_URL + '. Is the backend running, and is this origin allowed by CORS?';
  }
  return error.message;
}

async function unwrap(promise) {
  try {
    const response = await promise;
    return response.data;
  } catch (error) {
    throw new Error(describeError(error));
  }
}

/** Trigger a browser download from a blob response, honouring Content-Disposition. */
async function saveBlob(promise, fallbackName) {
  let response;
  try {
    response = await promise;
  } catch (error) {
    throw new Error(describeError(error));
  }
  const disposition = (response.headers && response.headers['content-disposition']) || '';
  const match = /filename="?([^";]+)"?/.exec(disposition);
  const filename = match ? match[1] : fallbackName;
  const url = window.URL.createObjectURL(response.data);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  document.body.removeChild(anchor);
  window.URL.revokeObjectURL(url);
  return filename;
}

export const api = {
  baseUrl: API_BASE_URL,
  health: function () { return unwrap(http.get('/api/healthz')); },
  config: function () { return unwrap(http.get('/api/config')); },
  listCountries: function () { return unwrap(http.get('/api/countries')); },
  listUploads: function (country) {
    return unwrap(http.get('/api/boq', { params: { country: country } }));
  },
  listTpi: function (params) { return unwrap(http.get('/api/indices/tpi', { params: params })); },
  listCpi: function (params) { return unwrap(http.get('/api/indices/cpi', { params: params })); },
  indexFreshness: function (params) { return unwrap(http.get('/api/indices/freshness', { params: params })); },
  listMaterials: function (params) { return unwrap(http.get('/api/indices/materials', { params: params })); },
  listBenchmarkRates: function (params) { return unwrap(http.get('/api/indices/benchmark-rates', { params: params })); },
  listRegions: function (params) { return unwrap(http.get('/api/indices/regions', { params: params })); },
  listClassifierRules: function (params) { return unwrap(http.get('/api/indices/classifier-rules', { params: params })); },
  uploadBoQ: function (file, country) {
    const form = new FormData();
    form.append('file', file);
    return unwrap(http.post('/api/boq/upload', form, {
      params: { country: country },
      headers: { 'Content-Type': 'multipart/form-data' },
    }));
  },
  getUpload: function (uploadId) { return unwrap(http.get('/api/boq/' + uploadId)); },
  patchItem: function (itemId, section) {
    return unwrap(http.patch('/api/boq/item/' + itemId, { smm2_section: section }));
  },
  benchmark: function (uploadId, payload) {
    return unwrap(http.post('/api/boq/' + uploadId + '/benchmark', payload));
  },
  sensitivity: function (uploadId, payload) {
    return unwrap(http.post('/api/boq/' + uploadId + '/sensitivity', payload));
  },
  downloadTemplate: function (country, format) {
    return saveBlob(
      http.get('/api/boq/template', { params: { country: country, format: format }, responseType: 'blob' }),
      'shouldcost-boq-template.' + format,
    );
  },
  exportBenchmark: function (uploadId, payload, level, format) {
    return saveBlob(
      http.post('/api/boq/' + uploadId + '/export', payload, {
        params: { level: level, format: format },
        responseType: 'blob',
      }),
      'shouldcost-' + level + '.' + format,
    );
  },
};
