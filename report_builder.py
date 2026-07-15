"""
HTML Report Builder — converts a ReviewReport into a rich, self-contained HTML document.
No external dependencies — all CSS is inlined.
"""
from __future__ import annotations

import html
from datetime import datetime

from models import Finding, ReviewReport, RoadmapItem, Scorecard


# ── Helpers ───────────────────────────────────────────────────

def _esc(s: str | None) -> str:
    return html.escape(str(s or ""))


def _sev_class(severity: str) -> str:
    return {
        "Critical": "sev-critical",
        "High": "sev-high",
        "Medium": "sev-medium",
        "Low": "sev-low",
        "Informational": "sev-info",
    }.get(severity, "sev-info")


def _score_class(score: float) -> str:
    if score >= 7:
        return "s-good"
    if score >= 4:
        return "s-mid"
    return "s-low"


def _verdict_class(verdict: str) -> str:
    if "Enterprise Ready" == verdict or "Production Ready" == verdict:
        return "verdict-pass"
    if "Minor" in verdict:
        return "verdict-warn"
    return "verdict-fail"


def _priority_class(priority: str) -> str:
    return {
        "Immediate":   "prio-immediate",
        "Short Term":  "prio-short",
        "Medium Term": "prio-medium",
        "Long Term":   "prio-long",
    }.get(priority, "")


# ── CSS ───────────────────────────────────────────────────────

CSS = """
:root{
  --bg:#ffffff;--surface:#f7f8fa;--border:#e5e7eb;
  --text:#1f2328;--muted:#57606a;--accent:#3b82d4;
  --secondary:#7c5cd8;--radius:6px;
  --danger:#991b1b;--warn:#854d0e;--ok:#166534;--info:#0c4a6e;
}
*{box-sizing:border-box;margin:0;padding:0;}
body{font-family:-apple-system,"Segoe UI",system-ui,sans-serif;font-size:14px;
     line-height:1.65;color:var(--text);background:var(--bg);
     max-width:960px;margin:0 auto;padding:32px 24px 60px;}
h1{font-size:24px;font-weight:700;margin-bottom:4px;}
h2{font-size:16px;font-weight:700;margin:36px 0 10px;color:var(--text);
   border-bottom:1px solid var(--border);padding-bottom:6px;}
h3{font-size:14px;font-weight:700;margin:20px 0 6px;color:var(--secondary);}
p{margin-bottom:10px;}
ul{margin:0 0 10px 18px;}
li{margin-bottom:4px;}
.meta{font-size:12px;color:var(--muted);margin-bottom:24px;}

/* banner */
.banner{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);
        padding:20px 24px;margin-bottom:24px;
        display:grid;grid-template-columns:repeat(3,1fr);gap:12px 24px;}
.banner-item label{font-size:11px;font-weight:700;color:var(--muted);
                   text-transform:uppercase;letter-spacing:.6px;display:block;margin-bottom:3px;}
.banner-item span{font-size:15px;font-weight:700;}
.verdict-box{grid-column:1/-1;border-radius:4px;padding:12px 16px;font-size:14px;font-weight:600;}
.verdict-pass{background:#dcfce7;border:1px solid #86efac;color:#166534;}
.verdict-warn{background:#fef9c3;border:1px solid #fde047;color:#854d0e;}
.verdict-fail{background:#fee2e2;border:1px solid #fca5a5;color:#991b1b;}

/* severity chips */
.sev{display:inline-block;font-size:10px;font-weight:700;padding:2px 8px;
     border-radius:20px;text-transform:uppercase;letter-spacing:.5px;white-space:nowrap;}
.sev-critical{background:#fee2e2;color:#991b1b;border:1px solid #fca5a5;}
.sev-high    {background:#ffedd5;color:#9a3412;border:1px solid #fdba74;}
.sev-medium  {background:#fef9c3;color:#854d0e;border:1px solid #fde047;}
.sev-low     {background:#dcfce7;color:#166534;border:1px solid #86efac;}
.sev-info    {background:#e0f2fe;color:#0c4a6e;border:1px solid #7dd3fc;}

/* score badges */
.score{display:inline-block;font-weight:800;font-size:14px;width:36px;
       text-align:center;border-radius:4px;padding:2px 0;}
.s-good{color:#166534;background:#dcfce7;}
.s-mid {color:#854d0e;background:#fef9c3;}
.s-low {color:#991b1b;background:#fee2e2;}

/* tables */
table{width:100%;border-collapse:collapse;margin-bottom:18px;font-size:13px;}
th{background:var(--surface);font-weight:700;text-align:left;
   padding:8px 10px;border:1px solid var(--border);}
td{padding:8px 10px;border:1px solid var(--border);vertical-align:top;}
tr:nth-child(even) td{background:var(--surface);}

/* findings */
.finding{border:1px solid var(--border);border-radius:var(--radius);
         margin-bottom:12px;overflow:hidden;}
.finding-head{background:var(--surface);padding:9px 14px;
              display:flex;align-items:center;gap:10px;flex-wrap:wrap;
              cursor:pointer;user-select:none;}
.finding-id{font-weight:700;font-size:12px;color:var(--muted);min-width:52px;}
.finding-title{font-weight:600;flex:1;}
.finding-body{padding:12px 16px;border-top:1px solid var(--border);}
.finding-body dl{display:grid;grid-template-columns:140px 1fr;gap:6px 10px;font-size:13px;}
.finding-body dt{font-weight:600;color:var(--muted);}
.finding-body dd{margin:0;}
.finding-body pre{background:#f3f4f6;border:1px solid var(--border);
                  border-radius:4px;padding:10px 12px;overflow-x:auto;
                  font-size:12px;font-family:"Cascadia Code",Consolas,monospace;
                  white-space:pre-wrap;word-break:break-all;margin-top:4px;}

/* priority labels */
.prio{display:inline-block;font-size:10px;font-weight:700;padding:2px 8px;
      border-radius:20px;text-transform:uppercase;letter-spacing:.4px;}
.prio-immediate{background:#fee2e2;color:#991b1b;border:1px solid #fca5a5;}
.prio-short    {background:#ffedd5;color:#9a3412;border:1px solid #fdba74;}
.prio-medium   {background:#fef9c3;color:#854d0e;border:1px solid #fde047;}
.prio-long     {background:#e0f2fe;color:#0c4a6e;border:1px solid #7dd3fc;}

/* callout */
.callout{border-left:3px solid var(--border);background:var(--surface);
         padding:10px 14px;border-radius:0 4px 4px 0;margin-bottom:12px;}
.callout-ok  {border-color:#4ade80;background:#f0fdf4;}
.callout-warn{border-color:#fbbf24;background:#fffbeb;}
.callout-fail{border-color:#f87171;background:#fff5f5;}

/* stat pills */
.pills{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:14px;}
.pill{background:var(--surface);border:1px solid var(--border);border-radius:20px;
      font-size:12px;padding:3px 10px;color:var(--muted);}
.pill strong{color:var(--text);}

/* section intro */
.section-intro{color:var(--muted);font-size:13px;margin-bottom:14px;}
.collapse-hint{font-size:11px;color:var(--muted);margin-left:auto;}

/* filter bar */
.filter-bar{display:flex;gap:8px;margin-bottom:16px;flex-wrap:wrap;}
.filter-btn{background:var(--surface);border:1px solid var(--border);border-radius:20px;
            padding:5px 14px;font-size:12px;font-weight:600;cursor:pointer;
            transition:all .15s;color:var(--muted);}
.filter-btn:hover,.filter-btn.active{background:var(--accent);color:#fff;border-color:var(--accent);}

footer{margin-top:48px;padding-top:16px;border-top:1px solid var(--border);
       text-align:center;font-size:11px;color:var(--muted);}

/* pdf / print button */
.pdf-bar{position:sticky;top:0;z-index:50;background:#fff;
         border-bottom:1px solid var(--border);padding:8px 0 8px;
         display:flex;gap:8px;margin-bottom:24px;}
.pdf-btn{padding:7px 18px;border-radius:6px;font-size:13px;font-weight:700;
         cursor:pointer;border:none;}
.pdf-btn-primary{background:#3b82d4;color:#fff;}
.pdf-btn-primary:hover{opacity:.85;}
.pdf-btn-ghost{background:var(--surface);color:var(--text);border:1px solid var(--border);}
.pdf-btn-ghost:hover{border-color:#3b82d4;color:#3b82d4;}

@media print{
  .pdf-bar{display:none !important;}
  .filter-bar{display:none !important;}
  .finding-body{display:block !important;}
  body{max-width:100%;padding:16px;}
  h2{page-break-before:auto;}
  .finding{page-break-inside:avoid;}
}
"""

# ── JS (collapse + filter) ────────────────────────────────────

JS = """
// Toggle finding body
document.querySelectorAll('.finding-head').forEach(h => {
  h.addEventListener('click', () => {
    const body = h.nextElementSibling;
    if (body) body.style.display = body.style.display === 'none' ? '' : 'none';
  });
});

// Severity filter
function filterFindings(sev) {
  document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
  const btn = document.getElementById('btn-' + sev);
  if (btn) btn.classList.add('active');

  document.querySelectorAll('.finding').forEach(f => {
    const chip = f.querySelector('.sev');
    if (!chip) return;
    f.style.display = (sev === 'all' || chip.textContent.trim().toLowerCase() === sev.toLowerCase()) ? '' : 'none';
  });
}
filterFindings('all');
"""


# ── Section renderers ─────────────────────────────────────────

def _render_overview_pills(report: ReviewReport) -> str:
    items = [
        ("Languages", ", ".join(report.languages) or "—"),
        ("Frameworks", ", ".join(report.frameworks) or "—"),
        ("Files", str(report.file_count)),
        ("Est. LoC", f"{report.estimated_loc:,}"),
        ("Project Type", report.project_type or "—"),
    ]
    pills = "".join(
        f'<div class="pill"><strong>{_esc(k)}:</strong> {_esc(v)}</div>'
        for k, v in items
    )
    return f'<div class="pills">{pills}</div>'


def _render_strengths_weaknesses(report: ReviewReport) -> str:
    def _list(items: list[str]) -> str:
        return "<ul>" + "".join(f"<li>{_esc(i)}</li>" for i in items) + "</ul>" if items else "<p>—</p>"

    return f"""
<div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:4px;">
  <div>
    <h3>✓ Strengths</h3>
    {_list(report.strengths)}
  </div>
  <div>
    <h3>✗ Weaknesses</h3>
    {_list(report.weaknesses)}
  </div>
</div>"""


def _render_finding(f: Finding) -> str:
    def _row(label: str, value: str | None) -> str:
        if not value:
            return ""
        return f"<dt>{_esc(label)}</dt><dd>{_esc(value)}</dd>"

    rec_html = f"<pre>{_esc(f.recommendation)}</pre>" if f.recommendation else ""

    return f"""
<div class="finding" data-sev="{_esc(f.severity)}">
  <div class="finding-head">
    <span class="finding-id">{_esc(f.id)}</span>
    <span class="sev {_sev_class(f.severity)}">{_esc(f.severity)}</span>
    <span class="finding-title">{_esc(f.description[:120])}</span>
    <span class="collapse-hint">▼ details</span>
  </div>
  <div class="finding-body">
    <dl>
      {_row("Category", f.category)}
      {_row("File", f.file_path)}
      {_row("Class", f.class_name)}
      {_row("Method", f.method_name)}
      {_row("Lines", f.line_numbers)}
      {_row("Root Cause", f.root_cause)}
      {_row("Business Impact", f.business_impact)}
      {_row("Technical Impact", f.technical_impact)}
      {_row("Effort", f.effort)}
      {_row("References", f.references)}
    </dl>
    {rec_html}
  </div>
</div>"""


def _render_findings_section(findings: list[Finding]) -> str:
    if not findings:
        return "<p>No findings generated.</p>"

    counts = {s: sum(1 for f in findings if f.severity == s)
              for s in ["Critical", "High", "Medium", "Low", "Informational"]}

    filter_btns = (
        '<button class="filter-btn active" id="btn-all" onclick="filterFindings(\'all\')">All '
        f'({len(findings)})</button>'
    )
    for sev, cls in [("Critical","sev-critical"),("High","sev-high"),
                     ("Medium","sev-medium"),("Low","sev-low"),("Informational","sev-info")]:
        n = counts.get(sev, 0)
        if n:
            filter_btns += (
                f'<button class="filter-btn" id="btn-{sev}" '
                f'onclick="filterFindings(\'{sev}\')">'
                f'<span class="sev {cls}">{sev}</span> {n}</button>'
            )

    cards = "".join(_render_finding(f) for f in findings)
    return f'<div class="filter-bar">{filter_btns}</div>\n{cards}'


def _render_scorecard(sc: Scorecard) -> str:
    rows = [
        ("Architecture", sc.architecture),
        ("Design", sc.design),
        ("Coding Standards", sc.coding_standards),
        ("Maintainability", sc.maintainability),
        ("Readability", sc.readability),
        ("Performance", sc.performance),
        ("Security", sc.security),
        ("Scalability", sc.scalability),
        ("Reliability", sc.reliability),
        ("Testability", sc.testability),
        ("Documentation", sc.documentation),
        ("DevOps Readiness", sc.devops_readiness),
        ("Cloud Readiness", sc.cloud_readiness),
        ("API Design", sc.api_design),
        ("Database Design", sc.database_design),
        ("Automation Practices", sc.automation_practices),
        ("Enterprise Compliance", sc.enterprise_compliance),
        ("Production Readiness", sc.production_readiness),
        ("Enterprise Readiness", sc.enterprise_readiness),
    ]

    def _bar(score: float) -> str:
        pct = int(score * 10)
        colour = "#4ade80" if score >= 7 else "#fbbf24" if score >= 4 else "#f87171"
        return (
            f'<div style="background:#e5e7eb;border-radius:4px;height:8px;width:100%;margin-top:4px;">'
            f'<div style="background:{colour};width:{pct}%;height:8px;border-radius:4px;"></div>'
            f'</div>'
        )

    trs = "".join(
        f"<tr><td>{_esc(label)}</td>"
        f"<td><span class='score {_score_class(score)}'>{score:.1f}</span>"
        f"{_bar(score)}</td></tr>"
        for label, score in rows
    )
    overall = sc.overall()
    return f"""
<table>
  <tr><th>Category</th><th>Score (0–10)</th></tr>
  {trs}
  <tr style="font-weight:700;">
    <td>Overall</td>
    <td><span class="score {_score_class(overall)}" style="font-size:18px;">{overall:.1f}</span></td>
  </tr>
</table>"""


def _render_roadmap(roadmap: list[RoadmapItem]) -> str:
    if not roadmap:
        return "<p>No roadmap items generated.</p>"

    groups: dict[str, list[RoadmapItem]] = {
        "Immediate": [], "Short Term": [], "Medium Term": [], "Long Term": []
    }
    for item in roadmap:
        groups.setdefault(item.priority, []).append(item)

    html_parts = []
    for priority, items in groups.items():
        if not items:
            continue
        rows = "".join(
            f"<tr><td>{_esc(item.title)}</td>"
            f"<td>{_esc(item.effort)}</td>"
            f"<td>{_esc(', '.join(item.finding_ids))}</td>"
            f"<td>{_esc(item.expected_benefit)}</td></tr>"
            for item in items
        )
        html_parts.append(
            f"<h3><span class='prio {_priority_class(priority)}'>{_esc(priority)}</span></h3>"
            f"<table><tr><th>Action</th><th>Effort</th><th>Findings</th><th>Benefit</th></tr>"
            f"{rows}</table>"
        )
    return "".join(html_parts)


def _render_missing_practices(items: list[str]) -> str:
    if not items:
        return "<p>—</p>"
    return "<ul>" + "".join(f"<li>{_esc(i)}</li>" for i in items) + "</ul>"


# ── Main entry point ──────────────────────────────────────────

def build_html_report(report: ReviewReport) -> str:
    """Generate and return a complete self-contained HTML report string."""
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    verdict_cls = _verdict_class(report.final_verdict)

    body = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1.0"/>
<title>Code Review — {_esc(report.repo_name)}</title>
<style>{CSS}</style>
</head>
<body>

<div class="pdf-bar">
  <button class="pdf-btn pdf-btn-primary" onclick="window.print()">⬇ Download as PDF</button>
  <button class="pdf-btn pdf-btn-ghost" onclick="window.close()">✕ Close</button>
</div>

<h1>Enterprise Code Review Report</h1>
<div class="meta">
  Repository: <strong>{_esc(report.repo_name)}</strong> &nbsp;|&nbsp;
  Source: <strong>{_esc(report.repo_source)}</strong> &nbsp;|&nbsp;
  Generated: <strong>{generated_at}</strong>
</div>

<h2>Executive Summary</h2>
<div class="banner">
  <div class="banner-item">
    <label>Overall Score</label>
    <span><span class="score {_score_class(report.overall_score)}" style="font-size:20px;">{report.overall_score:.1f}</span> / 10</span>
  </div>
  <div class="banner-item">
    <label>Confidence</label>
    <span>{report.confidence_pct}%</span>
  </div>
  <div class="banner-item">
    <label>Findings</label>
    <span>{len(report.findings)} issues</span>
  </div>
  <div class="banner-item">
    <label>Production Ready</label>
    <span>{"✓ Yes" if report.production_ready else "✗ No"}</span>
  </div>
  <div class="banner-item">
    <label>Enterprise Ready</label>
    <span>{"✓ Yes" if report.enterprise_ready else "✗ No"}</span>
  </div>
  <div class="banner-item">
    <label>Files / LoC</label>
    <span>{report.file_count} / {report.estimated_loc:,}</span>
  </div>
  <div class="verdict-box {verdict_cls}">
    ⚖ VERDICT: {_esc(report.final_verdict)}<br>
    <span style="font-weight:400;font-size:13px;">{_esc(report.verdict_justification)}</span>
  </div>
</div>

<h2>Repository Overview</h2>
{_render_overview_pills(report)}
{_render_strengths_weaknesses(report)}

<h2>Architecture Review</h2>
<div class="section-intro">{_esc(report.architecture_review)}</div>

<h2>Detailed Findings</h2>
{_render_findings_section(report.findings)}

<h2>Technical Debt Assessment</h2>
<div class="section-intro">{_esc(report.technical_debt)}</div>

<h2>Missing Best Practices</h2>
{_render_missing_practices(report.missing_practices)}

<h2>Refactoring Roadmap</h2>
{_render_roadmap(report.roadmap)}

<h2>Final Scorecard</h2>
{_render_scorecard(report.scorecard)}

<footer>Made with IBM Bob &mdash; Enterprise Code Review Automation</footer>

<script>{JS}</script>
</body>
</html>"""

    return body
