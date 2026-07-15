"""Application-wide Gradio theme and responsive component styles."""

APP_CSS = r"""
.bag-detail-panel {
  margin-top: 6px;
}
.bag-detail-panel .bag-details {
  display: grid;
  gap: 18px;
}
.bag-detail-panel .bag-details-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 14px;
  padding: 12px 16px;
  border: 1px solid var(--border-color-primary);
  border-radius: 12px;
  background: var(--block-background-fill);
  color: var(--body-text-color-subdued);
  font-size: 13px;
}
.bag-detail-panel .toolbar-label {
  color: var(--body-text-color);
  font-weight: 700;
}
.bag-detail-panel .toolbar-divider {
  width: 1px;
  height: 16px;
  background: var(--border-color-primary);
}
.bag-detail-panel .bag-card {
  overflow: hidden;
  border: 1px solid var(--border-color-primary);
  border-left: 5px solid #ef4444;
  border-radius: 14px;
  background: var(--block-background-fill);
  box-shadow: 0 8px 24px rgba(15, 23, 42, 0.07);
}
.bag-detail-panel .bag-card--pass {
  border-left-color: #22c55e;
}
.bag-detail-panel .bag-card__header {
  display: flex;
  justify-content: space-between;
  gap: 20px;
  padding: 18px 20px 15px;
  background: linear-gradient(135deg, rgba(239, 68, 68, 0.08), transparent 62%);
}
.bag-detail-panel .bag-card--pass .bag-card__header {
  background: linear-gradient(135deg, rgba(34, 197, 94, 0.08), transparent 62%);
}
.bag-detail-panel .bag-card__index {
  margin-bottom: 4px;
  color: var(--body-text-color-subdued);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: .09em;
  text-transform: uppercase;
}
.bag-detail-panel .bag-card__title {
  margin: 0;
  color: var(--body-text-color);
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 17px;
  line-height: 1.4;
  overflow-wrap: anywhere;
}
.bag-detail-panel .bag-card__path {
  margin-top: 7px;
  color: var(--body-text-color-subdued);
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
  line-height: 1.5;
  overflow-wrap: anywhere;
}
.bag-detail-panel .verdict {
  flex: 0 0 auto;
  align-self: flex-start;
  padding: 6px 11px;
  border-radius: 999px;
  font-size: 13px;
  font-weight: 800;
  white-space: nowrap;
}
.bag-detail-panel .verdict--fail {
  color: #b91c1c;
  background: #fee2e2;
}
.bag-detail-panel .verdict--pass {
  color: #15803d;
  background: #dcfce7;
}
.bag-detail-panel .bag-metrics {
  display: grid;
  grid-template-columns: repeat(5, minmax(110px, 1fr));
  gap: 10px;
  padding: 0 20px 16px;
}
.bag-detail-panel .metric {
  padding: 10px 12px;
  border: 1px solid var(--border-color-primary);
  border-radius: 10px;
  background: var(--body-background-fill);
}
.bag-detail-panel .metric__label {
  display: block;
  margin-bottom: 3px;
  color: var(--body-text-color-subdued);
  font-size: 11px;
}
.bag-detail-panel .metric__value {
  color: var(--body-text-color);
  font-size: 15px;
  font-weight: 800;
}
.bag-detail-panel .metric--danger .metric__value { color: #dc2626; }
.bag-detail-panel .metric--warning .metric__value { color: #d97706; }
.bag-detail-panel .metric--success .metric__value { color: #16a34a; }
.bag-detail-panel .bag-reason {
  margin: 0 20px 16px;
  padding: 10px 13px;
  border: 1px solid #fecaca;
  border-radius: 10px;
  color: #991b1b;
  background: #fef2f2;
  font-size: 13px;
  line-height: 1.55;
}
.bag-detail-panel .bag-reason strong {
  margin-right: 6px;
}
.bag-detail-panel .topic-table-wrap {
  overflow-x: auto;
  border-top: 1px solid var(--border-color-primary);
}
.bag-detail-panel .topic-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}
.bag-detail-panel .topic-table th {
  padding: 11px 14px;
  border-bottom: 1px solid var(--border-color-primary);
  background: var(--table-odd-background-fill);
  color: var(--body-text-color-subdued);
  font-size: 11px;
  font-weight: 800;
  letter-spacing: .035em;
  text-align: left;
  text-transform: uppercase;
  white-space: nowrap;
}
.bag-detail-panel .topic-table td {
  padding: 10px 14px;
  border-bottom: 1px solid var(--border-color-primary);
  color: var(--body-text-color);
  vertical-align: middle;
}
.bag-detail-panel .topic-table tr:last-child td {
  border-bottom: 0;
}
.bag-detail-panel .topic-table tbody tr:hover {
  background: var(--table-even-background-fill);
}
.bag-detail-panel .topic-table tr.row--danger {
  background: rgba(239, 68, 68, 0.045);
}
.bag-detail-panel .topic-table tr.row--warning {
  background: rgba(245, 158, 11, 0.045);
}
.bag-detail-panel .topic-name {
  min-width: 260px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  overflow-wrap: anywhere;
}
.bag-detail-panel .delay-cell {
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.bag-detail-panel .topic-status {
  display: inline-block;
  min-width: 66px;
  padding: 4px 8px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 750;
  text-align: center;
  white-space: nowrap;
}
.bag-detail-panel .topic-status--danger { color: #b91c1c; background: #fee2e2; }
.bag-detail-panel .topic-status--warning { color: #b45309; background: #fef3c7; }
.bag-detail-panel .topic-status--success { color: #15803d; background: #dcfce7; }
.bag-detail-panel .topic-status--reference { color: #1d4ed8; background: #dbeafe; }
.bag-detail-panel .topic-status--muted { color: #475569; background: #e2e8f0; }
.bag-detail-panel .bag-details-empty {
  padding: 26px;
  border: 1px dashed var(--border-color-primary);
  border-radius: 12px;
  color: var(--body-text-color-subdued);
  text-align: center;
}
@media (max-width: 900px) {
  .bag-detail-panel .bag-metrics { grid-template-columns: repeat(2, minmax(120px, 1fr)); }
  .bag-detail-panel .bag-card__header { flex-direction: column; gap: 12px; }
}

.bag-summary-scroll,
.bag-detail-scroll {
  max-height: min(560px, 70vh);
  overflow-y: auto;
  overscroll-behavior: contain;
  scrollbar-gutter: stable;
  padding-right: 6px;
  border-radius: 12px;
}
.bag-summary-scroll::-webkit-scrollbar,
.bag-detail-scroll::-webkit-scrollbar {
  width: 9px;
}
.bag-summary-scroll::-webkit-scrollbar-thumb,
.bag-detail-scroll::-webkit-scrollbar-thumb {
  border: 2px solid transparent;
  border-radius: 999px;
  background: var(--border-color-primary);
  background-clip: padding-box;
}
.bag-summary-scroll thead {
  position: sticky;
  top: 0;
  z-index: 4;
}
.bag-detail-scroll .bag-details-toolbar {
  position: sticky;
  top: 0;
  z-index: 5;
  box-shadow: 0 5px 16px rgba(15, 23, 42, 0.08);
}
@media (max-width: 700px) {
  .bag-summary-scroll,
  .bag-detail-scroll {
    max-height: 65vh;
  }
}

:root {
  --app-accent: #2563eb;
  --app-accent-strong: #1d4ed8;
  --app-cyan: #06b6d4;
  --app-radius: 16px;
}
body {
  background:
    radial-gradient(circle at 8% 0%, rgba(37, 99, 235, .10), transparent 28rem),
    radial-gradient(circle at 92% 12%, rgba(6, 182, 212, .08), transparent 24rem),
    var(--body-background-fill);
}
.gradio-container {
  max-width: 1480px !important;
  padding: 26px 26px 72px !important;
}
.app-shell {
  gap: 16px !important;
}
.app-hero {
  position: relative;
  overflow: hidden;
  margin-bottom: 6px;
  padding: 30px 34px;
  border: 1px solid rgba(255, 255, 255, .14);
  border-radius: 22px;
  color: #fff;
  background:
    radial-gradient(circle at 92% 12%, rgba(34, 211, 238, .28), transparent 18rem),
    linear-gradient(135deg, #0f172a 0%, #1d4ed8 68%, #0891b2 100%);
  box-shadow: 0 18px 45px rgba(15, 23, 42, .18);
}
.app-hero::after {
  content: "";
  position: absolute;
  right: -70px;
  bottom: -100px;
  width: 260px;
  height: 260px;
  border: 42px solid rgba(255, 255, 255, .08);
  border-radius: 50%;
}
.app-hero__eyebrow {
  position: relative;
  z-index: 1;
  margin-bottom: 9px;
  color: #a5f3fc;
  font-size: 12px;
  font-weight: 800;
  letter-spacing: .14em;
  text-transform: uppercase;
}
.app-hero h1 {
  position: relative;
  z-index: 1;
  margin: 0;
  color: #fff;
  font-size: clamp(26px, 3vw, 40px);
  line-height: 1.18;
  letter-spacing: -.025em;
}
.app-hero p {
  position: relative;
  z-index: 1;
  max-width: 880px;
  margin: 12px 0 18px;
  color: rgba(255, 255, 255, .82);
  font-size: 15px;
  line-height: 1.7;
}
.app-hero__badges {
  position: relative;
  z-index: 1;
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.app-hero__badge {
  padding: 6px 10px;
  border: 1px solid rgba(255, 255, 255, .18);
  border-radius: 999px;
  color: #e0f2fe;
  background: rgba(255, 255, 255, .10);
  font-size: 12px;
  font-weight: 700;
  backdrop-filter: blur(8px);
}
.app-section {
  overflow: hidden;
  border: 1px solid var(--border-color-primary) !important;
  border-radius: var(--app-radius) !important;
  background: var(--block-background-fill) !important;
  box-shadow: 0 8px 28px rgba(15, 23, 42, .055);
}
.app-section > button {
  min-height: 54px;
  padding-inline: 18px !important;
  color: #fff !important;
  background: linear-gradient(110deg, #0f172a 0%, #1d4ed8 72%, #0891b2 100%) !important;
  font-size: 18px !important;
  font-weight: 800 !important;
  letter-spacing: .01em;
}
.app-section > button:hover {
  filter: brightness(1.06);
}
.app-section > button::before {
  display: inline-grid;
  place-items: center;
  width: 34px;
  height: 26px;
  margin-right: 10px;
  border: 1px solid rgba(255, 255, 255, .22);
  border-radius: 8px;
  color: #cffafe;
  background: rgba(255, 255, 255, .11);
  font-size: 12px;
  font-weight: 850;
  letter-spacing: .06em;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, .12);
}
.step-01 > button::before { content: "01"; }
.step-02 > button::before { content: "02"; }
.step-03 > button::before { content: "03"; }
.step-04 > button::before { content: "04"; }
.summary-guide {
  margin: 2px 0 8px;
  padding: 9px 12px;
  border: 1px solid rgba(37, 99, 235, .14);
  border-radius: 9px;
  color: var(--body-text-color-subdued);
  background: rgba(37, 99, 235, .045);
  font-size: 12px;
  line-height: 1.55;
}
.section-lead {
  margin: 0 0 4px;
  padding: 10px 13px;
  border-left: 3px solid var(--app-accent);
  border-radius: 0 9px 9px 0;
  color: var(--body-text-color-subdued);
  background: rgba(37, 99, 235, .055);
  font-size: 13px;
  line-height: 1.6;
}
.status-note {
  min-height: 50px;
  padding: 12px 14px !important;
  border: 1px solid var(--border-color-primary);
  border-radius: 11px;
  background: var(--body-background-fill);
}
.action-row {
  align-items: stretch;
}
.action-row button {
  min-height: 42px;
  border-radius: 10px !important;
  font-weight: 750 !important;
}
button.primary {
  border-color: transparent !important;
  background: linear-gradient(135deg, var(--app-accent), var(--app-accent-strong)) !important;
  box-shadow: 0 7px 18px rgba(37, 99, 235, .20);
}
button.primary:hover {
  transform: translateY(-1px);
  box-shadow: 0 10px 22px rgba(37, 99, 235, .27);
}
.data-panel,
.export-summary {
  border-radius: 12px;
}
.bag-meta {
  min-height: 70px;
  padding: 12px 14px !important;
  border: 1px solid var(--border-color-primary);
  border-radius: 11px;
  background: var(--body-background-fill);
}
.export-summary {
  padding: 2px;
}
.export-summary > div {
  min-height: 116px;
  padding: 14px 16px !important;
  border: 1px solid var(--border-color-primary);
  border-radius: 12px;
  background: linear-gradient(135deg, rgba(37, 99, 235, .055), transparent);
}
.gradio-container label > span {
  font-weight: 700;
}
.gradio-container input,
.gradio-container textarea {
  border-radius: 9px !important;
}
.gradio-container .progress-bar {
  border-radius: 999px !important;
}
.workflow-hint {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 2px 0 8px;
}
.workflow-hint span {
  padding: 5px 9px;
  border-radius: 8px;
  color: var(--body-text-color-subdued);
  background: var(--body-background-fill);
  font-size: 12px;
  font-weight: 650;
}
@media (max-width: 760px) {
  .gradio-container {
    padding: 14px 12px 48px !important;
  }
  .app-hero {
    padding: 24px 20px;
    border-radius: 16px;
  }
  .app-hero p {
    font-size: 14px;
  }
  .app-section > button {
    font-size: 16px !important;
  }
}

.bad-point-cell {
  min-width: 280px;
  max-width: 520px;
}
.bad-points summary {
  cursor: pointer;
  color: #b91c1c;
  font-size: 12px;
  font-weight: 800;
  white-space: nowrap;
}
.bad-points[open] summary {
  margin-bottom: 8px;
}
.bad-point-list {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
  gap: 6px;
  max-height: 180px;
  padding: 7px;
  overflow-y: auto;
  border: 1px solid rgba(239, 68, 68, .14);
  border-radius: 8px;
  background: rgba(239, 68, 68, .035);
}
.bad-point-chip {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  padding: 5px 7px;
  border-radius: 6px;
  color: var(--body-text-color);
  background: var(--block-background-fill);
  font-size: 11px;
  font-variant-numeric: tabular-nums;
}
.bad-point-chip strong { color: #b91c1c; }
.bad-point-chip em {
  color: var(--body-text-color-subdued);
  font-style: normal;
}
.bad-point-note {
  grid-column: 1 / -1;
  color: var(--body-text-color-subdued);
  font-size: 11px;
}
"""
