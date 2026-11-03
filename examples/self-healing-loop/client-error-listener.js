/**
 * Reference Pattern: Browser Client Error & CSP Listener (client-error-listener.js)
 * Captures uncaught runtime exceptions, unhandled Promise rejections, broken static assets,
 * and Content-Security-Policy (CSP) violations in the browser and forwards a PII-scrubbed
 * payload to POST /client-error (which publishes to ErrorTicketsTopic -> GitHub Issue).
 */
(function initSelfHealingErrorReporter() {
  const ENDPOINT = '/client-error';
  const reportedFingerprints = new Set();

  function scrubPii(str) {
    if (!str || typeof str !== 'string') return '';
    return str
      .replace(/[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+/g, '[MASKED_EMAIL]')
      .replace(/\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b/g, '[MASKED_PHONE]');
  }

  function sendErrorReport(kind, message, stack, extra) {
    const cleanMsg = scrubPii(message);
    const key = `${kind}:${cleanMsg}:${window.location.pathname}`;
    if (reportedFingerprints.has(key)) return;
    reportedFingerprints.add(key);

    const payload = JSON.stringify({
      source: 'browser_client',
      kind: kind,
      message: cleanMsg,
      stack: scrubPii(stack || ''),
      path: window.location.pathname,
      userAgent: navigator.userAgent,
      extra: extra || {}
    });

    if (navigator.sendBeacon) {
      navigator.sendBeacon(ENDPOINT, new Blob([payload], { type: 'application/json' }));
    } else {
      fetch(ENDPOINT, { method: 'POST', body: payload, headers: { 'Content-Type': 'application/json' }, keepalive: true }).catch(function() {});
    }
  }

  window.addEventListener('error', function(event) {
    if (event.target && (event.target.tagName === 'IMG' || event.target.tagName === 'SCRIPT' || event.target.tagName === 'LINK')) {
      sendErrorReport('broken_asset', `Failed to load ${event.target.tagName}`, '', {
        src: event.target.src || event.target.href
      });
      return;
    }
    sendErrorReport('window_onerror', event.message || 'Uncaught error', event.error && event.error.stack, {
      filename: event.filename,
      lineno: event.lineno,
      colno: event.colno
    });
  }, true);

  window.addEventListener('unhandledrejection', function(event) {
    const reason = event.reason || {};
    sendErrorReport(
      'unhandled_rejection',
      reason.message || String(reason),
      reason.stack || ''
    );
  });

  document.addEventListener('securitypolicyviolation', function(event) {
    sendErrorReport('csp_violation', `CSP blocked ${event.blockedURI} on ${event.violatedDirective}`, '', {
      blockedURI: event.blockedURI,
      violatedDirective: event.violatedDirective
    });
  });
})();
